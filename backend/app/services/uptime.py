"""Knowing when the app couldn't work, so it can catch up gently afterwards.

The studio runs on a laptop. The laptop can be switched off, asleep / hibernating, or without internet when a post is
due. `Watchdog` (used by the worker) notices each of those:

  stopped  the worker wasn't running (laptop off, app closed)   -> seen at start-up from the last heartbeat
  asleep   the laptop slept or hibernated while the app ran     -> seen as a jump in the wall clock between ticks
  offline  no internet                                          -> seen by a small connectivity check

While offline the worker pauses (nothing is claimed, so no retries are wasted and no post fails); as soon as the
connection is back, or the laptop wakes, every periodic task runs straight away and late posts go out one by one
(spaced per account, see scheduler.dispatch). Downtime is recorded in `downtime_periods` for the "welcome back"
summary and shown on the status banner.

On Windows the watchdog also keeps the laptop awake while a post is due (SetThreadExecutionState) and, when an
organization opts in, sets a Windows wake timer for the next post (Task Scheduler, "wake the computer").
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from pathlib import Path

import httpx
from sqlalchemy import select

from app.core.db import platform_session
from app.models import DowntimePeriod, Organization, Post, SystemState

log = logging.getLogger("uptime")

TICK_SECONDS = 20
GAP_SECONDS = 120            # a pause longer than this between heartbeats means the laptop slept or the app was off
PROBE_EVERY_ONLINE = 60      # seconds between connectivity checks while online
OFFLINE_AFTER_FAILURES = 2   # consecutive failed checks before we call it offline
MIN_PERIOD_SECONDS = 60      # shorter blips aren't worth recording
PROBE_URLS = (
    "https://www.gstatic.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
    "http://www.msftconnecttest.com/connecttest.txt",
)
WAKE_TASK = "HRP Digital Marketing - wake for posts"
WAKE_LEAD = timedelta(minutes=3)
KEEP_AWAKE_WINDOW = timedelta(minutes=10)
DEFAULT_GAP_MINUTES = 20


def cloud() -> bool:
    from app.core.config import get_settings

    return get_settings().cloud


def stale_after() -> int:
    """Seconds without a heartbeat before the worker counts as stopped. Cloud: the job runner only wakes up for work
    (regular tick every TICK_EVERY_MINUTES), so silence in between is normal."""
    if cloud():
        from app.core.config import get_settings

        return get_settings().tick_every_minutes * 60 + 300
    return 120


def now_utc() -> datetime:
    return datetime.now(UTC)


def publishing_settings(org: Organization | None) -> dict:
    cfg = dict((org.publishing if org is not None else None) or {})
    try:
        gap = int(cfg.get("catch_up_gap_minutes", DEFAULT_GAP_MINUTES))
    except (TypeError, ValueError):
        gap = DEFAULT_GAP_MINUTES
    return {"catch_up_gap_minutes": max(0, min(gap, 240)), "wake_for_posts": bool(cfg.get("wake_for_posts", False)),
            "auto_approve_posts": bool(cfg.get("auto_approve_posts", False)),
            "auto_publish_articles": bool(cfg.get("auto_publish_articles", False)),
            "publish_held_posts": bool(cfg.get("publish_held_posts", False)),
            "blog_autopilot": {k: v for k, v in blog_settings(org).items() if k != "last_run"}}


def blog_settings(org: Organization | None) -> dict:
    from app.services import blog_autopilot

    return blog_autopilot.settings(org)


# ----------------------------------------------------------------------------- connectivity
async def probe(timeout: float = 6.0) -> tuple[bool, float | None]:
    """(online, clock skew in seconds = our clock minus the server's, when a Date header came back)."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
        for url in PROBE_URLS:
            try:
                r = await c.get(url)
            except (httpx.HTTPError, OSError):
                continue
            if r.status_code >= 500:
                continue
            skew = None
            try:
                if r.headers.get("date"):
                    skew = (now_utc() - parsedate_to_datetime(r.headers["date"])).total_seconds()
            except (TypeError, ValueError):
                skew = None
            return True, skew
    return False, None


def is_network_error(e: BaseException | None) -> bool:
    """True when the failure looks like 'couldn't reach the internet' (also through wrapped exceptions)."""
    seen = 0
    while e is not None and seen < 6:
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.NetworkError,
                          ConnectionError, socket.gaierror)):
            return True
        if getattr(e, "retryable", False) and "reach" in str(e).lower():
            return True
        e = e.__cause__ or e.__context__
        seen += 1
    return False


# ----------------------------------------------------------------------------- state and downtime
async def get_state(key: str = "worker") -> dict:
    async with platform_session() as s:
        row = await s.get(SystemState, key)
        return dict(row.value or {}) if row else {}


async def set_state(value: dict, key: str = "worker") -> None:
    async with platform_session() as s:
        row = await s.get(SystemState, key)
        if row is None:
            s.add(SystemState(key=key, value=value))
        else:
            row.value = value
            row.updated_at = now_utc()
        await s.commit()


async def record_downtime(start: datetime, end: datetime, reason: str) -> DowntimePeriod | None:
    if (end - start).total_seconds() < MIN_PERIOD_SECONDS:
        return None
    async with platform_session() as s:
        p = DowntimePeriod(started_at=start, ended_at=end, reason=reason)
        s.add(p)
        await s.commit()
    log.info("downtime recorded: %s from %s to %s", reason, start.isoformat(), end.isoformat())
    return p


async def recent_downtime(since: datetime) -> list[DowntimePeriod]:
    async with platform_session() as s:
        return list((await s.scalars(select(DowntimePeriod).where(DowntimePeriod.ended_at >= since)
                                     .order_by(DowntimePeriod.started_at))).all())


REASON_TEXT = {"stopped": "switched off or the app was closed", "asleep": "asleep", "offline": "offline"}


def describe(periods: list[DowntimePeriod], fmt) -> str:
    """'asleep 01:10–08:55, offline 09:00–09:20' using fmt(datetime) -> str."""
    return ", ".join(f"{REASON_TEXT.get(p.reason, p.reason)} {fmt(p.started_at)}–{fmt(p.ended_at)}" for p in periods)


# ----------------------------------------------------------------------------- Windows helpers
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def keep_awake(on: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint32(flags))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        log.debug("SetThreadExecutionState failed", exc_info=True)


def _powershell(script: str) -> bool:
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("PowerShell failed: %s", e)
        return False
    if r.returncode != 0:
        log.warning("PowerShell failed: %s", (r.stderr or r.stdout or "")[:300])
    return r.returncode == 0


def set_wake_timer(at: datetime | None) -> bool:
    """Create/replace (or remove, when `at` is None) a Windows task that wakes the laptop from sleep at `at`."""
    if sys.platform != "win32":
        return False
    if at is None:
        return _powershell(f"Unregister-ScheduledTask -TaskName '{WAKE_TASK}' -Confirm:$false -ErrorAction SilentlyContinue; exit 0")
    local = at.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    return _powershell(
        "$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit 0'; "
        f"$t = New-ScheduledTaskTrigger -Once -At ([datetime]'{local}'); "
        "$s = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Minutes 1); "
        f"Register-ScheduledTask -TaskName '{WAKE_TASK}' -Action $a -Trigger $t -Settings $s "
        "-Description 'Wakes the laptop shortly before a scheduled post (HRP Digital Marketing Studio).' -Force | Out-Null"
    )


# ----------------------------------------------------------------------------- watchdog
@dataclass
class Snapshot:
    online: bool
    offline_since: datetime | None


class Watchdog:
    def __init__(self) -> None:
        self.online = True
        self.offline_since: datetime | None = None
        self.failures = 0
        self.last_tick: datetime | None = None
        self.last_probe: datetime | None = None
        self.clock_skew: float | None = None
        self.wake_seq = 0  # bumped after every downtime: periodic tasks run straight away
        self.awake_held = False
        self.wake_at: datetime | None = None
        self.public_ok: bool | None = None
        self.public_failures = 0
        self.tunnel_proc: subprocess.Popen | None = None
        self.tunnel_started_at: datetime | None = None
        self.last_public_check: datetime | None = None
        self._lock = asyncio.Lock()
        self._woken = asyncio.Event()

    # -- public
    @property
    def paused(self) -> bool:
        return not self.online

    async def startup(self) -> None:
        now = now_utc()
        if cloud():  # always on, always connected: no downtime to detect, no tunnel, no laptop power management
            self.online, self.offline_since, self.last_tick = True, None, now
            await self._save(now)
            return
        st = await get_state()
        last = _parse(st.get("last_seen"))
        if st.get("wake_at"):
            self.wake_at = _parse(st.get("wake_at"))
        if last and (now - last).total_seconds() > GAP_SECONDS:
            await record_downtime(last, now, "stopped")
            self._wake()
        self.last_tick = now
        # Don't claim anything until the internet is confirmed.
        self.online, self.offline_since = False, now
        await self._probe(now)
        await self._save(now)

    async def ensure_fresh(self) -> None:
        """Called before periodic tasks and job claims: notices a sleep straight after waking up."""
        now = now_utc()
        if self.last_tick is None or (now - self.last_tick).total_seconds() >= TICK_SECONDS:
            await self.tick()

    async def tick(self) -> None:
        async with self._lock:
            now = now_utc()
            if cloud():  # heartbeat only (gaps between cloud ticks are normal, not downtime)
                self.online, self.last_tick = True, now
                await self._save(now)
                return
            if self.last_tick is not None and (now - self.last_tick).total_seconds() > GAP_SECONDS:
                await record_downtime(self.last_tick, now, "asleep")
                self._wake()
                # The network usually needs a few seconds after waking: pause until it answers.
                if self.online:
                    self.online, self.offline_since, self.failures = False, now, 0
            self.last_tick = now
            due = (not self.online) or self.last_probe is None or \
                (now - self.last_probe).total_seconds() >= PROBE_EVERY_ONLINE
            if due:
                await self._probe(now)
            if self.online and (self.last_public_check is None or
                                (now - self.last_public_check).total_seconds() >= 2 * PUBLIC_CHECK_SECONDS):
                self.last_public_check = now
                ok = await public_link_ok(force=True)
                if ok is None:
                    self.public_ok, self.public_failures = None, 0
                elif ok:
                    if self.public_ok is False:
                        log.info("public address is reachable again; resuming posts that need it")
                        self._wake()
                    self.public_ok, self.public_failures = True, 0
                else:
                    # Re-check every 30 s while it fails. Only call it down (banner) after three failures in a row,
                    # about a minute: a tunnel that is still connecting right after start-up isn't a problem.
                    self.public_failures += 1
                    self.last_public_check = now - timedelta(seconds=2 * PUBLIC_CHECK_SECONDS - 30)
                    if self.public_failures >= PUBLIC_DOWN_AFTER:
                        if self.public_ok is not False:
                            log.info("public address %s isn't answering", public_base())
                        self.public_ok = False
                        self._heal_tunnel(now)  # (re)start our own tunnel
            await self._save(now)

    def _heal_tunnel(self, now: datetime) -> None:
        if self.tunnel_started_at and now - self.tunnel_started_at < TUNNEL_RESTART_COOLDOWN:
            return
        if self.tunnel_proc is not None and self.tunnel_proc.poll() is None:
            try:
                self.tunnel_proc.terminate()
            except OSError:
                pass
        proc = start_tunnel()
        if proc is not None:
            self.tunnel_proc, self.tunnel_started_at = proc, now
            self.last_public_check = now - timedelta(seconds=2 * PUBLIC_CHECK_SECONDS - 30)  # re-check in ~30 s
            log.info("public address unreachable; started the Cloudflare Tunnel again (pid %s)", proc.pid)

    async def network_failed(self) -> bool:
        """A job couldn't reach a server. Re-check now; True when we're offline (the job should wait, not fail)."""
        if cloud():
            return False  # the cloud is online: a server that can't be reached is that server's problem (retry)
        async with self._lock:
            await self._probe(now_utc(), force_fail_count=True)
            return not self.online

    async def wait_woken(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._woken.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    # -- internals
    def _wake(self) -> None:
        self.wake_seq += 1
        self._woken.set()
        self._woken = asyncio.Event()

    async def _probe(self, now: datetime, force_fail_count: bool = False) -> None:
        ok, skew = await probe()
        self.last_probe = now
        if ok:
            self.clock_skew = skew
            if not self.online:
                if self.offline_since is not None:
                    await record_downtime(self.offline_since, now, "offline")
                log.info("internet connection is available; resuming")
                self.online, self.offline_since = True, None
                self._wake()
            self.failures = 0
            return
        self.failures += 1
        if self.online and (self.failures >= OFFLINE_AFTER_FAILURES or force_fail_count and self.failures >= 1):
            log.info("no internet connection; pausing publishing until it's back")
            self.online, self.offline_since = False, now

    async def _save(self, now: datetime) -> None:
        await set_state({
            "last_seen": now.isoformat(), "online": self.online,
            "offline_since": self.offline_since.isoformat() if self.offline_since else None,
            "clock_skew_seconds": round(self.clock_skew, 1) if self.clock_skew is not None else None,
            "wake_at": self.wake_at.isoformat() if self.wake_at else None,
            "public_base": public_base() or None, "public_ok": self.public_ok,
        })

    # -- Windows: stay awake for due posts, wake up for the next one
    async def manage_power(self) -> None:
        now = now_utc()
        async with platform_session() as s:
            soon = await s.scalar(select(Post.id).where(
                Post.status.in_(["scheduled", "approved", "publishing"]), Post.scheduled_at.is_not(None),
                Post.scheduled_at <= now + KEEP_AWAKE_WINDOW).limit(1))
            want_awake = soon is not None
            orgs = (await s.scalars(select(Organization).where(Organization.status == "active"))).all()
            wake_orgs = [o.id for o in orgs if publishing_settings(o)["wake_for_posts"]]
            nxt = None
            if wake_orgs:
                nxt = await s.scalar(select(Post.scheduled_at).where(
                    Post.organization_id.in_(wake_orgs), Post.status.in_(["scheduled", "approved", "in_review"]),
                    Post.scheduled_at > now + WAKE_LEAD).order_by(Post.scheduled_at).limit(1))
        if want_awake != self.awake_held:
            keep_awake(want_awake)
            self.awake_held = want_awake
        target = (nxt - WAKE_LEAD) if nxt else None
        if target != self.wake_at and sys.platform == "win32":
            if set_wake_timer(target):
                self.wake_at = target
                log.info("wake timer %s", f"set for {target.isoformat()}" if target else "removed")


def _parse(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


WATCHDOG: Watchdog | None = None  # set by the worker; None in the API process and in tests


# ----------------------------------------------------------------------------- public address (Cloudflare Tunnel)
PUBLIC_CHECK_SECONDS = 60
PUBLIC_DOWN_AFTER = 3  # failed checks in a row (30 s apart) before the address counts as down
_public_cache: dict[str, tuple[datetime, bool]] = {}


def public_base() -> str:
    from app.core.config import get_settings

    return (get_settings().public_base_url or "").strip().rstrip("/")


async def public_link_ok(base: str | None = None, *, force: bool = False) -> bool | None:
    """Can the outside world reach our public address (the tunnel Instagram, Threads, Google and TikTok fetch media
    from)? None when no public address is set up. Cached for a minute."""
    base = (base or public_base()).rstrip("/")
    if not base:
        return None
    now = now_utc()
    hit = _public_cache.get(base)
    if hit and not force and (now - hit[0]).total_seconds() < PUBLIC_CHECK_SECONDS:
        return hit[1]
    ok = False
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
            r = await c.get(f"{base}/api/v1/health")
            ok = r.status_code == 200
    except (httpx.HTTPError, OSError):
        ok = False
    _public_cache[base] = (now, ok)
    return ok


def origin_of(url: str) -> str | None:
    """'https://studio.hrpai.in' for one of our own self-hosted media links, else None (Cloudinary etc.)."""
    i = url.find("/api/v1/media/public/")
    return url[:i] if i > 0 else None


TUNNEL_RESTART_COOLDOWN = timedelta(minutes=10)
ROOT = Path(__file__).resolve().parents[3]  # the project folder (start.ps1, tools\, storage\)


def start_tunnel() -> subprocess.Popen | None:
    r"""Windows: start tools\cloudflared.exe with CLOUDFLARE_TUNNEL_TOKEN (loaded from .env by start.ps1).
    Its PID goes into storage\.pids so stop.ps1 stops it too. Returns None when that isn't possible."""
    if sys.platform != "win32":
        return None
    token = (os.environ.get("CLOUDFLARE_TUNNEL_TOKEN") or "").strip()
    exe = ROOT / "tools" / "cloudflared.exe"
    if not token.startswith("eyJ") or not exe.exists():
        return None
    logs = ROOT / "storage" / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(
            [str(exe), "tunnel", "--no-autoupdate", "run"], env={**os.environ, "TUNNEL_TOKEN": token},
            stdout=open(logs / "tunnel.log", "ab"), stderr=open(logs / "tunnel.err.log", "ab"),  # noqa: SIM115
            creationflags=flags, cwd=str(ROOT))
    except OSError as e:
        log.warning("couldn't start the Cloudflare Tunnel: %s", e)
        return None
    try:
        with open(ROOT / "storage" / ".pids", "a", encoding="ascii") as f:
            f.write(f"\n{proc.pid}")
    except OSError:
        pass
    return proc


PUBLIC_LINK_DOWN = ("Your public address ({base}) isn't reachable right now, so {platform} can't fetch the images. "
                    "The post is waiting and goes out by itself once the address works again. Usually the Cloudflare "
                    "Tunnel isn't running: see storage\\logs\\tunnel.err.log.")
