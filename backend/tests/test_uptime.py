"""Catching up after downtime: the watchdog notices sleep / app-off / no internet, pauses while offline, and jobs
that hit a dead connection wait instead of failing."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

import app.modules.content  # noqa: F401  (registers job handlers)
from app.core.db import platform_session
from app.models import DowntimePeriod, Job, SystemState
from app.services import jobs, uptime
from tests.test_content import new_org


async def _reset():
    async with platform_session() as s:
        await s.execute(delete(SystemState))
        await s.execute(delete(DowntimePeriod))
        await s.commit()


def _probe(monkeypatch, results):
    calls = iter(results)

    async def fake(timeout: float = 6.0):
        return next(calls, results[-1]), 0.5

    monkeypatch.setattr(uptime, "probe", fake)


async def _periods():
    async with platform_session() as s:
        return list((await s.scalars(select(DowntimePeriod).order_by(DowntimePeriod.started_at))).all())


async def test_startup_records_time_the_app_was_off(monkeypatch):
    await _reset()
    _probe(monkeypatch, [True])
    await uptime.set_state({"last_seen": (datetime.now(UTC) - timedelta(hours=8)).isoformat()})
    wd = uptime.Watchdog()
    await wd.startup()
    assert wd.online and wd.wake_seq >= 1
    (p,) = await _periods()
    assert p.reason == "stopped" and (p.ended_at - p.started_at) > timedelta(hours=7)
    st = await uptime.get_state()
    assert st["online"] is True and st["clock_skew_seconds"] == 0.5


async def test_sleep_and_offline_are_noticed_and_resumed(monkeypatch):
    await _reset()
    _probe(monkeypatch, [True])
    wd = uptime.Watchdog()
    await wd.startup()
    seq = wd.wake_seq
    # the laptop slept for two hours, and the network needs a moment after waking
    wd.last_tick = datetime.now(UTC) - timedelta(hours=2)
    _probe(monkeypatch, [False, False, True])
    await wd.tick()
    assert wd.paused and wd.wake_seq == seq + 1
    assert [p.reason for p in await _periods()] == ["asleep"]
    wd.last_tick = datetime.now(UTC) - timedelta(seconds=30)
    await wd.tick()
    assert wd.paused
    wd.offline_since = datetime.now(UTC) - timedelta(minutes=10)
    await wd.tick()
    assert not wd.paused and wd.wake_seq == seq + 2
    assert [p.reason for p in await _periods()] == ["asleep", "offline"]


async def test_job_waits_for_the_internet_without_using_an_attempt(monkeypatch):
    await _reset()
    calls = {"n": 0}

    @jobs.handler("test.needs_net")
    async def needs_net(ctx):
        calls["n"] += 1
        raise uptime.httpx.ConnectError("no route")

    w = jobs.Worker()
    uptime.WATCHDOG = w.watchdog
    try:
        _probe(monkeypatch, [False])
        async with platform_session() as s:
            job = await jobs.enqueue(s, "test.needs_net", None, {}, max_attempts=1)
            job.attempts, job.status = 1, "running"
            await s.commit()
            s.expunge(job)
        await w.run_job(job)
        async with platform_session() as s:
            j = await s.get(Job, job.id)
            assert j.status == "queued" and j.attempts == 0 and j.run_after > datetime.now(UTC)
            assert "internet" in (j.progress or "")
        # online again: a real failure fails normally
        w.watchdog.online = True
        _probe(monkeypatch, [True])
        job.attempts = 1
        await w.run_job(job)
        async with platform_session() as s:
            assert (await s.get(Job, job.id)).status == "failed"
    finally:
        uptime.WATCHDOG = None
        jobs.HANDLERS.pop("test.needs_net", None)


async def test_network_error_detection():
    assert uptime.is_network_error(uptime.httpx.ConnectError("x"))
    wrapped = RuntimeError("llm failed")
    wrapped.__cause__ = uptime.httpx.ConnectTimeout("t")
    assert uptime.is_network_error(wrapped)
    assert not uptime.is_network_error(ValueError("bad json"))
    assert uptime.publishing_settings(None) == {"catch_up_gap_minutes": 20, "wake_for_posts": False,
                                                "auto_approve_posts": False, "auto_publish_articles": False,
                                                "publish_held_posts": False,
                                                "blog_autopilot": {"enabled": False, "days": [0, 3], "time": "10:00"}}


async def test_status_endpoint_and_settings(client):
    org = await new_org(client)
    await _reset()
    await uptime.set_state({"last_seen": datetime.now(UTC).isoformat(), "online": False,
                            "offline_since": datetime.now(UTC).isoformat()})
    r = await client.get("/system/status", headers=org["h"])
    assert r.status_code == 200
    d = r.json()
    assert d["worker_running"] is True and d["online"] is False and d["catching_up"] == []
    r = await client.patch("/organization/publishing", headers=org["h"], json={"catch_up_gap_minutes": 15,
                                                                             "wake_for_posts": True})
    d = r.json()
    assert d["catch_up_gap_minutes"] == 15 and d["wake_for_posts"] is True and d["auto_approve_posts"] is False
    assert (await client.patch("/organization/publishing", headers=org["h"],
                               json={"catch_up_gap_minutes": 999})).status_code == 422


async def test_post_waits_while_public_address_is_down(client, monkeypatch):
    from tests.test_content import get_post, mk_account, mk_post

    org = await new_org(client)
    acc = await mk_account(org["id"], platform="instagram", name="IG")
    p = await mk_post(org["id"], acc, status="scheduled", post_type="image", text="hello",
                      media_ids=[str(uuid.uuid4())], scheduled_at=datetime.now(UTC))
    monkeypatch.setattr(uptime, "public_base", lambda: "https://studio.example.in")

    async def down(base=None, *, force=False):
        return False

    monkeypatch.setattr(uptime, "public_link_ok", down)
    async with platform_session() as s:
        job = await jobs.enqueue(s, "post.publish", org["id"], {"post_id": str(p.id)})
        job.attempts, job.status = 1, "running"
        await s.commit()
        s.expunge(job)
    await jobs.Worker().run_job(job)
    async with platform_session() as s:
        j = await s.get(Job, job.id)
        assert j.status == "queued" and j.attempts == 0
        assert (j.run_after - datetime.now(UTC)).total_seconds() > 200
    post = await get_post(org["id"], p.id)
    assert post.status == "publishing" and post.error is None and post.meta["waiting_for"] == "public_link"


async def test_instagram_fetch_hiccup_is_retried(monkeypatch):
    import httpx

    from app.services.social import common, meta

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(400, json={"error": {"message": "The media could not be fetched from this URI"}})
        return httpx.Response(200, json={"id": "c1"})

    async def nosleep(_):
        return None

    monkeypatch.setattr(common, "sleep", nosleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert (await meta._create_container(c, "ig", {"image_url": "u"}, "Instagram media"))["id"] == "c1"
    assert calls["n"] == 3


async def test_tunnel_is_restarted_when_public_address_stays_down(monkeypatch):
    await _reset()
    _probe(monkeypatch, [True])
    started = []

    class P:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            pass

    monkeypatch.setattr(uptime, "start_tunnel", lambda: started.append(1) or P())

    async def down(base=None, *, force=False):
        return False

    monkeypatch.setattr(uptime, "public_link_ok", down)
    wd = uptime.Watchdog()
    await wd.startup()
    for _ in range(2):                    # first failures: just note them (it may still be connecting)
        wd.last_public_check = None
        await wd.tick()
    assert started == [] and wd.public_ok is None
    wd.last_public_check = None
    await wd.tick()                       # third in a row: it's down; restart the tunnel
    assert started == [1] and wd.public_ok is False
    wd.last_public_check = None
    await wd.tick()                       # within the cooldown: leave it alone
    assert started == [1]


async def test_auto_approval_routes_and_waiting_posts(client):
    from app.services import content as svc
    from tests.test_content import get_post, mk_account, mk_post

    now = datetime.now(UTC)
    cfg = svc.GenSettings(mode="approve_first")
    p = svc.Post(mode="approve_first", scheduled_at=now + timedelta(hours=3), meta={})
    svc.route(p, cfg, hold_reason=None, now=now, auto_approve=True)
    assert p.status == "scheduled" and p.mode == "full_auto" and p.meta["auto_approved"]
    q = svc.Post(mode="approve_first", scheduled_at=None, meta={})
    svc.route(q, cfg, hold_reason=None, now=now, auto_approve=True)
    assert q.status == "scheduled" and q.scheduled_at == now + svc.AUTO_APPROVE_DELAY
    held = svc.Post(mode="approve_first", scheduled_at=now + timedelta(hours=3), meta={})
    svc.route(held, cfg, hold_reason="it is 93% similar to an earlier post", now=now, auto_approve=True)
    assert held.status == "in_review" and held.mode == "approve_first"
    manual = svc.Post(mode="manual", scheduled_at=None, meta={})
    svc.route(manual, cfg, hold_reason=None, now=now, auto_approve=True)
    assert manual.status == "draft"

    org = await new_org(client)
    acc = await mk_account(org["id"])
    good = await mk_post(org["id"], acc, status="in_review", text="fine", qa={"passed": True, "score": 8},
                         scheduled_at=now + timedelta(hours=2))
    timeless = await mk_post(org["id"], acc, status="in_review", text="no time", qa={"passed": True}, scheduled_at=None)
    similar = await mk_post(org["id"], acc, status="in_review", text="dup", qa={"passed": True},
                            meta={"similar_post_id": str(uuid.uuid4())})
    r = await client.get("/organization/publishing/waiting", headers=org["h"])
    assert r.json() == {"waiting": 3, "would_approve": 2}
    r = await client.patch("/organization/publishing", headers=org["h"],
                           json={"auto_approve_posts": True, "auto_publish_articles": True, "approve_waiting": True})
    d = r.json()
    assert d["auto_approve_posts"] and d["auto_publish_articles"] and d["approved"] == 2 and d["still_waiting"] == 1
    assert (await get_post(org["id"], good.id)).status == "scheduled"
    t = await get_post(org["id"], timeless.id)
    assert t.status == "scheduled" and t.scheduled_at > now
    assert (await get_post(org["id"], similar.id)).status == "in_review"


async def test_big_images_are_compressed_to_fit():
    import io
    import os

    from PIL import Image

    from app.services.social import common

    img = Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3))  # noise: a very large PNG
    buf = io.BytesIO()
    img.save(buf, "PNG")
    assert len(buf.getvalue()) > 1_000_000
    data, mime = common.fit_image(buf.getvalue(), "image/png", 1_000_000)
    assert len(data) <= 1_000_000 and mime == "image/jpeg"
    small = b"x" * 10
    assert common.fit_image(small, "image/png", 1_000_000) == (small, "image/png")
