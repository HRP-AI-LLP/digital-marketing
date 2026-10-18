"""Postgres-backed background jobs and periodic tasks.

- `enqueue(...)` adds a job in the caller's transaction.
- Modules register handlers with `@handler("kind")` and periodic tasks with `@periodic(seconds)`.
- `Worker` claims jobs with SELECT ... FOR UPDATE SKIP LOCKED, runs each handler inside a
  session scoped to the job's organization (RLS applies), retries with backoff, and runs periodic
  tasks. Start it with `python -m app.worker` (start.ps1 does this).
"""

import asyncio
import logging
import os
import socket
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import Job

log = logging.getLogger("worker")


@dataclass
class JobContext:
    job_id: uuid.UUID
    kind: str
    org_id: uuid.UUID | None
    payload: dict
    session: AsyncSession  # scoped to org_id (or bypass for platform jobs)
    attempt: int

    async def progress(self, message: str) -> None:
        async with platform_session() as s:
            await s.execute(update(Job).where(Job.id == self.job_id).values(progress=message[:500], locked_at=datetime.now(UTC)))
            await s.commit()


Handler = Callable[[JobContext], Awaitable[dict | None]]
HANDLERS: dict[str, Handler] = {}
PERIODIC: list[tuple[str, int, Callable[[], Awaitable[None]]]] = []


def handler(kind: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


def periodic(seconds: int, name: str | None = None, *, offline_ok: bool = False):
    """Run `fn` every `seconds` (by the wall clock, so a sleeping laptop catches up on waking).
    Tasks pause while there's no internet unless offline_ok=True."""
    def deco(fn):
        fn._offline_ok = offline_ok
        PERIODIC.append((name or fn.__name__, seconds, fn))
        return fn
    return deco


class PermanentError(Exception):
    """Raise from a handler to fail the job without retrying."""


class WaitForNetwork(Exception):
    """Raise from a handler when the internet is down: the job waits and runs again once the connection is
    back, without using up an attempt."""


async def offline_now(e: BaseException | None = None) -> bool:
    """In the worker: True when `e` (if given) is a network failure and the internet is confirmed down."""
    from app.services import uptime

    if uptime.WATCHDOG is None or (e is not None and not uptime.is_network_error(e)):
        return False
    return await uptime.WATCHDOG.network_failed()


async def enqueue(
    session: AsyncSession,
    kind: str,
    org_id: uuid.UUID | None,
    payload: dict | None = None,
    *,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
    priority: int = 5,
    max_attempts: int = 3,
    created_by: uuid.UUID | None = None,
) -> Job:
    if dedupe_key:
        existing = await session.scalar(
            select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(["queued", "running"]))
        )
        if existing:
            return existing
    job = Job(
        organization_id=org_id, kind=kind, payload=payload or {}, run_after=run_after or datetime.now(UTC),
        dedupe_key=dedupe_key, priority=priority, max_attempts=max_attempts, created_by=created_by,
    )
    session.add(job)
    await session.flush()
    return job


async def enqueue_now(kind: str, org_id: uuid.UUID | None, payload: dict | None = None, **kw) -> uuid.UUID:
    """Enqueue in its own committed transaction (for use outside request sessions)."""
    async with platform_session() as s:
        job = await enqueue(s, kind, org_id, payload, **kw)
        await s.commit()
        return job.id


async def org_ids_active() -> list[uuid.UUID]:
    from app.models import Organization

    async with platform_session() as s:
        return list((await s.scalars(select(Organization.id).where(Organization.status == "active"))).all())


class Worker:
    def __init__(self, concurrency: int = 3, poll_seconds: float = 2.0):
        from app.services import uptime

        self.watchdog = uptime.Watchdog()  # becomes uptime.WATCHDOG once run() starts
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        self.name = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task] = set()

    def stop(self) -> None:
        self._stop.set()

    async def _recover_stale(self, startup: bool = False) -> None:
        """Requeue jobs whose worker died. At startup every job locked by this machine is orphaned
        (one worker per laptop); later, anything running for over 30 minutes is.
        Interrupted publish jobs are NOT requeued: the post may already be live, so they fail with
        a message asking a person to check the platform."""
        from app.models import Post

        host = socket.gethostname()
        async with platform_session() as s:
            cond = Job.locked_at < datetime.now(UTC) - timedelta(minutes=30)
            if startup:
                cond = cond | Job.locked_by.startswith(f"{host}:")
            stale = (await s.scalars(select(Job).where(Job.status == "running", cond))).all()
            for j in stale:
                if j.kind == "post.publish":
                    j.status, j.finished_at = "failed", datetime.now(UTC)
                    j.error = "Interrupted while publishing (the app stopped)."
                    pid = (j.payload or {}).get("post_id")
                    if pid:
                        post = await s.get(Post, uuid.UUID(str(pid)))
                        if post is not None and post.status == "publishing":
                            post.status = "failed"
                            post.error = ("The app stopped while publishing this post. Check the platform: if the post "
                                          "isn't there, press Publish now.")
                else:
                    j.status, j.locked_at, j.locked_by = "queued", None, None
            await s.commit()
            if stale:
                log.info("recovered %d interrupted job(s)", len(stale))

    async def _claim(self) -> Job | None:
        async with platform_session() as s:
            row = await s.execute(
                text(
                    "SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now() "
                    "ORDER BY priority, run_after LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
            )
            job_id = row.scalar()
            if job_id is None:
                await s.rollback()
                return None
            job = await s.get(Job, job_id)
            job.status, job.locked_at, job.locked_by = "running", datetime.now(UTC), self.name
            job.attempts += 1
            await s.commit()
            return job

    async def _finish(self, job_id: uuid.UUID, **values) -> None:
        async with platform_session() as s:
            await s.execute(update(Job).where(Job.id == job_id).values(**values))
            await s.commit()

    async def run_job(self, job: Job) -> None:
        fn = HANDLERS.get(job.kind)
        if fn is None:
            await self._finish(job.id, status="failed", error=f"No handler for {job.kind}", finished_at=datetime.now(UTC))
            return
        try:
            async with SessionLocal() as session:
                if job.organization_id:
                    set_tenant(session, job.organization_id)
                else:
                    session.info["bypass_rls"] = True
                ctx = JobContext(job.id, job.kind, job.organization_id, job.payload or {}, session, job.attempts)
                result = await fn(ctx)
                await session.commit()
            await self._finish(job.id, status="done", result=result or {}, error=None, finished_at=datetime.now(UTC), progress=None)
        except WaitForNetwork:
            await self._finish(job.id, status="queued", attempts=max(0, job.attempts - 1), locked_at=None,
                               locked_by=None, run_after=datetime.now(UTC) + timedelta(seconds=60),
                               progress="Waiting for the internet connection")
        except Exception as e:  # noqa: BLE001
            if not isinstance(e, PermanentError) and await offline_now(e):
                await self._finish(job.id, status="queued", attempts=max(0, job.attempts - 1), locked_at=None,
                                   locked_by=None, run_after=datetime.now(UTC) + timedelta(seconds=60),
                                   progress="Waiting for the internet connection")
                return
            permanent = isinstance(e, PermanentError) or job.attempts >= job.max_attempts
            err = f"{type(e).__name__}: {e}"
            log.warning("job %s %s failed (attempt %s): %s", job.kind, job.id, job.attempts, err)
            log.debug(traceback.format_exc())
            if permanent:
                await self._finish(job.id, status="failed", error=err[:4000], finished_at=datetime.now(UTC))
                await _on_job_failed(job, err)
            else:
                backoff = timedelta(seconds=60 * (2 ** (job.attempts - 1)))
                await self._finish(job.id, status="queued", error=err[:4000], run_after=datetime.now(UTC) + backoff,
                                   locked_at=None, locked_by=None)

    async def _wait(self, timeout: float) -> None:
        """Sleep up to `timeout` s; returns early on stop or when the watchdog reports a wake-up."""
        stop = asyncio.create_task(self._stop.wait())
        woken = asyncio.create_task(self.watchdog.wait_woken(timeout))
        try:
            await asyncio.wait({stop, woken}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (stop, woken):
                t.cancel()

    async def _periodic_loop(self, name: str, seconds: int, fn, index: int = 0) -> None:
        """Runs by the wall clock in short naps, so a task that fell due while the laptop slept runs on waking.
        After any downtime every task runs at once (spread a couple of seconds apart, to be gentle)."""
        await asyncio.sleep(3 + index * 0.3)
        offline_ok = getattr(fn, "_offline_ok", False)
        seq = self.watchdog.wake_seq
        next_due = datetime.now(UTC)
        while not self._stop.is_set():
            try:
                await self.watchdog.ensure_fresh()
            except Exception:  # noqa: BLE001
                log.exception("watchdog check failed")
            if self.watchdog.wake_seq != seq:
                seq = self.watchdog.wake_seq
                next_due = min(next_due, datetime.now(UTC) + timedelta(seconds=index * 2))
            now = datetime.now(UTC)
            if now >= next_due and (offline_ok or not self.watchdog.paused):
                try:
                    await fn()
                except Exception:  # noqa: BLE001
                    log.exception("periodic task %s failed", name)
                next_due = datetime.now(UTC) + timedelta(seconds=seconds)
            remaining = (next_due - datetime.now(UTC)).total_seconds()
            await self._wait(max(1.0, min(30.0, remaining)))

    async def _watchdog_loop(self) -> None:
        from app.services.uptime import TICK_SECONDS

        n = 0
        while not self._stop.is_set():
            try:
                await self.watchdog.tick()
                if n % 3 == 0:
                    await self.watchdog.manage_power()
            except Exception:  # noqa: BLE001
                log.exception("watchdog tick failed")
            n += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except TimeoutError:
                pass

    async def _recovery_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=300)
            except TimeoutError:
                pass
            try:
                await self._recover_stale()
            except Exception:  # noqa: BLE001
                log.exception("stale job recovery failed")

    async def run(self) -> None:
        from app.services import uptime

        uptime.WATCHDOG = self.watchdog
        await self._recover_stale(startup=True)
        try:
            await self.watchdog.startup()
        except Exception:  # noqa: BLE001
            log.exception("watchdog start-up failed")
            self.watchdog.online = True
        self._tasks.add(asyncio.create_task(self._recovery_loop()))
        self._tasks.add(asyncio.create_task(self._watchdog_loop()))
        log.info("worker %s started with %d handlers, %d periodic tasks", self.name, len(HANDLERS), len(PERIODIC))
        for i, (name, seconds, fn) in enumerate(PERIODIC):
            self._tasks.add(asyncio.create_task(self._periodic_loop(name, seconds, fn, i)))
        while not self._stop.is_set():
            await self._sem.acquire()
            try:
                await self.watchdog.ensure_fresh()
            except Exception:  # noqa: BLE001
                log.exception("watchdog check failed")
            if self.watchdog.paused:  # no internet: claim nothing, so nothing fails or wastes a retry
                self._sem.release()
                await self._wait(self.poll_seconds * 5)
                continue
            try:
                job = await self._claim()
            except Exception:  # noqa: BLE001
                log.exception("claim failed")
                job = None
            if job is None:
                self._sem.release()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
                continue

            async def _go(j=job):
                try:
                    await self.run_job(j)
                finally:
                    self._sem.release()

            t = asyncio.create_task(_go())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        for t in list(self._tasks):
            t.cancel()


async def _on_job_failed(job: Job, err: str) -> None:
    """Hook: notify admins about failed jobs that matter (publishing, generation)."""
    try:
        from app.services.notify import notify_org

        if job.organization_id and job.kind in {"post.publish", "post.generate", "intel.crawl", "seo.write"}:
            await notify_org(
                job.organization_id, "job_failed", f"Background task failed: {job.kind}", err[:500],
                link="/settings/activity", permission="content.view",
            )
    except Exception:  # noqa: BLE001
        log.exception("failed to notify about job failure")


async def run_inline(kind: str, org_id: uuid.UUID | None, payload: dict) -> dict | None:
    """Run a handler immediately (used by tests and 'run now' buttons that wait)."""
    fn = HANDLERS[kind]
    async with SessionLocal() as session:
        if org_id:
            set_tenant(session, org_id)
        else:
            session.info["bypass_rls"] = True
        ctx = JobContext(uuid.uuid4(), kind, org_id, payload, session, 1)
        result = await fn(ctx)
        await session.commit()
        return result
