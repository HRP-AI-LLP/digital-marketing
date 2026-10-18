"""Catching up after downtime: the watchdog notices sleep / app-off / no internet, pauses while offline, and jobs
that hit a dead connection wait instead of failing."""

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
    assert uptime.publishing_settings(None) == {"catch_up_gap_minutes": 20, "wake_for_posts": False}


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
    assert r.json() == {"catch_up_gap_minutes": 15, "wake_for_posts": True}
    assert (await client.patch("/organization/publishing", headers=org["h"],
                               json={"catch_up_gap_minutes": 999})).status_code == 422
