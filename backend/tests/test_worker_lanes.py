"""Worker safety: a job that hangs is stopped and retried (freeing its slot), and publishing / metrics have their
own lane so slow work can't hold them up."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

import app.modules.content  # noqa: F401  (registers job handlers)
from app.core.db import platform_session
from app.models import Job
from app.services import jobs
from tests.test_content import new_org


async def _job(org_id, kind, **kw):
    async with platform_session() as s:
        j = await jobs.enqueue(s, kind, org_id, kw.get("payload") or {})
        await s.commit()
        s.expunge(j)
        return j


async def test_hung_job_times_out_and_is_retried(client, monkeypatch):
    org = await new_org(client)

    async def hang(ctx):
        await asyncio.sleep(30)

    monkeypatch.setitem(jobs.HANDLERS, "test.hang", hang)
    monkeypatch.setitem(jobs.TIME_LIMITS, "test.hang", 0.2)
    j = await _job(org["id"], "test.hang")
    async with platform_session() as s:
        await s.execute(update(Job).where(Job.id == j.id).values(status="running", attempts=1))
        await s.commit()
    j.attempts = 1
    await asyncio.wait_for(jobs.Worker().run_job(j), timeout=5)
    async with platform_session() as s:
        row = await s.get(Job, j.id)
        assert row.status == "queued" and "without finishing" in row.error
        assert row.run_after > datetime.now(UTC) + timedelta(minutes=4)


async def test_fast_lane_only_claims_publishing_kinds(client):
    org = await new_org(client)
    async with platform_session() as s:  # keep other tests' leftovers out of the way
        await s.execute(update(Job).where(Job.status == "queued").values(run_after=datetime.now(UTC) + timedelta(days=1)))
        await s.commit()
    slow = await _job(org["id"], "siteaudit.run")
    fast = await _job(org["id"], "post.metrics", payload={"post_id": "00000000-0000-0000-0000-000000000000"})
    w = jobs.Worker()
    got = await w._claim(True)
    assert got is not None and got.id == fast.id
    got2 = await w._claim(False)
    assert got2 is not None and got2.id == slow.id
    assert await w._claim(True) is None


async def test_facebook_video_metrics_use_the_page_post_and_video_views(monkeypatch):
    import httpx

    from app.models import Post, SocialAccount
    from app.services.social import meta, net

    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        path = req.url.path
        if path.endswith("/555/video_insights"):
            return httpx.Response(200, json={"data": [{"name": "total_video_views", "values": [{"value": 42}]},
                                                      {"name": "total_video_impressions", "values": [{"value": 90}]}]})
        if path.endswith("/555"):
            return httpx.Response(200, json={"post_id": "777", "likes": {"summary": {"total_count": 3}},
                                             "comments": {"summary": {"total_count": 1}}})
        if path.endswith("/PAGE_777"):
            return httpx.Response(200, json={"shares": {"count": 2}, "reactions": {"summary": {"total_count": 5}},
                                             "comments": {"summary": {"total_count": 1}}})
        if path.endswith("/PAGE_777/insights"):
            return httpx.Response(200, json={"data": [{"name": "post_impressions", "values": [{"value": 120}]}]})
        return httpx.Response(400, json={"error": {"message": "(#100) Tried accessing nonexisting field (shares)"}})

    monkeypatch.setattr(net, "client", lambda timeout=60.0: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(meta, "account_tokens", lambda a: {"access_token": "t"})
    acc = SocialAccount(platform="facebook_page", external_id="PAGE", name="p", settings={})
    post = Post(platform="facebook_page", post_type="video", external_id="555",
                external_url="https://www.facebook.com/PAGE/videos/555")
    m = await meta.fetch_metrics(None, post, acc)
    assert m["likes"] == 5 and m["shares"] == 2 and m["video_views"] == 42 and m["impressions"] == 120
