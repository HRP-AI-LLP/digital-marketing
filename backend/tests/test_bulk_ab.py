"""Bulk CSV import (preview errors, commit, approval rules, image URLs), A/B tests (create, evaluate with fake
metrics, honest comparison, reuse the winner, AI variants) and hashtag analytics. External HTTP and the LLM are
faked; the database is real (RLS enforced)."""

import io
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import BrandProfile, ContentPillar, Experiment, MediaAsset, Organization, Post, PostMetric
from app.modules import experiments as exp_module
from app.services import bulk_import, crawler
from app.services import experiments as ex
from app.services import hashtag_stats
from app.services import reports as rep
from app.services.llm import router as llm_router
from tests.test_content import add, get_post, mk_account, mk_post, new_org


# ------------------------------------------------------------------ fixtures & helpers
@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def storage_env(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage"))
    yield
    s.storage_dir = old


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def csv_file(rows: list[list[str]], header: list[str] | None = None) -> list:
    import csv

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header or ["account", "date", "time", "text", "hashtags", "link", "image_url", "post_type", "pillar", "status"])
    w.writerows(rows)
    return [("file", ("posts.csv", buf.getvalue().encode(), "text/csv"))]


def future(days: int = 3) -> tuple[str, str]:
    d = (datetime.now(UTC) + timedelta(days=days)).date()
    return d.isoformat(), "10:30"


async def set_auto_approve(org_id, on: bool) -> None:
    async with platform_session() as s:
        org = await s.get(Organization, org_id)
        org.publishing = {**(org.publishing or {}), "auto_approve_posts": on}
        await s.commit()


async def count_posts(org_id) -> int:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return await s.scalar(select(func.count()).select_from(Post))


async def org_with_accounts(client):
    o = await new_org(client)
    li = await mk_account(o["id"], "linkedin_org", name="HRP page")
    xa = await mk_account(o["id"], "x", name="HRP X")
    await add(o["id"], ContentPillar(organization_id=o["id"], name="Tips & how-tos"))
    return o, li, xa


# ------------------------------------------------------------------ bulk import
async def test_template_and_preview_row_errors(client):
    o, li, xa = await org_with_accounts(client)
    r = await client.get("/content/import/template", headers=o["h"])
    assert r.status_code == 200
    t = r.json()
    assert t["csv"].splitlines()[0].startswith("account,date,time,text") and "HRP" in t["csv"]

    d, tm = future()
    past = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    rows = [
        ["HRP page", "", "", "A simple draft post", "#HR #Payroll", "", "", "", "Tips & how-tos", "draft"],  # 2 ok
        ["HRP page", past, "10:00", "Late review post", "", "", "", "", "", "review"],  # 3 past
        ["HRP X", d, tm, "x" * 300, "", "", "", "", "", "schedule"],  # 4 too long for X
        ["Nobody", d, tm, "Who is this", "", "", "", "", "", "draft"],  # 5 unknown account
        ["x", d, tm, "A document on X", "", "", "", "document", "", "draft"],  # 6 unsupported type (account by platform)
        ["HRP page", d, tm, "Status is odd", "", "", "", "", "", "maybe"],  # 7 bad status
        ["HRP X", d, "25:99", "Bad time", "#a #b #c", "", "", "", "Unknown pillar", "review"],  # 8 bad time + warnings
        ["HRP page", d, tm, "Image post without an image", "", "", "", "image", "", "review"],  # 9 needs media
        ["HRP page", d, tm, "", "", "", "not a url", "", "", "draft"],  # 10 bad image url + no text
        [str(li.id), d, tm, "Account by id, for review", "", "https://hrpai.in", "", "", "", "review"],  # 11 ok
    ]
    r = await client.post("/content/import/preview", headers=o["h"], files=csv_file(rows))
    assert r.status_code == 200, r.text
    p = r.json()
    by = {x["row"]: x for x in p["rows"]}
    assert by[2]["ok"] and by[2]["status"] == "draft" and by[2]["hashtags"] == ["HR", "Payroll"]
    assert any("past" in e for e in by[3]["errors"])
    assert any("Too long for X" in e for e in by[4]["errors"]) and by[4]["max_chars"] == 280
    assert any("No connected account" in e for e in by[5]["errors"])
    assert by[6]["account"]["id"] == str(xa.id) and any("doesn't support" in e for e in by[6]["errors"])
    assert any("isn't one of draft" in e for e in by[7]["errors"])
    assert any("isn't a time" in e for e in by[8]["errors"])
    assert any("hashtag" in w for w in by[8]["warnings"]) and any("Pillar" in w for w in by[8]["warnings"])
    assert any("needs media" in e for e in by[9]["errors"])
    assert any("isn't a web address" in e for e in by[10]["errors"])
    assert by[11]["ok"] and by[11]["outcome"].startswith("Waits for approval")
    assert p["ok"] == 2 and p["errors"] == 8

    # nothing is created while rows have problems ...
    r = await client.post("/content/import/commit", headers=o["h"], files=csv_file(rows))
    assert r.status_code == 400 and "problems" in r.json()["detail"]
    assert await count_posts(o["id"]) == 0
    # ... unless only the good rows are imported
    r = await client.post("/content/import/commit?skip_invalid=true", headers=o["h"], files=csv_file(rows))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 2 and res["drafts"] == 1 and res["waiting"] == 1 and 3 in res["skipped_rows"]
    draft = await get_post(o["id"], uuid.UUID(res["post_ids"][0]))
    assert draft.status == "draft" and draft.pillar_id is not None and draft.meta["source"] == "import"

    # files that can't be read at all
    r = await client.post("/content/import/preview", headers=o["h"],
                          files=[("file", ("p.csv", b"foo,bar\n1,2\n", "text/csv"))])
    assert r.status_code == 400 and "account" in r.json()["detail"]
    r = await client.post("/content/import/preview", headers=o["h"],
                          files=[("file", ("p.xlsx", b"PK\x03\x04junk", "application/octet-stream"))])
    assert r.status_code == 400 and "CSV" in r.json()["detail"]


async def test_import_approval_semantics(client):
    o, li, xa = await org_with_accounts(client)
    await add(o["id"], BrandProfile(organization_id=o["id"], banned_words=["guaranteed"]))
    d, tm = future(4)
    rows = [
        ["HRP page", d, tm, "Draft with a time", "", "", "", "", "", "draft"],
        ["HRP page", d, "11:00", "Please review this", "", "", "", "", "", "review"],
        ["HRP page", d, "12:00", "Schedule me please", "", "", "", "", "", "schedule"],
        ["HRP page", d, "13:00", "Results guaranteed for everyone", "", "", "", "", "", "schedule"],  # fails QA
    ]
    r = await client.post("/content/import/commit", headers=o["h"], files=csv_file(rows))
    assert r.status_code == 200, r.text
    posts = [await get_post(o["id"], uuid.UUID(i)) for i in r.json()["post_ids"]]
    assert [p.status for p in posts] == ["draft", "in_review", "in_review", "in_review"]
    assert posts[2].approval_deadline == posts[2].scheduled_at  # waits for approval (auto-approval is off)

    await set_auto_approve(o["id"], True)
    try:
        r = await client.post("/content/import/preview", headers=o["h"], files=csv_file(rows))
        assert r.json()["auto_approve"] and r.json()["rows"][2]["outcome"].startswith("Scheduled")
        r = await client.post("/content/import/commit", headers=o["h"], files=csv_file(rows))
        posts = [await get_post(o["id"], uuid.UUID(i)) for i in r.json()["post_ids"]]
        assert [p.status for p in posts] == ["draft", "in_review", "scheduled", "in_review"]
        assert posts[2].meta.get("auto_approved") and posts[2].approved_at is not None
        assert any("Held for approval" in w for w in posts[3].meta.get("warnings", []))
    finally:
        await set_auto_approve(o["id"], False)


async def test_import_images_by_url(client, monkeypatch):
    o, li, xa = await org_with_accounts(client)
    png = png_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/old.png":
            return httpx.Response(302, headers={"location": "/pic.png"})
        if req.url.path == "/pic.png":
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        if req.url.path == "/page.html":
            return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def public_dns(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(crawler, "resolve_host", public_dns)
    monkeypatch.setattr(bulk_import, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    d, tm = future()
    good = [["HRP page", d, tm, "Look at this picture", "", "", "https://cdn.example.com/old.png", "", "", "review"],
            ["HRP X", d, "11:00", "Same picture on X", "", "", "https://cdn.example.com/old.png", "", "", "draft"]]
    r = await client.post("/content/import/commit", headers=o["h"], files=csv_file(good))
    assert r.status_code == 200, r.text
    p1, p2 = [await get_post(o["id"], uuid.UUID(i)) for i in r.json()["post_ids"]]
    assert p1.post_type == "image" and len(p1.media_ids) == 1 and p1.media_ids == p2.media_ids  # downloaded once
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        a = await s.get(MediaAsset, uuid.UUID(p1.media_ids[0]))
        assert a.source == "import:url" and a.meta["source_url"].endswith("old.png") and a.width == 40
    before = await count_posts(o["id"])

    for url, msg in (("https://cdn.example.com/missing.png", "404"), ("https://cdn.example.com/page.html", "isn't a PNG"),
                     ("http://127.0.0.1/x.png", "private")):
        rows = [["HRP page", d, tm, "Fine row", "", "", "", "", "", "draft"],
                ["HRP page", d, "12:00", "Broken image", "", "", url, "", "", "draft"]]
        r = await client.post("/content/import/commit", headers=o["h"], files=csv_file(rows))
        assert r.status_code == 400 and "Row 3" in r.json()["detail"], r.text
        assert msg.lower() in r.json()["detail"].lower(), r.json()["detail"]
    assert await count_posts(o["id"]) == before  # one transaction: the fine rows weren't created either


# ------------------------------------------------------------------ A/B tests
async def test_compare_is_honest():
    rows = lambda a, b: [{"label": "A", **a}, {"label": "B", **b}]  # noqa: E731
    big = ex.compare("engagement_rate", rows({"value": 0.02, "impressions": 5000, "engagements": 100},
                                             {"value": 0.06, "impressions": 5000, "engagements": 300}))
    assert big.verdict == "clear" and big.leader == 1 and big.difference_pct == 200.0 and "not just chance" in big.message
    assert "significan" not in big.message.lower()
    small = ex.compare("engagement_rate", rows({"value": 0.05, "impressions": 40, "engagements": 2},
                                               {"value": 0.10, "impressions": 40, "engagements": 4}))
    assert small.verdict == "slight" and "hint" in small.message and "Very few" in small.message
    close = ex.compare("clicks", rows({"value": 100, "impressions": 900, "engagements": 0},
                                      {"value": 104, "impressions": 900, "engagements": 0}))
    assert close.verdict == "tie" and "too close" in close.message
    assert ex.compare("clicks", rows({"value": None, "impressions": 0, "engagements": 0},
                                     {"value": 3, "impressions": 0, "engagements": 0})).verdict == "no_data"
    assert ex.compare("impressions", rows({"value": 10, "impressions": 10, "engagements": 0},
                                          {"value": 10, "impressions": 10, "engagements": 0})).verdict == "tie"


async def _publish(org_id, pid, at, **metrics):
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        p = await s.get(Post, pid)
        p.status, p.published_at, p.scheduled_at = "published", at, at
        if metrics:
            s.add(PostMetric(organization_id=org_id, post_id=pid, raw={}, **metrics))
        await s.commit()


async def test_experiment_lifecycle(client):
    o, li, xa = await org_with_accounts(client)
    li2 = await mk_account(o["id"], "linkedin_org", name="HRP careers")
    first = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
    src = await mk_post(o["id"], li, status="draft", mode="manual", text="Original hook. Payroll in minutes.",
                        hashtags=["HR"], scheduled_at=first)

    # validation
    r = await client.post("/content/experiments", headers=o["h"], json={
        "mode": "accounts", "variants": [{"text": "One", "account_id": str(li.id)}, {"text": "Two", "account_id": str(li.id)}]})
    assert r.status_code == 400 and "own account" in r.json()["detail"]
    r = await client.post("/content/experiments", headers=o["h"], json={
        "mode": "accounts", "variants": [{"text": "One", "account_id": str(li.id)}, {"text": "Two", "account_id": str(xa.id)}]})
    assert r.status_code == 400 and "same platform" in r.json()["detail"]
    r = await client.post("/content/experiments", headers=o["h"], json={
        "source_post_id": str(src.id), "variants": [{"post_id": str(src.id)}, {"text": "Original hook. Payroll in minutes."}]})
    assert r.status_code == 400 and "different text" in r.json()["detail"]

    r = await client.post("/content/experiments", headers=o["h"], json={
        "name": "Hook test", "source_post_id": str(src.id), "metric": "engagement_rate", "mode": "time", "then": "review",
        "variants": [{"post_id": str(src.id), "kind": "original"},
                     {"text": "New hook: payroll in minutes.", "kind": "manual", "change": "New hook"}]})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["status"] == "running" and [v["label"] for v in e["variants"]] == ["A", "B"]
    a_id, b_id = e["variants"][0]["post_id"], e["variants"][1]["post_id"]
    assert a_id == str(src.id)
    pa, pb = await get_post(o["id"], uuid.UUID(a_id)), await get_post(o["id"], uuid.UUID(b_id))
    assert pb.scheduled_at - pa.scheduled_at == timedelta(days=7)  # same weekday and hour, a week later
    assert pa.status == pb.status == "in_review" and pb.hashtags == ["HR"] and pb.meta["experiment_label"] == "B"
    # a post can only be in one running test
    r = await client.post("/content/experiments", headers=o["h"], json={
        "variants": [{"post_id": a_id}, {"text": "Something else entirely"}]})
    assert r.status_code == 400 and "already part" in r.json()["detail"]

    # waiting → collecting → done
    r = await client.post(f"/content/experiments/{e['id']}/evaluate", headers=o["h"], json={})
    assert r.json()["result"]["state"] == "waiting" and "A, B" in r.json()["result"]["message"]
    now = datetime.now(UTC)
    await _publish(o["id"], uuid.UUID(a_id), now - timedelta(days=9), impressions=5000, likes=80, comments=10, shares=5, clicks=5)
    await _publish(o["id"], uuid.UUID(b_id), now - timedelta(days=2))
    r = await client.post(f"/content/experiments/{e['id']}/evaluate", headers=o["h"], json={})
    assert r.json()["result"]["state"] == "collecting" and "compared on" in r.json()["result"]["message"]
    r = await client.post(f"/content/experiments/{e['id']}/evaluate", headers=o["h"], json={"force": True})
    assert r.json()["status"] == "running" and "variant B" in r.json()["result"]["message"]  # no metrics for B yet
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        s.add(PostMetric(organization_id=o["id"], post_id=uuid.UUID(b_id), impressions=5000, likes=250, comments=30,
                         shares=10, clicks=10, raw={}))
        await s.commit()
    # the periodic task does the rest once the waiting period has passed
    assert await exp_module.run_once(now=now + timedelta(days=3)) >= 1
    r = await client.get(f"/content/experiments/{e['id']}", headers=o["h"])
    d = r.json()
    assert d["status"] == "done" and d["winner_post_id"] == b_id and d["result"]["verdict"] == "clear"
    assert d["result"]["leader"] == "B" and d["variants"][1]["is_winner"] and d["variants"][1]["metrics"]["engagement_rate"] == 0.06
    assert any("same weekday" in n for n in d["result"]["notes"])

    # reuse the winner as a new draft
    r = await client.post(f"/content/experiments/{e['id']}/reuse-winner", headers=o["h"], json={"account_id": str(li2.id)})
    assert r.status_code == 201, r.text
    new = await get_post(o["id"], uuid.UUID(r.json()["post_id"]))
    assert new.status == "draft" and new.text == "New hook: payroll in minutes." and new.social_account_id == li2.id

    # different accounts, small numbers → only a hint; cancelled and deleted tests
    r = await client.post("/content/experiments", headers=o["h"], json={
        "mode": "accounts", "metric": "clicks", "then": "draft",
        "variants": [{"text": "Try it today", "account_id": str(li.id)}, {"text": "Book a demo", "account_id": str(li2.id)}]})
    assert r.status_code == 201, r.text
    e2 = r.json()
    p1, p2 = (uuid.UUID(v["post_id"]) for v in e2["variants"])
    assert (await get_post(o["id"], p1)).status == "draft"
    await _publish(o["id"], p1, now - timedelta(days=5), impressions=50, clicks=2)
    await _publish(o["id"], p2, now - timedelta(days=5), impressions=60, clicks=4)
    r = await client.post(f"/content/experiments/{e2['id']}/evaluate", headers=o["h"], json={})
    d2 = r.json()
    assert d2["status"] == "done" and d2["result"]["verdict"] == "slight" and d2["winner_post_id"] == str(p2)
    assert any("own audience" in n for n in d2["result"]["notes"])

    r = await client.post("/content/experiments", headers=o["h"], json={
        "then": "draft", "variants": [{"text": "Alpha", "account_id": str(li.id)}, {"text": "Beta", "account_id": str(li.id)}]})
    e3 = r.json()
    r = await client.post(f"/content/experiments/{e3['id']}/cancel", headers=o["h"])
    assert r.json()["status"] == "cancelled"
    r = await client.post(f"/content/experiments/{e3['id']}/reuse-winner", headers=o["h"], json={})
    assert r.status_code == 400
    r = await client.delete(f"/content/experiments/{e3['id']}", headers=o["h"])
    assert r.status_code == 204
    items = (await client.get("/content/experiments", headers=o["h"])).json()["items"]
    assert {i["id"] for i in items} == {e["id"], e2["id"]}

    # a deleted variant ends a running test
    r = await client.post("/content/experiments", headers=o["h"], json={
        "then": "draft", "variants": [{"text": "Gamma", "account_id": str(li.id)}, {"text": "Delta", "account_id": str(li.id)}]})
    e4 = r.json()
    assert (await client.delete(f"/content/posts/{e4['variants'][1]['post_id']}", headers=o["h"])).status_code == 204
    r = await client.post(f"/content/experiments/{e4['id']}/evaluate", headers=o["h"], json={})
    assert r.json()["status"] == "done" and r.json()["result"]["state"] == "incomplete"

    # other organizations can't see the tests
    other = await new_org(client)
    assert (await client.get(f"/content/experiments/{e['id']}", headers=other["h"])).status_code == 404
    assert (await client.get("/content/experiments", headers=other["h"])).json()["items"] == []

    # the PDF report builds with this data
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        _, doc, _ = await rep.build(s, org, "experiments", {})
        assert any(b["type"] == "heading" and b.get("text") == "Hook test" for b in doc.blocks)
        assert "Hook test" in rep.render_html(doc, org, "data:image/png;base64,", "now")


async def test_ai_variants_drop_invented_details(client, monkeypatch):
    o, li, xa = await org_with_accounts(client)
    calls = []

    async def fake_complete_json(session, task, prompt, *, system=None, temperature=0.5, max_tokens=2500):
        calls.append((task, prompt, system))
        return {"variants": [
            {"text": "Payroll done in minutes, 50% faster than before.", "change": "Added a claim"},  # invented number
            {"text": "Tired of slow payroll? Run it in minutes. #HR", "change": "Question hook"},
            {"text": "Run payroll in minutes.", "change": "Same as original"},
        ]}

    monkeypatch.setattr(llm_router, "complete_json", fake_complete_json)
    r = await client.post("/content/experiments/suggest", headers=o["h"],
                          json={"text": "Run payroll in minutes.", "account_id": str(xa.id), "focus": "hook", "count": 2})
    assert r.status_code == 200, r.text
    vs = r.json()["variants"]
    assert [v["text"] for v in vs] == ["Tired of slow payroll? Run it in minutes."] and vs[0]["kind"] == "ai"
    task, prompt, system = calls[0]
    assert task == "write" and "Do not add new facts" in system and "X (Twitter)" in prompt

    async def only_invented(session, task, prompt, **kw):
        return {"variants": [{"text": "Trusted by 500 companies.", "change": "x"}]}

    monkeypatch.setattr(llm_router, "complete_json", only_invented)
    r = await client.post("/content/experiments/suggest", headers=o["h"],
                          json={"text": "Run payroll in minutes.", "account_id": str(xa.id)})
    assert r.status_code == 422 and "aren't in the original" in r.json()["detail"]


# ------------------------------------------------------------------ hashtag analytics
async def test_hashtag_stats(client):
    o, li, xa = await org_with_accounts(client)
    await add(o["id"], BrandProfile(organization_id=o["id"], hashtags=["#HRTech", "#Payroll"]))
    now = datetime.now(UTC)
    # LinkedIn: baseline 1000 impressions; #Payroll does well, #Boring does badly. X posts have their own baseline.
    spec = [  # (account, tags, impressions, engagements, days ago)
        (li, ["Payroll", "HR"], 1000, 60, 20), (li, ["Payroll"], 1000, 60, 15), (li, ["payroll", "HR"], 1000, 60, 3),
        (li, ["Boring", "HR"], 1000, 10, 18), (li, ["Boring"], 1000, 10, 10), (li, ["Boring", "HR"], 1000, 10, 2),
        (xa, ["Shiny"], 100, 30, 5), (xa, ["Plain"], 100, 2, 4),
    ]
    for acc, tags, imp, eng, ago in spec:
        p = await mk_post(o["id"], acc, status="published", hashtags=tags, text="t", published_at=now - timedelta(days=ago),
                          scheduled_at=now - timedelta(days=ago))
        await add(o["id"], PostMetric(organization_id=o["id"], post_id=p.id, impressions=imp, likes=eng, raw={}))
    await mk_post(o["id"], li, status="published", hashtags=["Payroll"], text="no metrics yet",
                  published_at=now - timedelta(days=1))
    await mk_post(o["id"], li, status="draft", hashtags=["Draft"], text="not published")

    r = await client.get("/analytics/hashtags", headers=o["h"])
    assert r.status_code == 200, r.text
    d = r.json()
    tags = {t["tag"].lower(): t for t in d["hashtags"]}
    assert "draft" not in tags and d["totals"]["posts"] == 9 and d["totals"]["posts_with_metrics"] == 8
    pay = tags["payroll"]
    assert pay["posts"] == 4 and pay["posts_with_metrics"] == 3 and pay["engagement_rate"] == 0.06
    # LinkedIn baseline rate = 210 / 6000 = 0.035 → payroll lift = 0.06/0.035 - 1
    assert pay["lift"] == pytest.approx(0.06 / 0.035 - 1, abs=1e-3) and pay["suggestion"] == "keep"
    assert tags["boring"]["suggestion"] == "drop" and tags["boring"]["lift"] < -0.5
    assert tags["hr"]["posts_with_metrics"] == 4
    assert tags["shiny"]["suggestion"] == "try" and tags["plain"]["suggestion"] == "wait"
    assert [t["tag"] for t in d["top"]][0] == "Payroll" and d["bottom"][0]["tag"] == "Boring"
    assert any(t["tag"] == "HRTech" and "brand kit" in t["reason"] for t in d["suggestions"]["try"])
    assert not any(t["tag"].lower() == "payroll" for t in d["suggestions"]["try"])
    assert tags["boring"]["trend"] is None  # all posts fall in the second half of the default 90 days
    s21 = (now - timedelta(days=21)).date().isoformat()
    t21 = {t["tag"]: t for t in (await client.get(f"/analytics/hashtags?start={s21}", headers=o["h"])).json()["hashtags"]}
    assert t21["Boring"]["trend"] == "flat" and t21["Payroll"]["trend_detail"]["second_half_posts"] == 1

    r = await client.get("/analytics/hashtags?platform=x", headers=o["h"])
    d = r.json()
    assert {t["tag"] for t in d["hashtags"]} == {"Shiny", "Plain"} and d["platform_label"] == "X (Twitter)"
    start = (now - timedelta(days=7)).date().isoformat()
    d = (await client.get(f"/analytics/hashtags?start={start}", headers=o["h"])).json()
    assert d["start"] == start and "payroll" in {t["tag"].lower() for t in d["hashtags"]} and d["totals"]["posts"] == 5
    assert (await client.get("/analytics/hashtags?platform=nope", headers=o["h"])).status_code == 400

    empty = await new_org(client)
    d = (await client.get("/analytics/hashtags", headers=empty["h"])).json()
    assert d["hashtags"] == [] and "No published posts" in d["note"]

    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        _, doc, _ = await rep.build(s, org, "hashtags", {})
        html = rep.render_html(doc, org, "data:image/png;base64,", "now")
        assert "#Payroll" in html and "#Boring" in html
        stats = await hashtag_stats.hashtag_stats(s, start=(now - timedelta(days=30)).date(), end=now.date(),
                                                  tz=UTC, platform="linkedin_org")
        assert {t["tag"] for t in stats["hashtags"]} == {"Payroll", "HR", "Boring"}


async def test_permissions(client):
    o, li, xa = await org_with_accounts(client)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        n = await s.scalar(select(func.count()).select_from(Experiment))
    assert n == 0
    r = await client.post("/content/import/preview", files=csv_file([["HRP page", "", "", "x", "", "", "", "", "", ""]]))
    assert r.status_code == 401
