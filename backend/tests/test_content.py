"""Content engine: planning, generation pipeline, QA, uniqueness, modes, actions, catch-up,
publishing hand-off, API permissions and tenant isolation. LLM, media, knowledge and publishing
are faked; the database is real (RLS enforced)."""

import sys
import types
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

import app.modules.content  # noqa: F401  (registers job handlers)
import app.services as services_pkg
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import (
    AuditLog,
    ContentPillar,
    Job,
    MediaAsset,
    Notification,
    Organization,
    Post,
    PostActivity,
    PostMetric,
    Role,
    Schedule,
    SocialAccount,
)
from app.services import content as svc
from app.services import content_actions as actions
from app.services import scheduler
from app.services.jobs import HANDLERS, JobContext, PermanentError, run_inline
from app.services.llm import router as llm_router
from app.services.tenancy import provision_organization
from tests.conftest import auth, login

IST = ZoneInfo("Asia/Kolkata")
PW = "TestPassw0rd!23"


# ------------------------------------------------------------------ fakes
class FakeLLM:
    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []
        self.writes: list[dict] = []  # consumed in order; afterwards unique text per call
        self.score = 8.0
        self.n = 0

    async def complete_json(self, session, task, prompt, *, system=None, temperature=0.5, max_tokens=2500):
        self.calls.append((task, prompt, system))
        self.n += 1
        if task == "plan":
            return {"topic": f"Fresh topic {uuid.uuid4().hex[:8]}", "angle": "A practical angle"}
        if task == "write":
            if self.writes:
                return self.writes.pop(0)
            words = " ".join(uuid.uuid4().hex[:6] for _ in range(30))
            return {"text": f"Unique post {words}.", "hashtags": ["#HRTech", "AI"], "image_brief": "A calm office",
                    "alt_text": "People at work"}
        if task == "qa":
            return {"score": self.score, "issues": []}
        return {}

    def count(self, task: str) -> int:
        return sum(1 for c in self.calls if c[0] == task)


def _module(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def install(monkeypatch, name: str, mod: types.ModuleType) -> None:
    monkeypatch.setitem(sys.modules, f"app.services.{name}", mod)
    monkeypatch.setattr(services_pkg, name, mod, raising=False)


class PublishError(Exception):
    def __init__(self, msg, retryable=False, reconnect=False):
        super().__init__(msg)
        self.retryable, self.reconnect = retryable, reconnect


@pytest.fixture
def fake(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(llm_router, "complete_json", llm.complete_json)

    async def no_embed(session, texts):
        return None, None

    monkeypatch.setattr(llm_router, "embed_texts", no_embed)

    async def company_context(session, max_chars=3500):
        return "HRP AI LLP builds HRMS and AI Studio. Public clients: Acme Corp."

    async def retrieve(session, query, k=6):
        return [{"text": "HRP HRMS automates payroll.", "url": "https://hrpai.in", "title": "HRMS"}]

    async def next_opportunity(session, pillar_id):
        return None

    install(monkeypatch, "knowledge", _module("app.services.knowledge", company_context=company_context,
                                              retrieve=retrieve, next_opportunity=next_opportunity))

    async def generate_for_post(session, post, brand):
        a = MediaAsset(organization_id=post.organization_id, kind="image", mime="image/png", path="t/x.png",
                       sha256=uuid.uuid4().hex * 2, source="template:test", width=1200, height=1200)
        session.add(a)
        await session.flush()
        return [a]

    def asset_out(a):
        return {"id": str(a.id), "kind": a.kind, "mime": a.mime, "width": a.width, "height": a.height,
                "url": f"/api/v1/media/file/{a.id}"}

    install(monkeypatch, "media", _module("app.services.media", generate_for_post=generate_for_post, asset_out=asset_out))
    llm.publish_calls = []

    async def publish(session, post, account):
        llm.publish_calls.append(post.id)
        if llm.publish_error:
            raise llm.publish_error
        return "ext-1", "https://linkedin.com/feed/update/ext-1"

    async def fetch_metrics(session, post, account):
        return {"impressions": 120, "likes": "7", "comments": None}

    llm.publish_error = None
    install(monkeypatch, "publishing", _module("app.services.publishing", publish=publish, fetch_metrics=fetch_metrics,
                                               PublishError=PublishError))
    return llm


# ------------------------------------------------------------------ data helpers
async def new_org(client, tz: str = "Asia/Kolkata") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@content-test.in"
    async with platform_session() as s:
        org, user, _ = await provision_organization(s, name=f"Org {uuid.uuid4().hex[:6]}", admin_email=email,
                                                    admin_name="Owner", admin_password=PW, timezone=tz)
        await s.commit()
        oid, uid = org.id, user.id
    tokens = await login(client, email, PW)
    return {"id": oid, "user_id": uid, "email": email, "tokens": tokens, "h": auth(tokens)}


async def add(org_id, *objs):
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        for o in objs:
            s.add(o)
        await s.commit()
    return objs[0] if len(objs) == 1 else objs


async def mk_account(org_id, platform="linkedin_org", status="connected", name="HRP page") -> SocialAccount:
    return await add(org_id, SocialAccount(id=uuid.uuid4(), organization_id=org_id, platform=platform, name=name,
                                           status=status, settings={}))


async def mk_schedule(org_id, account_id, **kw) -> Schedule:
    base = dict(id=uuid.uuid4(), organization_id=org_id, social_account_id=account_id, name="Weekdays",
                days_of_week=[0, 1, 2, 3, 4, 5, 6], times=["10:00", "17:30"], post_types={"text": 1},
                pillar_ids=[], mode="approve_first", lead_hours=48, catch_up_grace_hours=3, approval_window_hours=4)
    base.update(kw)
    return await add(org_id, Schedule(**base))


async def mk_post(org_id, account, **kw) -> Post:
    base = dict(id=uuid.uuid4(), organization_id=org_id, social_account_id=account.id, platform=account.platform,
                status="planned", mode="approve_first", post_type="text", hashtags=[], media_ids=[], qa={}, meta={},
                scheduled_at=datetime.now(UTC) + timedelta(hours=10))
    base.update(kw)
    return await add(org_id, Post(**base))


async def get_post(org_id, pid) -> Post:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return await s.get(Post, pid)


async def scalar(org_id, q):
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return await s.scalar(q)


async def generate(org_id, pid):
    return await run_inline("post.generate", org_id, {"post_id": str(pid)})


# ================================================================== slot planning
async def test_slot_times_respect_timezone_days_and_blackouts():
    sched = Schedule(days_of_week=[0, 2], times=["09:30", "18:00", "bad", "25:00"])
    start = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)  # Mon 19 Oct 01:30 IST
    end = start + timedelta(days=4)
    slots = scheduler.slot_times(sched, IST, start, end, {"2026-10-21"})
    assert slots == [datetime(2026, 10, 19, 4, 0, tzinfo=UTC), datetime(2026, 10, 19, 12, 30, tzinfo=UTC)]
    # Without the blackout, Wednesday's slots appear; local times are what was configured.
    slots = scheduler.slot_times(sched, IST, start, end)
    assert [s.astimezone(IST).strftime("%a %H:%M") for s in slots] == ["Mon 09:30", "Mon 18:00", "Wed 09:30", "Wed 18:00"]


async def test_planning_is_idempotent_and_skips_disconnected_accounts(client, fake):
    o = await new_org(client)
    ok = await mk_account(o["id"])
    expired = await mk_account(o["id"], status="expired", name="Expired")
    manual = await mk_account(o["id"], platform="manual", status="manual", name="Hand")
    await mk_schedule(o["id"], ok.id)
    await mk_schedule(o["id"], expired.id)
    await mk_schedule(o["id"], manual.id, times=["12:00"], lead_hours=24)
    await add(o["id"], ContentPillar(organization_id=o["id"], name="P", weight=10))
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        await svc.get_brand(s, o["id"])
        brand = await s.scalar(select(svc.BrandProfile))
        brand.blackout_dates = ["2026-10-20"]
        now = datetime(2026, 10, 18, 22, 0, tzinfo=UTC)  # Mon 19 Oct 03:30 IST
        first = await scheduler.plan_all(s, org, now)
        await s.commit()
        second = await scheduler.plan_all(s, org, now)
        await s.commit()
    assert second == []
    # 48 h ahead: Mon 10:00 + 17:30, Tue blacked out, Wed 10:00 (03:30 IST + 48h = Wed 03:30 → none). Manual: Mon 12:00.
    local = sorted((p.platform, p.scheduled_at.astimezone(IST).strftime("%a %H:%M")) for p in first)
    assert local == [("linkedin_org", "Mon 10:00"), ("linkedin_org", "Mon 17:30"), ("manual", "Mon 12:00")]
    jobs = await scalar(o["id"], select(func.count()).select_from(Job).where(
        Job.kind == "post.generate", Job.organization_id == o["id"]))
    assert jobs == 3
    assert all(p.status == "planned" and p.mode == "approve_first" for p in first)


async def test_pillar_weighting_by_deficit(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    big = ContentPillar(id=uuid.uuid4(), organization_id=o["id"], name="Big", weight=70)
    small = ContentPillar(id=uuid.uuid4(), organization_id=o["id"], name="Small", weight=30)
    off = ContentPillar(id=uuid.uuid4(), organization_id=o["id"], name="Off", weight=100, active=False)
    await add(o["id"], big, small, off)
    for _ in range(3):
        await mk_post(o["id"], acc, pillar_id=big.id, status="published")
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        assert (await svc.choose_pillar(s, acc.id, [])).name == "Small"
        assert (await svc.choose_pillar(s, acc.id, [str(big.id)])).name == "Big"  # schedule restricts
    await mk_post(o["id"], acc, pillar_id=small.id, status="published")
    await mk_post(o["id"], acc, pillar_id=small.id, status="published")
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        # big 3/5 vs target 70%·6=4.2 → deficit 1.2; small 2 vs 1.8 → -0.2
        assert (await svc.choose_pillar(s, acc.id, [])).name == "Big"
        # post types: unsupported ones are dropped, the mix follows weights
        assert await svc.choose_post_type(s, "instagram", {"text": 5, "image": 1, "document": 9}, acc.id) == "image"
        assert await svc.choose_post_type(s, "linkedin_org", {"image": 0}, acc.id) == "text"


# ================================================================== pipeline
async def test_qa_failure_triggers_rewrite_and_full_auto_schedules(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        b = await svc.get_brand(s, o["id"])
        b.banned_words = ["synergy"]
        b.emoji_policy = "none"
        await s.commit()
    fake.writes = [
        {"text": "We unlock synergy with AI [client name] 🚀", "hashtags": ["a", "b", "c", "d", "e", "f", "g"]},
        {"text": "HRP HRMS automates payroll so HR teams focus on people.", "hashtags": ["HRTech"],
         "first_comment": "Learn more at hrpai.in"},
    ]
    p = await mk_post(o["id"], acc, mode="full_auto")
    res = await generate(o["id"], p.id)
    assert res["status"] == "scheduled"
    post = await get_post(o["id"], p.id)
    assert fake.count("write") == 2 and fake.count("plan") == 1 and fake.count("qa") == 1
    assert post.text.startswith("HRP HRMS") and post.hashtags == ["HRTech"]
    assert post.qa["passed"] is True and post.qa["rewritten"] is True and post.qa["score"] == 8.0
    assert post.topic.startswith("Fresh topic") and post.first_comment == "Learn more at hrpai.in"
    # the rewrite prompt named the problems
    rewrite_prompt = [c for c in fake.calls if c[0] == "write"][1][1]
    assert "banned words" in rewrite_prompt and "placeholder" in rewrite_prompt
    system = [c for c in fake.calls if c[0] == "write"][0][2]
    assert "synergy" in system and "Acme Corp" in system and "Do not use any emojis" in system
    acts = await scalar(o["id"], select(func.count()).select_from(PostActivity).where(PostActivity.post_id == p.id))
    assert acts == 1


async def test_deterministic_checks_and_fixes():
    brand = svc.BrandProfile(banned_words=["guarantee"], emoji_policy="light", disclaimer="T&Cs apply.")
    d = {"text": "Hi 😀😀😀😀😀 there", "hashtags": [f"t{i}" for i in range(9)], "title": None}
    fixed = svc.deterministic_fixes(d, "bluesky", brand, link_url="https://hrpai.in", post_type="text")
    assert len(fixed["hashtags"]) == 2 and svc.count_emojis(fixed["text"]) == 3
    assert "https://hrpai.in" in fixed["text"] and fixed["text"].endswith("T&Cs apply.")
    checks = {c["name"]: c["ok"] for c in svc.run_checks(fixed, "bluesky", brand, link_url="https://hrpai.in")}
    assert all(checks.values()), checks
    long = {"text": ("A sentence that goes on. " * 40).strip(), "hashtags": ["one", "two"]}
    trimmed = svc.deterministic_fixes(long, "bluesky", None, link_url=None, post_type="text", force_length=True)
    assert len(svc.full_caption(trimmed["text"], trimmed["hashtags"])) <= 300
    # malformed model output
    with pytest.raises(ValueError):
        svc.normalize_draft("nope", "linkedin_org", "text")
    d = svc.normalize_draft({"text": "Body line\n#One #Two", "hashtags": "#Three, four"}, "pinterest", "carousel")
    assert d["text"] == "Body line" and d["hashtags"] == ["One", "Two", "Three", "four"] and d["slides"] == []


async def test_uniqueness_regenerates_with_jaccard_fallback(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    old_text = "Payroll mistakes cost companies time and trust. HRP HRMS checks every payslip before it goes out."
    await mk_post(o["id"], acc, status="published", text=old_text, topic="Payroll")
    fake.writes = [{"text": old_text, "hashtags": []}]
    p = await mk_post(o["id"], acc, mode="full_auto")
    await generate(o["id"], p.id)
    post = await get_post(o["id"], p.id)
    assert fake.count("write") == 2  # first draft duplicated the old post
    assert post.similarity < svc.JACCARD_THRESHOLD and post.status == "scheduled"
    assert post.qa["similarity_method"] == "jaccard" and post.embedding is None
    second_prompt = [c for c in fake.calls if c[0] == "write"][1][2]
    assert "Do not repeat this earlier post" in second_prompt

    # Always the same text: gives up after 2 retries and holds it for a human.
    fake.writes = [{"text": old_text, "hashtags": []} for _ in range(3)]
    p2 = await mk_post(o["id"], acc, mode="full_auto")
    await generate(o["id"], p2.id)
    post2 = await get_post(o["id"], p2.id)
    assert post2.status == "in_review" and post2.mode == "approve_first" and post2.similarity == 1.0
    assert any("similar" in w for w in post2.meta["warnings"])


async def test_embedding_similarity_uses_pgvector(client, fake, monkeypatch):
    o = await new_org(client)
    acc = await mk_account(o["id"])

    async def embed(session, texts):
        return [[1.0, 0.0, 0.0]], "fake:emb"

    monkeypatch.setattr(llm_router, "embed_texts", embed)
    await mk_post(o["id"], acc, status="published", text="old", embedding=[0.99, 0.05, 0.0], embedding_model="fake:emb")
    p = await mk_post(o["id"], acc, mode="manual")
    await generate(o["id"], p.id)
    post = await get_post(o["id"], p.id)
    assert fake.count("write") == 3 and post.similarity > 0.99 and post.embedding_model == "fake:emb"
    assert post.status == "draft"


async def test_modes_route_and_notify(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    slot = datetime.now(UTC) + timedelta(hours=10)
    pa = await mk_post(o["id"], acc, mode="approve_first", scheduled_at=slot)
    pw = await mk_post(o["id"], acc, mode="auto_with_window", scheduled_at=slot)
    pw_soon = await mk_post(o["id"], acc, mode="auto_with_window", scheduled_at=datetime.now(UTC) + timedelta(hours=1))
    pm = await mk_post(o["id"], acc, mode="manual", scheduled_at=slot)
    for p in (pa, pw, pw_soon, pm):
        await generate(o["id"], p.id)
    a, w, ws, m = [await get_post(o["id"], p.id) for p in (pa, pw, pw_soon, pm)]
    assert a.status == "in_review" and a.approval_deadline == slot
    assert w.status == "in_review" and abs((w.approval_deadline - datetime.now(UTC)) - timedelta(hours=4)) < timedelta(minutes=2)
    assert ws.approval_deadline == ws.scheduled_at - timedelta(minutes=5)
    assert m.status == "draft" and m.approval_deadline is None
    notes = await scalar(o["id"], select(func.count()).select_from(Notification).where(
        Notification.event == "post_review", Notification.user_id == o["user_id"]))
    assert notes == 3


async def test_low_judge_score_holds_auto_post(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    fake.score = 3
    p = await mk_post(o["id"], acc, mode="full_auto")
    await generate(o["id"], p.id)
    post = await get_post(o["id"], p.id)
    assert post.status == "in_review" and post.meta["held_from_mode"] == "full_auto"


async def test_media_failure_downgrades_or_fails(client, fake, monkeypatch):
    o = await new_org(client)
    li = await mk_account(o["id"])
    ig = await mk_account(o["id"], platform="instagram", name="IG")

    async def broken(session, post, brand):
        raise RuntimeError("renderer offline")

    monkeypatch.setattr(sys.modules["app.services.media"], "generate_for_post", broken)
    p1 = await mk_post(o["id"], li, post_type="image", meta={"post_type_fixed": True}, mode="manual")
    await generate(o["id"], p1.id)
    post = await get_post(o["id"], p1.id)
    assert post.post_type == "text" and post.status == "draft" and "renderer offline" in post.meta["warnings"][0]

    p2 = await mk_post(o["id"], ig, post_type="image", meta={"post_type_fixed": True})
    with pytest.raises(PermanentError):
        await generate(o["id"], p2.id)
    post = await get_post(o["id"], p2.id)
    assert post.status == "failed" and "Couldn't create the single image" in post.error


async def test_media_attached_for_image_posts(client, fake):
    o = await new_org(client)
    li = await mk_account(o["id"])
    s = await mk_schedule(o["id"], li.id, post_types={"image": 1})
    p = await mk_post(o["id"], li, schedule_id=s.id)
    await generate(o["id"], p.id)
    post = await get_post(o["id"], p.id)
    assert post.post_type == "image" and len(post.media_ids) == 1 and post.meta["image_brief"] == "A calm office"
    r = await client.get(f"/content/posts/{p.id}", headers=o["h"])
    assert r.json()["media"][0]["url"].startswith("/api/v1/media/file/")


async def test_no_text_model_fails_permanently(client, monkeypatch):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    p = await mk_post(o["id"], acc)
    monkeypatch.setitem(sys.modules, "app.services.knowledge", None)  # also proves missing modules degrade
    with pytest.raises(PermanentError, match="Add an AI text model"):
        await generate(o["id"], p.id)
    post = await get_post(o["id"], p.id)
    assert post.status == "failed" and post.error == "Add an AI text model in Settings → Integrations"


# ================================================================== actions
async def test_approve_reject_regenerate_with_activity_and_audit(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    p = await mk_post(o["id"], acc, status="in_review", text="Hello", mode="approve_first")
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        from app.models import User

        user = await s.get(User, o["user_id"])
        assert (await actions.approve_by_id(s, str(p.id), user, via="Telegram")).startswith("Approved")
        await s.commit()
        post = await s.get(Post, p.id)
        assert post.status == "scheduled" and post.approved_by == user.id
        assert await actions.reject_by_id(s, p.id, user, "Too salesy, mention payroll less") == \
            "Rejected. The reason will guide future posts."
        await s.commit()
        post = await s.get(Post, p.id)
        assert post.status == "rejected" and post.meta["avoid"] == ["Too salesy, mention payroll less"]
        msg = await actions.regenerate_by_id(s, p.id, user)
        await s.commit()
        assert "new version" in msg
        with pytest.raises(ValueError):
            await actions.approve_by_id(s, uuid.uuid4(), user)
        acts = (await s.scalars(select(PostActivity.action).where(PostActivity.post_id == p.id)
                                .order_by(PostActivity.created_at))).all()
        assert acts == ["approved", "rejected", "regenerate_requested"]
        audits = (await s.scalars(select(AuditLog.action).where(AuditLog.entity_id == str(p.id)))).all()
        assert sorted(audits) == ["post.approved", "post.regenerated", "post.rejected"]
        actor = await s.scalar(select(PostActivity.actor).where(PostActivity.post_id == p.id, PostActivity.action == "approved"))
        assert actor.endswith("via Telegram")
    # regenerate queued a job; running it uses the rejection reason in the prompt
    job = await scalar(o["id"], select(Job).where(Job.dedupe_key == f"gen:{p.id}"))
    assert job is not None and job.status == "queued"
    await generate(o["id"], p.id)
    system = [c for c in fake.calls if c[0] == "write"][-1][2]
    assert "Too salesy" in system
    post = await get_post(o["id"], p.id)
    assert post.status == "in_review" and "regenerate_note" in post.meta and post.meta["regenerate_note"] is None


async def test_window_promotion_dispatch_and_catch_up(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    now = datetime.now(UTC)
    promote = await mk_post(o["id"], acc, status="in_review", mode="auto_with_window", text="w",
                            approval_deadline=now - timedelta(minutes=1), scheduled_at=now + timedelta(minutes=4))
    still = await mk_post(o["id"], acc, status="in_review", mode="auto_with_window", text="w2",
                          approval_deadline=now + timedelta(hours=1), scheduled_at=now + timedelta(hours=2))
    late = await mk_post(o["id"], acc, status="scheduled", text="late", scheduled_at=now - timedelta(hours=1))
    too_late = await mk_post(o["id"], acc, status="scheduled", text="old", scheduled_at=now - timedelta(hours=5))
    unapproved = await mk_post(o["id"], acc, status="in_review", mode="approve_first", text="u",
                               scheduled_at=now - timedelta(minutes=30))
    result = await scheduler.run_org(o["id"])
    assert result["promoted"] == 1 and result["queued"] == 1 and result["missed"] == 2
    assert (await get_post(o["id"], promote.id)).status == "scheduled"
    assert (await get_post(o["id"], still.id)).status == "in_review"
    assert (await get_post(o["id"], too_late.id)).status == "missed"
    assert (await get_post(o["id"], unapproved.id)).status == "missed"
    job = await scalar(o["id"], select(Job).where(Job.dedupe_key == f"pub:{late.id}"))
    assert job.kind == "post.publish"
    # running again doesn't duplicate the publish job
    await scheduler.run_org(o["id"])
    n = await scalar(o["id"], select(func.count()).select_from(Job).where(Job.dedupe_key == f"pub:{late.id}"))
    assert n == 1
    missed_notes = await scalar(o["id"], select(func.count()).select_from(Notification).where(
        Notification.user_id == o["user_id"], Notification.title.like("Missed%")))
    assert missed_notes == 1

    # Approving a missed post within grace publishes it about now; too late needs a new time.
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        from app.models import User

        user = await s.get(User, o["user_id"])
        msg = await actions.approve_by_id(s, unapproved.id, user)
        assert "about a minute" in msg
        post = await s.get(Post, unapproved.id)
        assert post.status == "scheduled" and post.scheduled_at > datetime.now(UTC)
        with pytest.raises(ValueError, match="Pick a new time"):
            await actions.approve_by_id(s, too_late.id, user)
        new_time = datetime.now(UTC) + timedelta(days=1)
        await actions.approve_by_id(s, too_late.id, user, scheduled_at=new_time)
        assert (await s.get(Post, too_late.id)).scheduled_at == new_time
        await s.commit()


# ================================================================== publishing hand-off
async def test_manual_platform_goes_to_manual_pending(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"], platform="manual", status="manual")
    xacc = await mk_account(o["id"], platform="x", status="connected", name="X")
    p = await mk_post(o["id"], acc, status="scheduled", text="By hand", scheduled_at=datetime.now(UTC))
    px = await mk_post(o["id"], xacc, status="scheduled", text="On X", scheduled_at=datetime.now(UTC))
    assert (await run_inline("post.publish", o["id"], {"post_id": str(p.id)}))["status"] == "manual_pending"
    assert (await run_inline("post.publish", o["id"], {"post_id": str(px.id)}))["status"] == "manual_pending"
    assert fake.publish_calls == []
    n = await scalar(o["id"], select(func.count()).select_from(Notification).where(Notification.event == "manual_publish"))
    assert n == 2
    r = await client.post(f"/content/posts/{p.id}/mark-published", headers=o["h"],
                          json={"external_url": "https://linkedin.com/p/1"})
    assert r.status_code == 200 and r.json()["status"] == "published"


async def test_publish_success_retry_and_reconnect(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    ok = await mk_post(o["id"], acc, status="scheduled", text="Go", scheduled_at=datetime.now(UTC))
    r = await run_inline("post.publish", o["id"], {"post_id": str(ok.id)})
    post = await get_post(o["id"], ok.id)
    assert r["status"] == "published" and post.external_id == "ext-1" and post.published_at
    assert (await run_inline("post.publish", o["id"], {"post_id": str(ok.id)}))["skipped"] == "Already published."
    # metrics
    await run_inline("post.metrics", o["id"], {"post_id": str(ok.id)})
    m = await scalar(o["id"], select(PostMetric).where(PostMetric.post_id == ok.id))
    assert m.impressions == 120 and m.likes == 7 and m.comments == 0

    # retryable: re-raises for the job to retry; final attempt fails and notifies
    fake.publish_error = PublishError("rate limited", retryable=True)
    rp = await mk_post(o["id"], acc, status="scheduled", text="Retry", scheduled_at=datetime.now(UTC))
    job_id = uuid.uuid4()  # retries re-run the same job
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        with pytest.raises(PublishError):
            await HANDLERS["post.publish"](JobContext(job_id, "post.publish", o["id"], {"post_id": str(rp.id)}, s, 1))
    assert (await get_post(o["id"], rp.id)).status == "publishing"
    # A different (duplicate) job must not touch a post another job is publishing.
    dup = await run_inline("post.publish", o["id"], {"post_id": str(rp.id)})
    assert "skipped" in dup
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        res = await HANDLERS["post.publish"](JobContext(job_id, "post.publish", o["id"], {"post_id": str(rp.id)}, s, 3))
        await s.commit()
    assert res["status"] == "failed"
    post = await get_post(o["id"], rp.id)
    assert post.status == "failed" and post.error == "rate limited" and post.attempts == 2

    # non-retryable fails at once
    fake.publish_error = PublishError("text too long", retryable=False)
    nr = await mk_post(o["id"], acc, status="scheduled", text="Bad", scheduled_at=datetime.now(UTC))
    assert (await run_inline("post.publish", o["id"], {"post_id": str(nr.id)}))["status"] == "failed"

    # reconnect: account marked expired, owner told
    fake.publish_error = PublishError("token revoked", reconnect=True)
    rc = await mk_post(o["id"], acc, status="scheduled", text="Token", scheduled_at=datetime.now(UTC))
    r = await run_inline("post.publish", o["id"], {"post_id": str(rc.id)})
    assert r == {"status": "failed", "reconnect": True}
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        assert (await s.get(SocialAccount, acc.id)).status == "expired"
        events = set((await s.scalars(select(Notification.event).where(Notification.user_id == o["user_id"]))).all())
    assert {"post_published", "post_failed", "account_issue"} <= events
    # expired accounts are not planned
    await mk_schedule(o["id"], acc.id)
    assert (await scheduler.run_org(o["id"]))["planned"] == 0


# ================================================================== API
async def test_brand_pillars_campaigns_api(client, fake):
    o = await new_org(client)
    h = o["h"]
    r = await client.get("/content/brand", headers=h)
    assert r.status_code == 200 and r.json()["emoji_policy"] == "light"
    r = await client.put("/content/brand", headers=h, json={
        "voice": "Warm, expert", "banned_words": ["synergy", "Synergy", ""], "hashtags": ["#HR Tech", "AI"],
        "blackout_dates": ["2026-12-25", "2026-12-25"], "emoji_policy": "none"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["banned_words"] == ["synergy"] and b["hashtags"] == ["HRTech", "AI"] and b["blackout_dates"] == ["2026-12-25"]
    assert (await client.put("/content/brand", headers=h, json={"blackout_dates": ["25/12/2026"]})).status_code == 422
    r = await client.post("/content/pillars/seed-defaults", headers=h)
    assert len(r.json()) == 5
    assert len((await client.post("/content/pillars/seed-defaults", headers=h)).json()) == 5
    pid = r.json()[0]["id"]
    r = await client.patch(f"/content/pillars/{pid}", headers=h, json={"weight": 55, "color": "#123456"})
    assert r.json()["weight"] == 55
    assert (await client.patch(f"/content/pillars/{pid}", headers=h, json={"color": "red"})).status_code == 422
    r = await client.post("/content/campaigns", headers=h, json={"name": "Diwali", "starts_on": "2026-11-01",
                                                                  "ends_on": "2026-10-01"})
    assert r.status_code == 400 and "end date" in r.json()["detail"]
    r = await client.post("/content/campaigns", headers=h, json={"name": "Diwali", "starts_on": "2026-11-01",
                                                                  "ends_on": "2026-11-10", "boost": 100})
    assert r.status_code == 201
    meta = (await client.get("/content/meta", headers=h)).json()
    assert any(p["id"] == "instagram" and "text" not in p["post_types"] for p in meta["platforms"])
    assert set(meta["modes"]) == {"manual", "approve_first", "auto_with_window", "full_auto"}


async def test_schedule_api_validation_preview_and_run_now(client, fake):
    o = await new_org(client)
    h = o["h"]
    ig = await mk_account(o["id"], platform="instagram", name="IG")
    li = await mk_account(o["id"])
    r = await client.post("/content/schedules", headers=h, json={
        "social_account_id": str(ig.id), "name": "IG daily", "post_types": {"text": 1}})
    assert r.status_code == 400 and "doesn't support text only posts" in r.json()["detail"]
    r = await client.post("/content/schedules", headers=h, json={
        "social_account_id": str(li.id), "name": "LI", "times": ["9:5"]})
    assert r.status_code == 422
    r = await client.post("/content/schedules", headers=h, json={
        "social_account_id": str(li.id), "name": "LI", "include_link": True})
    assert r.status_code == 400 and "link" in r.json()["detail"]
    r = await client.post("/content/schedules", headers=h, json={
        "social_account_id": str(li.id), "name": "LI twice a day", "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "times": ["17:30", "10:00"], "post_types": {"text": 1, "image": 2}, "mode": "auto_with_window"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["times"] == ["10:00", "17:30"] and r.json()["ready"] is True
    prev = (await client.get(f"/content/schedules/{sid}/preview?days=7", headers=h)).json()
    assert prev["timezone"] == "Asia/Kolkata" and 13 <= len(prev["slots"]) <= 14
    assert all(s["local"].endswith(("10:00", "17:30")) for s in prev["slots"])
    r = await client.post(f"/content/schedules/{sid}/run-now", headers=h)
    assert r.status_code == 200
    first = r.json()
    r2 = (await client.post(f"/content/schedules/{sid}/run-now", headers=h)).json()
    assert r2["scheduled_at"] > first["scheduled_at"]  # next free slot
    await run_inline("post.generate", o["id"], {"post_id": first["post_id"]})
    post = (await client.get(f"/content/posts/{first['post_id']}", headers=h)).json()
    assert post["status"] == "in_review" and post["mode"] == "auto_with_window" and post["activities"]
    r = await client.patch(f"/content/schedules/{sid}", headers=h, json={"post_types": {"video": 1}, "active": False})
    assert r.status_code == 200 and r.json()["active"] is False


async def test_posts_api_flow(client, fake):
    o = await new_org(client)
    h = o["h"]
    acc = await mk_account(o["id"])
    pin = await mk_account(o["id"], platform="pinterest", name="Pins")
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    r = await client.post("/content/posts", headers=h, json={
        "account_id": str(acc.id), "text": "Hand written post", "hashtags": ["#One", "two"], "status": "in_review",
        "scheduled_at": future})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["status"] == "in_review" and p["hashtags"] == ["One", "two"] and p["qa"]["passed"] is True
    r = await client.post("/content/posts", headers=h, json={"account_id": str(pin.id), "post_type": "text", "text": "x"})
    assert r.status_code == 400
    # edit, approvals queue, approve
    r = await client.patch(f"/content/posts/{p['id']}", headers=h, json={"text": "Edited text"})
    assert r.json()["text"] == "Edited text" and r.json()["activities"][0]["action"] == "edited"
    q = (await client.get("/content/approvals", headers=h)).json()
    assert [x["id"] for x in q] == [p["id"]]
    r = await client.post(f"/content/posts/{p['id']}/approve", headers=h)
    assert r.status_code == 200 and r.json()["post"]["status"] == "scheduled"
    r = await client.post(f"/content/posts/{p['id']}/approve", headers=h)
    assert r.json()["message"] == "Already approved."
    # compose with AI for two accounts
    r = await client.post("/content/posts/compose", headers=h, json={
        "account_ids": [str(acc.id), str(pin.id)], "brief": "Launch of leave management", "post_type": "image"})
    assert r.status_code == 200, r.text
    made = r.json()["posts"]
    assert len(made) == 2
    for m in made:
        await run_inline("post.generate", o["id"], {"post_id": str(m["post_id"])})
    pins = (await client.get("/content/posts?platform=pinterest", headers=h)).json()
    assert pins["total"] == 1 and pins["items"][0]["status"] == "draft" and pins["items"][0]["post_type"] == "image"
    pin_post = (await client.get(f"/content/posts/{pins['items'][0]['id']}", headers=h)).json()
    assert pin_post["title"] and len(pin_post["title"]) <= 100 and pin_post["topic"] == "Launch of leave management"
    # search, calendar, duplicate, delete, publish-now
    found = (await client.get("/content/posts?q=Edited", headers=h)).json()
    assert found["total"] == 1
    start = datetime.now(UTC).date().isoformat()
    end = (datetime.now(UTC) + timedelta(days=5)).date().isoformat()
    cal = (await client.get(f"/content/calendar?start={start}T00:00:00&end={end}T00:00:00", headers=h)).json()
    assert [c["id"] for c in cal] == [p["id"]]
    r = await client.post(f"/content/posts/{p['id']}/duplicate", headers=h, json={"account_id": str(pin.id)})
    assert r.status_code == 400  # Pinterest has no text posts
    r = await client.post(f"/content/posts/{p['id']}/publish-now", headers=h)
    assert r.status_code == 200 and r.json()["job_id"]
    await run_inline("post.publish", o["id"], {"post_id": p["id"]})
    r = await client.patch(f"/content/posts/{p['id']}", headers=h, json={"text": "nope"})
    assert r.status_code == 409
    assert (await client.delete(f"/content/posts/{p['id']}", headers=h)).status_code == 409
    draft = made[0]["post_id"]
    assert (await client.delete(f"/content/posts/{draft}", headers=h)).status_code == 204
    assert (await client.get(f"/content/posts/{draft}", headers=h)).status_code == 404


async def test_viewer_cannot_approve_and_tenants_are_isolated(client, fake):
    o = await new_org(client)
    other = await new_org(client)
    acc = await mk_account(o["id"])
    p = await mk_post(o["id"], acc, status="in_review", text="Secret plan")
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        viewer_role = await s.scalar(select(Role.id).where(Role.name == "Viewer"))
    r = await client.post("/users", headers=o["h"], json={
        "email": f"viewer-{uuid.uuid4().hex[:6]}@content-test.in", "full_name": "Vee", "role_ids": [str(viewer_role)]})
    assert r.status_code == 201, r.text
    vt = await login(client, r.json()["member"]["email"], r.json()["temporary_password"])
    vh = auth(vt)
    assert (await client.get(f"/content/posts/{p.id}", headers=vh)).status_code == 200
    r = await client.post(f"/content/posts/{p.id}/approve", headers=vh)
    assert r.status_code == 403 and "content.approve" in r.json()["detail"]
    assert (await client.post("/content/approvals/bulk", headers=vh, json={"ids": [str(p.id)], "action": "approve"})).status_code == 403
    assert (await client.put("/content/brand", headers=vh, json={})).status_code == 403
    assert (await client.post("/content/pillars/seed-defaults", headers=vh)).status_code == 403
    # another tenant sees nothing and can't act
    oh = other["h"]
    assert (await client.get(f"/content/posts/{p.id}", headers=oh)).status_code == 404
    assert (await client.post(f"/content/posts/{p.id}/approve", headers=oh)).status_code == 404
    assert (await client.get("/content/posts", headers=oh)).json()["total"] == 0
    assert (await client.get("/content/approvals", headers=oh)).json() == []
    r = await client.post("/content/approvals/bulk", headers=oh, json={"ids": [str(p.id)], "action": "reject"})
    assert r.json()["failed"] == 1
    r = await client.post("/content/schedules", headers=oh, json={"social_account_id": str(acc.id), "name": "steal"})
    assert r.status_code == 404
    assert (await get_post(o["id"], p.id)).status == "in_review"
    # bulk approve as the owner works
    r = await client.post("/content/approvals/bulk", headers=o["h"], json={"ids": [str(p.id)], "action": "approve"})
    assert r.json()["ok"] == 1


async def test_daily_digest_once_per_day(client, fake):
    from app.models import NotificationPref
    from app.modules.content import daily_digest_for

    o = await new_org(client)
    acc = await mk_account(o["id"])
    await mk_post(o["id"], acc, status="in_review", text="waiting")
    await add(o["id"], NotificationPref(organization_id=o["id"], user_id=o["user_id"], channel="inapp", enabled=True,
                                        events=["daily_digest", "post_review"]))
    morning = datetime.now(IST).replace(hour=9, minute=5).astimezone(UTC)
    res = await daily_digest_for(o["id"], morning)
    assert res["waiting_for_approval"] == 1 and res["recipients"] == 1
    assert await daily_digest_for(o["id"], morning + timedelta(minutes=10)) is None
    early = datetime.now(IST).replace(hour=7).astimezone(UTC) + timedelta(days=1)
    assert await daily_digest_for(o["id"], early) is None


async def test_posts_bulk_actions(client, fake):
    o = await new_org(client)
    other = await new_org(client)
    acc = await mk_account(o["id"])
    a = await mk_post(o["id"], acc, status="in_review", text="One")
    b = await mk_post(o["id"], acc, status="in_review", text="Two")
    no_time = await mk_post(o["id"], acc, status="in_review", text="Three", scheduled_at=None)
    pub = await mk_post(o["id"], acc, status="published", text="Live")
    h = o["h"]
    r = (await client.post("/content/posts/bulk", headers=h, json={"ids": [str(a.id), str(no_time.id)], "action": "approve"})).json()
    assert r["ok"] == 1 and r["failed"] == 1 and "date and time" in [x for x in r["results"] if not x["ok"]][0]["message"]
    assert (await get_post(o["id"], a.id)).status in ("scheduled", "approved")
    r = (await client.post("/content/posts/bulk", headers=h, json={"ids": [str(b.id)], "action": "reject", "reason": "Off brand"})).json()
    assert r["ok"] == 1 and (await get_post(o["id"], b.id)).status == "rejected"
    r = (await client.post("/content/posts/bulk", headers=h, json={"ids": [str(no_time.id), str(pub.id)], "action": "publish"})).json()
    assert r["ok"] == 1 and "already published" in [x for x in r["results"] if not x["ok"]][0]["message"]
    assert (await get_post(o["id"], no_time.id)).status == "scheduled"
    r = (await client.post("/content/posts/bulk", headers=h, json={"ids": [str(b.id), str(pub.id)], "action": "delete"})).json()
    assert r["ok"] == 1 and (await get_post(o["id"], pub.id)) is not None
    # another organization can't touch these posts
    r = (await client.post("/content/posts/bulk", headers=other["h"], json={"ids": [str(a.id)], "action": "delete"})).json()
    assert r["failed"] == 1 and (await get_post(o["id"], a.id)) is not None
