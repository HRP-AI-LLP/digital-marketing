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


# Jobs that must never wait behind slow work: they get their own lane (see Worker._lane).
FAST_KINDS = frozenset({"post.publish", "post.metrics", "account.refresh", "webhook.deliver", "security.signin_notice",
                        "inbox.sync"})
# How long a job may run before it's stopped and retried (seconds). A hung network call or renderer can
# otherwise hold a worker slot for ever.
TIME_LIMITS = {
    "post.metrics": 180, "account.refresh": 180, "webhook.deliver": 120, "security.signin_notice": 120, "inbox.sync": 600,
    "post.publish": 1800, "post.generate": 3600, "media.generate": 3600,
    "siteaudit.run": 3 * 3600, "intel.crawl": 3 * 3600, "intel.competitor_crawl": 3 * 3600, "intel.run_all": 3 * 3600,
    "aivis.run": 2 * 3600, "research.run": 2 * 3600, "keywords.rank_check": 2 * 3600,
}
DEFAULT_TIME_LIMIT = 45 * 60


def time_limit(kind: str) -> int:
    return TIME_LIMITS.get(kind, DEFAULT_TIME_LIMIT)


class JobTimeout(Exception):
    pass


async def _publish_interrupted(job, message: str) -> None:
    from app.models import Post

    pid = (job.payload or {}).get("post_id")
    if not pid:
        return
    async with platform_session() as s:
        post = await s.get(Post, uuid.UUID(str(pid)))
        if post is not None and post.status == "publishing":
            post.status, post.error = "failed", message
            await s.commit()


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
    """Raise from a handler when the internet (or our public address) is down: the job waits and runs again
    later, without using up an attempt."""

    def __init__(self, delay: int = 60, reason: str = "Waiting for the internet connection"):
        super().__init__(reason)
        self.delay, self.reason = delay, reason


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
    from app.services import wake

    wake.note_queued()  # cloud: the API books a wake-up once the request is done
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


_PERIODIC_SAVED: dict[str, datetime] = {}


async def record_periodic(name: str, seconds: int, ok: bool, error: str | None, *, force: bool = False) -> None:
    """Remember when each periodic task last ran (for the system map). Saved at most once a minute per task,
    and always when it fails. Never raises."""
    from app.models import SystemState

    now = datetime.now(UTC)
    last = _PERIODIC_SAVED.get(name)
    if ok and last and (now - last).total_seconds() < 60 and not force:
        return
    _PERIODIC_SAVED[name] = now
    try:
        async with platform_session() as s:
            key = f"p:{name}"[:40]
            row = await s.get(SystemState, key)
            prev = dict(row.value or {}) if row else {}
            value = {"name": name, "last_run": now.isoformat(), "ok": ok, "error": error, "every": seconds,
                     "last_ok": now.isoformat() if ok else prev.get("last_ok")}
            if row is None:
                s.add(SystemState(key=key, value=value))
            else:
                row.value = value
            await s.commit()
    except Exception:  # noqa: BLE001
        log.debug("could not record periodic run for %s", name, exc_info=True)


# Periodic tasks that make no sense in the cloud (the database host keeps its own backups).
def cloud_skip() -> set[str]:
    """Periodic tasks the cloud doesn't run. The nightly backup is off unless CLOUD_NIGHTLY_BACKUP=true (then it goes
    to R2); "Back up now" on Platform -> Backups works either way."""
    from app.core.config import get_settings

    return set() if get_settings().cloud_nightly_backup else {"backups.daily"}


async def _run_periodic_if_due(name: str, seconds: int, fn, now: datetime) -> bool:
    """Cloud tick: run a periodic task when its interval has passed since the last run recorded in the database,
    holding an advisory lock so overlapping ticks never run it twice."""
    from app.models import SystemState

    async with platform_session() as s:
        row = await s.get(SystemState, f"p:{name}"[:40])
        last = None
        if row and (row.value or {}).get("last_run"):
            try:
                last = datetime.fromisoformat(row.value["last_run"])
            except ValueError:
                last = None
        # every-minute tasks (the post dispatcher, Telegram polling) run on every tick: a wake-up at a post's time
        # must dispatch it even if the previous tick was moments ago
        if seconds > 60 and last is not None and (now - last).total_seconds() < seconds - 5:
            return False
        got = await s.scalar(text("SELECT pg_try_advisory_lock(hashtext(:n))"), {"n": f"periodic:{name}"})
        if not got:
            return False
        ok, err = True, None
        try:
            await asyncio.wait_for(fn(), timeout=max(60, min(seconds, 600)))
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"{type(e).__name__}: {e}"[:300]
            log.exception("periodic task %s failed", name)
        finally:
            await record_periodic(name, seconds, ok, err, force=True)
            await s.execute(text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": f"periodic:{name}"})
            await s.commit()
    return True


_TICK_WORKER: "Worker | None" = None


def tick_worker() -> "Worker":
    global _TICK_WORKER
    if _TICK_WORKER is None:
        _TICK_WORKER = Worker()
    return _TICK_WORKER


class Worker:
    def __init__(self, concurrency: int = 3, poll_seconds: float = 2.0):
        from app.services import uptime

        self.watchdog = uptime.Watchdog()  # becomes uptime.WATCHDOG once run() starts
        self.concurrency = concurrency
        self.poll_seconds = poll_seconds
        self.name = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(concurrency)
        self._fast_sem = asyncio.Semaphore(2)
        self._done_ids: list[uuid.UUID] = []
        self._tasks: set[asyncio.Task] = set()

    def stop(self) -> None:
        self._stop.set()

    async def _lane(self, fast: bool, sem: asyncio.Semaphore, until: datetime | None = None,
                    track: set | None = None) -> None:
        while not self._stop.is_set():
            if until is not None and datetime.now(UTC) >= until:
                return  # tick mode: stop claiming; jobs already started finish on their own
            await sem.acquire()
            try:
                await self.watchdog.ensure_fresh()
            except Exception:  # noqa: BLE001
                log.exception("watchdog check failed")
            if self.watchdog.paused:  # no internet: claim nothing, so nothing fails or wastes a retry
                sem.release()
                await self._wait(self.poll_seconds * 5)
                continue
            try:
                job = await self._claim(fast)
            except Exception:  # noqa: BLE001
                log.exception("claim failed")
                job = None
            if job is None:
                sem.release()
                if until is not None and not any(not t.done() for t in (track or ())):
                    return  # tick mode, nothing queued and nothing of ours still running: end early (pay per second)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
                continue

            async def _go(j=job, sm=sem):
                try:
                    await self.run_job(j)
                finally:
                    sm.release()

            t = asyncio.create_task(_go())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
            if track is not None:
                track.add(t)

    async def _recover_stale(self, startup: bool = False) -> None:
        """Requeue jobs whose worker died. At startup every job locked by this machine is orphaned
        (one worker per laptop); later, anything running for over 30 minutes is.
        Interrupted publish jobs are NOT requeued: the post may already be live, so they fail with
        a message asking a person to check the platform."""
        from app.models import Post

        host = socket.gethostname()
        async with platform_session() as s:
            now = datetime.now(UTC)
            cond = Job.locked_at < now - timedelta(minutes=30)
            if startup:
                cond = cond | Job.locked_by.startswith(f"{host}:")
            rows = (await s.scalars(select(Job).where(Job.status == "running", cond))).all()
            # a job is only stale once it has run past its own time limit (plus a margin) or its machine restarted
            stale = [j for j in rows if (startup and (j.locked_by or "").startswith(f"{host}:"))
                     or (j.locked_at and now - j.locked_at > timedelta(seconds=time_limit(j.kind) + 600))]
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

    async def _claim(self, fast: bool | None = None) -> Job | None:
        """Next due job. fast=True: only FAST_KINDS (publishing lane); False: everything else; None: any."""
        lane = ""
        if fast is not None:
            lane = f" AND kind {'=' if fast else '<>'} ANY(:fast)"
        async with platform_session() as s:
            row = await s.execute(
                text(
                    "SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now()" + lane +
                    " ORDER BY priority, run_after LIMIT 1 FOR UPDATE SKIP LOCKED"
                ),
                {"fast": list(FAST_KINDS)} if fast is not None else {},
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
                try:
                    result = await asyncio.wait_for(fn(ctx), timeout=time_limit(job.kind))
                except TimeoutError as e:
                    raise JobTimeout(f"Stopped after {time_limit(job.kind) // 60} min without finishing (a service didn't "
                                     "answer). It will be retried.") from e
                await session.commit()
            await self._finish(job.id, status="done", result=result or {}, error=None, finished_at=datetime.now(UTC), progress=None)
            self._done_ids.append(job.id)
            del self._done_ids[:-1000]
        except WaitForNetwork as w:
            await self._finish(job.id, status="queued", attempts=max(0, job.attempts - 1), locked_at=None,
                               locked_by=None, run_after=datetime.now(UTC) + timedelta(seconds=w.delay),
                               progress=w.reason[:500])
        except JobTimeout as e:
            log.warning("job %s %s timed out after %s s", job.kind, job.id, time_limit(job.kind))
            if job.kind == "post.publish":
                await _publish_interrupted(job, "It took too long to publish (the platform didn't answer). Check the "
                                                "platform: if the post isn't there, press Publish now.")
                await self._finish(job.id, status="failed", error=str(e)[:4000], finished_at=datetime.now(UTC))
                return
            permanent = job.attempts >= job.max_attempts
            if permanent:
                await self._finish(job.id, status="failed", error=str(e)[:4000], finished_at=datetime.now(UTC))
                await _on_job_failed(job, str(e))
            else:
                await self._finish(job.id, status="queued", error=str(e)[:4000], locked_at=None, locked_by=None,
                                   run_after=datetime.now(UTC) + timedelta(minutes=5 * job.attempts))
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
                ok, err = True, None
                try:
                    await fn()
                except Exception as e:  # noqa: BLE001
                    ok, err = False, f"{type(e).__name__}: {e}"[:300]
                    log.exception("periodic task %s failed", name)
                await record_periodic(name, seconds, ok, err)
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

    # ------------------------------------------------------------------ cloud: one tick per scheduler call
    async def tick(self, budget_seconds: float = 50) -> dict:
        """Run due periodic tasks, then claim jobs for `budget_seconds`, then wait for the jobs started here.
        Several ticks (and several instances) may overlap: jobs are claimed with SKIP LOCKED and each periodic task
        takes a Postgres advisory lock, so nothing runs twice."""
        from app.core import storage
        from app.services import uptime

        started = datetime.now(UTC)
        if not getattr(self, "_ticked", False):
            uptime.WATCHDOG = self.watchdog
            self._ticked = True
            try:
                await self.watchdog.startup()
            except Exception:  # noqa: BLE001
                log.exception("watchdog start-up failed")
                self.watchdog.online = True
        try:
            await self.watchdog.tick()
        except Exception:  # noqa: BLE001
            log.exception("watchdog tick failed")
            self.watchdog.online = True
        try:
            await self._recover_stale()
        except Exception:  # noqa: BLE001
            log.exception("stale job recovery failed")
        ran: list[str] = []
        for name, seconds, fn in PERIODIC:
            if name in cloud_skip() or (self.watchdog.paused and not getattr(fn, "_offline_ok", False)):
                continue
            if await _run_periodic_if_due(name, seconds, fn, started):
                ran.append(name)
        until = started + timedelta(seconds=max(5.0, budget_seconds))
        before = len(self._done_ids)
        mine: set[asyncio.Task] = set()
        await asyncio.gather(self._lane(True, self._fast_sem, until, mine), self._lane(False, self._sem, until, mine))
        if mine:
            await asyncio.gather(*mine, return_exceptions=True)
        await asyncio.to_thread(storage.prune_cache)
        return {"periodic": ran, "jobs": len(self._done_ids) - before,
                "seconds": round((datetime.now(UTC) - started).total_seconds(), 1)}

    async def run(self) -> None:
        from app.services import uptime

        from app.core.config import get_settings

        if not get_settings().automation_enabled:
            log.warning("AUTOMATION_ENABLED=false: the worker isn't starting (another environment runs the jobs)")
            await self._stop.wait()
            return
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
        # Two lanes, so a slow job (a site crawl, a video render) can never hold up publishing or metrics.
        await asyncio.gather(self._lane(True, self._fast_sem), self._lane(False, self._sem))
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
