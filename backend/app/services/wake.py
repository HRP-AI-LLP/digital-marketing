"""Cloud wake-ups: run the job runner exactly when something is due, without a tick every minute.

Cloud Scheduler calls POST /api/v1/internal/tick every TICK_EVERY_MINUTES (20 minutes) for the periodic work. In between,
this module books one-off Cloud Tasks that call the same endpoint:
  - right away, when someone queues work in the app (generate a post, render a video, publish now);
  - at the exact time of the next scheduled post or the next queued job (a retry, a catch-up slot), when that comes
    before the next regular tick.
So posts go out on the minute, the database (Neon) can sleep in between, and Cloud Run is only billed while working.

Task names are the target time rounded to 15 seconds, so asking twice for the same moment books one task.
Locally (DEPLOY_MODE=local) or without TASKS_QUEUE everything here does nothing.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select

from app.core.config import get_settings

log = logging.getLogger("wake")
_META = "http://metadata.google.internal/computeMetadata/v1"
_META_H = {"Metadata-Flavor": "Google"}
_cache: dict = {}
_booked: dict[int, float] = {}  # bucket -> when booked (this instance)
BUCKET = 15
# set by jobs.enqueue during a request, so the API books a wake-up after the request
queued_in_request: contextvars.ContextVar[list | None] = contextvars.ContextVar("dm_queued", default=None)


def note_queued() -> None:
    box = queued_in_request.get()
    if box is not None:
        box.append(1)


def enabled() -> bool:
    s = get_settings()
    return bool(s.cloud and s.tasks_queue and s.cron_secret and s.automation_enabled)


async def _meta(path: str) -> str:
    async with httpx.AsyncClient(timeout=3) as c:
        r = await c.get(f"{_META}/{path}", headers=_META_H)
        r.raise_for_status()
        return r.text


async def _token() -> str:
    tok = _cache.get("token")
    if tok and tok[1] > time.time() + 60:
        return tok[0]
    async with httpx.AsyncClient(timeout=3) as c:
        r = await c.get(f"{_META}/instance/service-accounts/default/token", headers=_META_H)
        r.raise_for_status()
        d = r.json()
    _cache["token"] = (d["access_token"], time.time() + int(d.get("expires_in", 300)))
    return d["access_token"]


async def _where() -> tuple[str, str, str]:
    """(project id, region, this service's run.app URL)."""
    if "where" not in _cache:
        s = get_settings()
        project = s.gcp_project or await _meta("project/project-id")
        number = await _meta("project/numeric-project-id")
        region = s.gcp_region or (await _meta("instance/region")).rsplit("/", 1)[-1]
        service = os.environ.get("K_SERVICE", "")
        url = s.tick_url or f"https://{service}-{number}.{region}.run.app"
        _cache["where"] = (project, region, url.rstrip("/"))
    return _cache["where"]


async def book(at: datetime | None = None) -> bool:
    """Book a tick for `at` (now when None). Returns True when a task was created (or already existed)."""
    if not enabled():
        return False
    now = datetime.now(UTC)
    at = max(at or now, now)
    bucket = int(at.timestamp()) // BUCKET
    if bucket in _booked:
        return True
    s = get_settings()
    try:
        project, region, url = await _where()
        name = f"projects/{project}/locations/{region}/queues/{s.tasks_queue}/tasks/tick-{bucket}"
        body = {"task": {
            "name": name,
            "scheduleTime": datetime.fromtimestamp(bucket * BUCKET + (BUCKET if at > now else 0), UTC)
                                    .isoformat().replace("+00:00", "Z"),
            "dispatchDeadline": "1800s",
            "httpRequest": {"httpMethod": "POST", "url": f"{url}/api/v1/internal/tick",
                            "headers": {"X-Cron-Secret": s.cron_secret, "X-Origin-Verify": s.origin_verify_secret,
                                        "X-Wake": "task"}},
        }}
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"https://cloudtasks.googleapis.com/v2/projects/{project}/locations/{region}/queues/"
                             f"{s.tasks_queue}/tasks", json=body, headers={"Authorization": f"Bearer {await _token()}"})
        if r.status_code not in (200, 409):  # 409: this moment is already booked (by another instance)
            log.warning("couldn't book a wake-up at %s: %s %s", at.isoformat(), r.status_code, r.text[:300])
            return False
    except Exception:  # noqa: BLE001 - the regular tick still runs everything, just later
        log.warning("couldn't book a wake-up", exc_info=True)
        return False
    for b in [b for b in _booked if b < bucket - 400]:
        _booked.pop(b, None)
    _booked[bucket] = time.time()
    return True


async def next_due(horizon: timedelta) -> datetime | None:
    """The earliest queued job or scheduled post due within `horizon` (now if something is due already)."""
    from app.core.db import platform_session
    from app.models import Job, Post

    now = datetime.now(UTC)
    until = now + horizon
    async with platform_session() as s:
        job = await s.scalar(select(func.min(Job.run_after)).where(Job.status == "queued", Job.run_after <= until))
        # only posts still ahead: past-due ones are dispatched (or waiting their catch-up slot as a queued job)
        post = await s.scalar(select(func.min(Post.scheduled_at)).where(
            Post.status.in_(["scheduled", "approved"]), Post.scheduled_at > now, Post.scheduled_at <= until))
    times = [t for t in (job, post) if t is not None]
    return min(times) if times else None


def horizon() -> timedelta:
    """How far ahead to look: until the next regular tick (plus a little)."""
    return timedelta(minutes=get_settings().tick_every_minutes + 1)


async def plan(not_before: timedelta | None = None) -> datetime | None:
    """Book a wake-up for whatever comes due before the next regular tick. Returns the booked time.
    not_before: never sooner than this from now (after a tick that found nothing it could run)."""
    if not enabled():
        return None
    try:
        at = await next_due(horizon())
    except Exception:  # noqa: BLE001
        log.warning("couldn't look up the next due work", exc_info=True)
        return None
    if at is None:
        return None
    now = datetime.now(UTC)
    if at > now:
        at += timedelta(seconds=2)  # scheduled posts: be a moment after, never before, their time
    if not_before is not None:
        at = max(at, now + not_before)
    return at if await book(at) else None
