"""Hands-off publishing: approving posts without a time, publishing held posts, and the blog autopilot."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant
from app.models import AnalyticsSnapshot, Job, Organization, Post, SeoArticle
from app.services import blog_autopilot
from app.services import content as svc
from tests.test_content import add, get_post, mk_account, mk_post, new_org


async def test_approving_a_post_without_a_time_picks_a_good_time(client):
    o = await new_org(client)
    acc = await mk_account(o["id"], platform="pinterest")
    p = await mk_post(o["id"], acc, status="in_review", text="A pin", scheduled_at=None)
    r = await client.post("/content/posts/bulk", headers=o["h"], json={"ids": [str(p.id)], "action": "approve"})
    assert r.status_code == 200, r.text
    got = await get_post(o["id"], p.id)
    now = datetime.now(UTC)
    assert got.status == "scheduled" and now < got.scheduled_at <= now + timedelta(days=3, hours=1)


async def test_publish_held_posts_setting(client):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    p = await mk_post(o["id"], acc, status="in_review", text="Same again",
                      meta={"similar_post_id": str(uuid.uuid4())}, qa={"passed": True, "score": 4.0})
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org, post = await s.get(Organization, o["id"]), await s.get(Post, p.id)
        assert len(svc.hold_reasons(post, org)) == 2
    r = await client.patch("/organization/publishing", headers=o["h"],
                           json={"auto_approve_posts": True, "publish_held_posts": True, "approve_waiting": True})
    assert r.status_code == 200 and r.json()["approved"] == 1 and r.json()["publish_held_posts"] is True
    assert (await get_post(o["id"], p.id)).status == "scheduled"
    # a post that breaks a platform rule still waits
    bad = await mk_post(o["id"], acc, status="in_review", text="x", qa={"passed": False})
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        assert svc.hold_reasons(await s.get(Post, bad.id), await s.get(Organization, o["id"])) == ["quality checks failed"]


async def test_blog_autopilot_is_due():
    cfg = {"enabled": True, "days": [0, 3], "time": "10:00", "last_run": None}
    ist = ZoneInfo("Asia/Kolkata")
    mon_9, mon_11 = datetime(2026, 10, 26, 9, 0, tzinfo=ist), datetime(2026, 10, 26, 11, 0, tzinfo=ist)
    assert not blog_autopilot.is_due(cfg, mon_9) and blog_autopilot.is_due(cfg, mon_11)
    assert not blog_autopilot.is_due({**cfg, "last_run": "2026-10-26"}, mon_11)
    assert not blog_autopilot.is_due(cfg, datetime(2026, 10, 27, 11, 0, tzinfo=ist))  # Tuesday
    assert not blog_autopilot.is_due({**cfg, "enabled": False}, mon_11)


async def test_blog_autopilot_writes_the_next_unused_keyword(client):
    o = await new_org(client)
    oid = o["id"]
    r = await client.patch("/organization/publishing", headers=o["h"],
                           json={"blog_autopilot": {"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6], "time": "00:00"}})
    assert r.status_code == 200 and r.json()["blog_autopilot"]["enabled"] is True
    await add(oid, AnalyticsSnapshot(organization_id=oid, source="seo_ideas", key="", data={"ideas": [
        {"keyword": "AI payroll India", "difficulty_guess": "high"},
        {"keyword": "HRMS for mid-size companies", "difficulty_guess": "low", "intent": "commercial"},
        {"keyword": "Leave policy template", "difficulty_guess": "low"}]}))
    await add(oid, SeoArticle(organization_id=oid, title="x", slug="x", keyword="hrms for mid size companies", outline=[]))

    out = await blog_autopilot.run_org(oid)
    assert out["keyword"] == "Leave policy template"  # low difficulty first, skipping the one already written
    assert (await blog_autopilot.run_org(oid))["created"] is None  # once a day
    async with SessionLocal() as s:
        set_tenant(s, oid)
        a = await s.get(SeoArticle, uuid.UUID(out["created"]))
        assert a.status == "queued"
        jobs = (await s.scalars(select(Job).where(Job.kind == "seo.write", Job.organization_id == oid))).all()
        assert [j.payload["article_id"] for j in jobs] == [out["created"]]
        org = await s.get(Organization, oid)
        assert org.publishing["blog_autopilot"]["last_run"]
    # saving the settings again keeps today's run
    await client.patch("/organization/publishing", headers=o["h"],
                       json={"blog_autopilot": {"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6], "time": "00:00"}})
    assert (await blog_autopilot.run_org(oid))["created"] is None
    r = await client.patch("/organization/publishing", headers=o["h"], json={"blog_autopilot": {"enabled": True, "days": []}})
    assert r.status_code == 400
