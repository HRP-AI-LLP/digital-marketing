"""Smart scheduling: best-time bucket maths and fallback, best-time schedule slots, the suggestion endpoint,
the best_times report and evergreen recycling (LLM faked like tests/test_content.py)."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant
from app.models import Organization, Post, PostMetric, Schedule
from app.services import best_time, evergreen, scheduler
from app.services.best_time import Sample
from tests.test_content import add, fake, get_post, mk_account, mk_post, mk_schedule, new_org, scalars  # noqa: F401

IST = ZoneInfo("Asia/Kolkata")


def _local(days_ago: int, hour: int, weekday: int | None = None) -> datetime:
    """A UTC instant `days_ago` days back (moved to `weekday` if given) at `hour`:00 IST."""
    d = datetime.now(IST).date() - timedelta(days=days_ago)
    if weekday is not None:
        d -= timedelta(days=(d.weekday() - weekday) % 7)
    return datetime.combine(d, time(hour, 0), tzinfo=IST).astimezone(UTC)


async def published(org_id, account, at: datetime, *, likes=10, impressions=100, **kw) -> Post:
    p = await mk_post(org_id, account, status="published", published_at=at, scheduled_at=at, text=kw.pop("text", "Hello"),
                      **kw)
    await add(org_id, PostMetric(organization_id=org_id, post_id=p.id, impressions=impressions, likes=likes, comments=0,
                                 shares=0, clicks=0, saves=0))
    return p


# ================================================================== bucket maths
async def test_bucket_maths_kernel_normalisation_and_confidence():
    samples = [Sample(1, 15, 0.10), Sample(1, 15, 0.20), Sample(3, 9, 0.05)]
    bt = best_time.build(samples, "linkedin_org")
    assert bt.sample == 3 and bt.source == "account"
    assert abs(bt.confidence - 0.3) < 1e-9
    assert bt.counts[1][15] == 2 and bt.counts[3][9] == 1 and sum(map(sum, bt.counts)) == 3
    # Tue 15:00 is the strongest own bucket (mean 0.15 → scaled to 1.0); neighbours get a share of it.
    assert bt.own[1][15] == 1.0
    assert bt.own[1][14] is not None and bt.own[1][16] is not None
    assert bt.own[1][3] is None  # nothing near 03:00
    # Blend: 0.85 × confidence of own + the rest from the LinkedIn defaults.
    w = 0.85 * 0.3
    assert abs(bt.grid[1][15] - (w * 1.0 + (1 - w) * bt.defaults[1][15])) < 1e-9
    # Full confidence from 10 posts on: own data dominates and Tue 15:00 wins over the default morning peak.
    many = [Sample(1, 15, 0.2)] * 10 + [Sample(1, 9, 0.01)] * 2
    strong = best_time.build(many, "linkedin_org")
    assert strong.confidence == 1.0
    assert strong.ranked_hours(1)[0] == 15


async def test_defaults_fallback_and_pick_hours_spacing():
    bt = best_time.build([], "linkedin_org")
    assert bt.source == "defaults" and bt.confidence == 0 and "typical" in bt.message()
    assert bt.ranked_hours(1)[0] == 9  # LinkedIn: weekday mornings
    assert best_time.build([], "instagram").ranked_hours(2)[0] == 20  # consumer networks: evening
    hours = bt.pick_hours(1, 3)
    assert hours == sorted(hours) and len(hours) == 3
    assert all(best_time.DAY_START <= h <= best_time.DAY_END for h in hours)
    assert all(b - a >= 3 for a, b in zip(hours, hours[1:]))
    assert 3 < len(bt.pick_hours(1, 9)) <= 5  # 08, 11, 14, 17, 20 is the most that fits
    assert best_time.build([], "unknown").ranked_hours(0)[0] == 10  # generic defaults
    hm = bt.heatmap()
    assert len(hm) == 168 and hm[0]["day"] == "Mon"


async def test_samples_use_rate_or_raw_engagement():
    class P:
        def __init__(self, at):
            self.published_at = at

    class M:
        def __init__(self, imp, likes):
            self.impressions, self.likes, self.comments, self.shares, self.saves, self.clicks = imp, likes, 1, 0, 0, 0

    at = datetime(2026, 10, 20, 4, 30, tzinfo=UTC)  # Tue 10:00 IST
    s, metric = best_time.samples_from([(P(at), M(100, 9)), (P(at), M(50, 4))], IST)
    assert metric == "engagement_rate" and [round(x.value, 2) for x in s] == [0.1, 0.1]
    assert (s[0].weekday, s[0].hour) == (1, 10)
    s, metric = best_time.samples_from([(P(at), M(0, 9)), (P(at), M(0, 4)), (P(at), None)], IST)
    assert metric == "engagements" and [x.value for x in s] == [10.0, 5.0]


async def test_account_falls_back_to_platform_pool(client, fake):
    o = await new_org(client)
    mine = await mk_account(o["id"], name="New page")
    other = await mk_account(o["id"], name="Old page")
    for i in range(12):  # evenings do much better than mornings on this platform
        await published(o["id"], other, _local(i * 5 + 1, 18), likes=20)
        await published(o["id"], other, _local(i * 5 + 1, 9), likes=1)
    await published(o["id"], mine, _local(3, 9), likes=1)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        bt = await best_time.for_account(s, mine, IST)
        own = await best_time.for_account(s, other, IST)
    assert bt.source == "platform" and bt.sample == 25
    assert own.source == "account" and own.sample == 24 and own.confidence == 1.0
    assert all(own.ranked_hours(wd)[0] == 18 for wd in range(7))


# ================================================================== schedules with time_mode = best
async def test_slot_times_best_mode_picks_top_hours_per_day():
    bt = best_time.build([Sample(wd, 15, 0.3) for wd in range(7)] + [Sample(wd, 9, 0.01) for wd in range(7)],
                         "linkedin_org")
    sched = Schedule(days_of_week=[0, 2], times=["10:00"], time_mode="best", posts_per_day=2)
    start = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)  # Mon 19 Oct 01:30 IST
    slots = scheduler.slot_times(sched, IST, start, start + timedelta(days=4), best=bt)
    local = [s.astimezone(IST) for s in slots]
    assert [t.strftime("%a") for t in local] == ["Mon", "Mon", "Wed", "Wed"]
    for a, b in ((local[0], local[1]), (local[2], local[3])):
        assert b.hour - a.hour >= 3 and 8 <= a.hour <= 21 and 8 <= b.hour <= 21
    assert 15 in {t.hour for t in local}
    # Without learned data the platform-neutral defaults are used; fixed schedules are unchanged.
    assert len(scheduler.slot_times(sched, IST, start, start + timedelta(days=4))) == 4
    fixed = Schedule(days_of_week=[0, 2], times=["09:30"])
    assert [s.astimezone(IST).strftime("%a %H:%M") for s in scheduler.slot_times(fixed, IST, start, start + timedelta(days=4))] \
        == ["Mon 09:30", "Wed 09:30"]


async def test_best_mode_planning_is_capped_per_day_and_idempotent(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    for i in range(10):
        await published(o["id"], acc, _local(i * 3 + 2, 16), likes=30)
        await published(o["id"], acc, _local(i * 3 + 2, 9), likes=1)
    sched = await mk_schedule(o["id"], acc.id, time_mode="best", posts_per_day=2, lead_hours=48, times=["10:00"])
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        first = await scheduler.plan_all(s, org, now)
        await s.commit()
        assert await scheduler.plan_all(s, org, now) == []
        await s.commit()
        at = await scheduler.next_free_slot(s, await s.get(Schedule, sched.id), IST, now)
    per_day: dict[date, int] = {}
    for p in first:
        d = p.scheduled_at.astimezone(IST).date()
        per_day[d] = per_day.get(d, 0) + 1
        assert 8 <= p.scheduled_at.astimezone(IST).hour <= 21
    assert first and max(per_day.values()) <= 2
    assert 16 in {p.scheduled_at.astimezone(IST).hour for p in first}
    assert at is not None and at.astimezone(IST).date() not in {d for d, n in per_day.items() if n >= 2}


async def test_schedule_api_accepts_best_time_and_previews(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    r = await client.post("/content/schedules", headers=o["h"], json={
        "social_account_id": str(acc.id), "name": "Smart", "days_of_week": [0, 1, 2, 3, 4, 5, 6], "times": ["10:00"],
        "post_types": {"text": 1}, "time_mode": "best", "posts_per_day": 3})
    assert r.status_code == 201, r.text
    assert r.json()["time_mode"] == "best" and r.json()["posts_per_day"] == 3
    sid = r.json()["id"]
    prev = (await client.get(f"/content/schedules/{sid}/preview?days=3", headers=o["h"])).json()["slots"]
    assert 6 <= len(prev) <= 9
    bad = await client.patch(f"/content/schedules/{sid}", headers=o["h"], json={"posts_per_day": 9})
    assert bad.status_code == 422
    r = await client.patch(f"/content/schedules/{sid}", headers=o["h"], json={"time_mode": "fixed"})
    assert r.json()["time_mode"] == "fixed"


# ================================================================== suggestion endpoint
async def test_best_times_endpoint_returns_next_five(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    r = await client.get(f"/content/best-times?account_id={acc.id}", headers=o["h"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "defaults" and d["confidence"] == 0 and len(d["heatmap"]) == 168
    slots = d["slots"]
    assert len(slots) == 5
    ats = [datetime.fromisoformat(s["at"]) for s in slots]
    assert ats == sorted(ats) and ats[0] >= datetime.now(UTC) + timedelta(minutes=29)
    for s, at in zip(slots, ats):
        local = at.astimezone(IST)
        assert 8 <= local.hour <= 21 and s["input"] == local.strftime("%Y-%m-%dT%H:%M")
    # An hour already taken by a post on the account isn't suggested again.
    taken = ats[0]
    await mk_post(o["id"], acc, status="scheduled", scheduled_at=taken)
    again = (await client.get(f"/content/best-times?account_id={acc.id}", headers=o["h"])).json()["slots"]
    assert taken.isoformat() not in {s["at"] for s in again}
    assert (await client.get(f"/content/best-times?account_id={uuid.uuid4()}", headers=o["h"])).status_code == 404


async def test_best_times_report_builds(client, fake):
    from app.services import reports as rep

    o = await new_org(client)
    acc = await mk_account(o["id"])
    await published(o["id"], acc, _local(2, 11))
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        _, doc, _ = await rep.build(s, org, "best_times", {})
    titles = [b.get("title") for b in doc.blocks]
    assert "Best times by account" in titles and any("score by weekday" in (t or "") for t in titles)


# ================================================================== evergreen recycling
async def _org_row(org_id):
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return await s.get(Organization, org_id)


async def test_evergreen_toggle_settings_and_recycling(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    media_id = str(uuid.uuid4())
    good = await published(o["id"], acc, datetime.now(UTC) - timedelta(days=90), likes=50,
                           text="Payroll in 3 clicks with HRP HRMS. https://hrpai.in/products/hrms", media_ids=[media_id],
                           post_type="image")
    meh = await published(o["id"], acc, datetime.now(UTC) - timedelta(days=80), likes=2, text="Leave policies made simple.")
    fresh = await published(o["id"], acc, datetime.now(UTC) - timedelta(days=5), likes=99, text="New this week.")
    sched = await mk_schedule(o["id"], acc.id, times=["10:00"], lead_hours=24)
    for p in (good, meh, fresh):
        r = await client.post(f"/content/posts/{p.id}/evergreen", headers=o["h"], json={"evergreen": True})
        assert r.status_code == 200 and r.json()["evergreen"] is True

    # Off by default: nothing happens.
    assert (await evergreen.run_org(o["id"]))["created"] is None
    r = await client.put("/content/evergreen", headers=o["h"], json={"enabled": True, "per_week": 2, "min_days_between": 60})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["settings"]["enabled"] and d["eligible"] == 2 and d["total"] == 3
    assert d["queue"][0]["id"] == str(good.id)  # best performer first
    assert next(q for q in d["queue"] if q["id"] == str(fresh.id))["eligible"] is False

    fake.writes = [{"text": "Run payroll in 3 clicks: HRP HRMS makes it easy. https://hrpai.in/products/hrms",
                    "hashtags": ["Payroll"]}]
    res = await evergreen.run_org(o["id"])
    assert res["created"], res
    new = await get_post(o["id"], uuid.UUID(res["created"]))
    assert new.meta["recycled_from"] == str(good.id) and new.id != good.id
    assert new.media_ids == [media_id] and new.post_type == "image" and new.social_account_id == acc.id
    assert new.text.startswith("Run payroll") and new.status == "in_review" and new.mode == "approve_first"
    assert new.schedule_id == sched.id and new.scheduled_at is not None and new.scheduled_at > datetime.now(UTC)
    assert new.scheduled_at.astimezone(IST).strftime("%H:%M") == "10:00"
    write_prompt = next(c for c in fake.calls if c[0] == "write")
    assert "Payroll in 3 clicks" in write_prompt[1] and "never invent numbers" in write_prompt[2]
    # Spread out: the next run isn't due yet; history shows the reshare.
    assert (await evergreen.run_org(o["id"]))["created"] is None
    d = (await client.get("/content/evergreen", headers=o["h"])).json()
    assert d["history"][0]["original"]["id"] == str(good.id) and d["due"] is False
    assert next(q for q in d["queue"] if q["id"] == str(good.id))["eligible"] is False  # shared within 60 days

    # Run now (ignores the pace): the next best eligible post; an invented number holds it for review.
    fake.writes = [{"text": "Leave policies made simple for 500 teams.", "hashtags": []}]
    r = await client.post("/content/evergreen/run-now", headers=o["h"])
    assert r.status_code == 200, r.text
    held = await get_post(o["id"], uuid.UUID(r.json()["post_id"]))
    assert held.meta["recycled_from"] == str(meh.id) and held.status == "in_review"
    assert any("numbers" in w for w in held.meta["warnings"])
    r = await client.post("/content/evergreen/run-now", headers=o["h"])
    assert r.status_code == 400  # nothing left to reshare


async def test_evergreen_auto_approval_and_number_guard(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    src = await published(o["id"], acc, datetime.now(UTC) - timedelta(days=70), text="Our HRMS handles leave and payroll.")
    await client.post(f"/content/posts/{src.id}/evergreen", headers=o["h"], json={"evergreen": True})
    await client.put("/content/evergreen", headers=o["h"], json={"enabled": True, "mode": "full_auto"})
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        org.publishing = {**(org.publishing or {}), "auto_approve_posts": True}
        await s.commit()
    fake.writes = [{"text": "Leave and payroll, handled by our HRMS.", "hashtags": []}]
    res = await evergreen.run_org(o["id"])
    new = await get_post(o["id"], uuid.UUID(res["created"]))
    # No schedule on the account: it goes to the next best time and, with auto-approval, is scheduled.
    assert new.schedule_id is None and new.status == "scheduled" and new.scheduled_at > datetime.now(UTC)
    assert 8 <= new.scheduled_at.astimezone(IST).hour <= 21

    assert evergreen.check_facts("Save 3 hours a week. https://hrpai.in", "Save 5 hours. https://hrpai.in") == [
        "the new version mentions numbers that aren't in the original (5)"]
    assert evergreen.check_facts("See https://hrpai.in/x", "See our site") == ["a link from the original is missing"]
    assert evergreen.check_facts("3 tips", "Three tips, 3 of them") == []


async def test_evergreen_bulk_and_permissions(client, fake):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    a = await published(o["id"], acc, datetime.now(UTC) - timedelta(days=100))
    b = await mk_post(o["id"], acc, status="draft", text="Draft")
    r = await client.post("/content/posts/bulk", headers=o["h"], json={"ids": [str(a.id), str(b.id)], "action": "evergreen_on"})
    assert r.status_code == 200 and r.json()["ok"] == 2
    rows = await scalars(o["id"], select(Post).where(Post.evergreen.is_(True)))
    assert {p.id for p in rows} == {a.id, b.id}
    r = await client.post("/content/posts/bulk", headers=o["h"], json={"ids": [str(b.id)], "action": "evergreen_off"})
    assert r.json()["ok"] == 1
    assert (await get_post(o["id"], b.id)).evergreen is False
    out = (await client.get(f"/content/posts/{a.id}", headers=o["h"])).json()
    assert out["evergreen"] is True and any(x["action"] == "evergreen_on" for x in out["activities"])
