"""Automatic database backups (platform level).

Every day at a set local time (default 02:30; if the laptop was off then, as soon as the worker runs again) a
compressed PostgreSQL dump (pg_dump custom format, `-Fc`) is written to storage/backups/. The dump is made with,
in order of availability:

1. `podman exec hrp-dm-postgres pg_dump -U <owner> -Fc <database>`  (the Podman container start.ps1 runs)
2. a local `pg_dump` with the MIGRATION_DATABASE_URL credentials (password passed via PGPASSWORD, never logged)

Each file is checked (not empty, starts with the "PGDMP" header), optionally copied to a second folder (for example
a OneDrive or Google Drive synced folder) and the media files under storage/orgs can be zipped too. Old backups are
rotated: the newest backup of each of the last `keep_daily` days plus the newest of each of the last `keep_weekly`
weeks are kept. Every attempt is a BackupRun row; failures notify platform administrators.

Restore with restore.ps1 at the repository root.

In the cloud (DEPLOY_MODE=cloud) the dump is made with the image's PostgreSQL 17 `pg_dump` over the owner connection
(MIGRATION_DATABASE_URL), stored in the R2 bucket under backups/ and rotated there; media already lives in R2, so it
isn't zipped. Restore a cloud backup with pg_restore against the owner connection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import platform_session
from app.models import BackupRun

log = logging.getLogger(__name__)

STATE_KEY = "backups"
CONTAINER = "hrp-dm-postgres"
HEADER = b"PGDMP"
DUMP_TIMEOUT_S = 60 * 60
DEFAULTS = {"enabled": True, "time": "02:30", "keep_daily": 14, "keep_weekly": 8, "copy_to": "",
            "include_media": False}
NAME_RX = re.compile(r"^dm-(\d{8})-(\d{6})-(daily|manual)\.dump$")
MEDIA_RX = re.compile(r"^dm-(\d{8})-(\d{6})-(daily|manual)-media\.zip$")
_lock = asyncio.Lock()


class BackupError(RuntimeError):
    """Shown to the platform admin as is (already free of passwords)."""


# ---- settings ------------------------------------------------------------------------------------------
def backup_dir() -> Path:
    p = Path(get_settings().storage_dir) / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


async def get_config() -> dict:
    from app.services.uptime import get_state

    st = await get_state(STATE_KEY)
    return {**DEFAULTS, **{k: v for k, v in (st.get("settings") or {}).items() if k in DEFAULTS}}


async def save_config(changes: dict) -> dict:
    from app.services.uptime import get_state, set_state

    st = await get_state(STATE_KEY)
    cfg = {**DEFAULTS, **(st.get("settings") or {}), **changes}
    await set_state({**st, "settings": cfg}, STATE_KEY)
    return await get_config()


def parse_time(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (value or "").strip())
    if not m:
        raise ValueError("Use a time like 02:30 (24-hour clock).")
    return int(m.group(1)), int(m.group(2))


def check_copy_folder(path: str) -> str:
    """Validate the second folder (must exist and be writable). Returns the cleaned path ('' turns it off)."""
    path = (path or "").strip().strip('"')
    if not path:
        return ""
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ValueError("Enter the full folder path, for example C:\\Users\\you\\OneDrive\\Backups.")
    if not p.is_dir():
        raise ValueError("That folder doesn't exist on this laptop. Create it first (or check the spelling).")
    probe = p / ".hrp-write-test"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        raise ValueError("The studio can't write to that folder. Pick a folder in your user profile.")
    return str(p)


# ---- commands ------------------------------------------------------------------------------------------
def _db_parts() -> dict:
    u = urlsplit(get_settings().migration_database_url)
    return {"host": u.hostname or "localhost", "port": str(u.port or 5432), "user": unquote(u.username or "postgres"),
            "password": unquote(u.password or ""), "db": (u.path or "/").lstrip("/") or "postgres"}


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _container_running(podman: str) -> bool:
    try:
        r = subprocess.run([podman, "ps", "--filter", f"name=^{CONTAINER}$", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=30, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and CONTAINER in (r.stdout or "").split()


def _find_pg_dump() -> str | None:
    found = shutil.which("pg_dump")
    if found:
        return found
    if sys.platform == "win32":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"
        for exe in sorted(base.glob("*/bin/pg_dump.exe"), reverse=True):
            return str(exe)
    return None


def _cloud() -> bool:
    return get_settings().cloud


def choose_command() -> tuple[str, list[str], dict | None]:
    """(method, argv, extra env). Raises BackupError when neither Podman nor pg_dump is available."""
    db = _db_parts()
    if _cloud():
        pg_dump = _find_pg_dump()
        if not pg_dump:
            raise BackupError("pg_dump isn't installed in this image.")
        local = db["host"] in ("localhost", "127.0.0.1", "::1")
        env = {**os.environ, "PGPASSWORD": db["password"], "PGSSLMODE": "prefer" if local else "require"}
        return "pg_dump", [pg_dump, "-h", db["host"], "-p", db["port"], "-U", db["user"], "-Fc", "--no-password",
                           db["db"]], env
    podman = shutil.which("podman")
    if podman and _container_running(podman):
        return "podman", [podman, "exec", CONTAINER, "pg_dump", "-U", db["user"], "-Fc", db["db"]], None
    pg_dump = _find_pg_dump()
    if pg_dump:
        env = {**os.environ, "PGPASSWORD": db["password"]} if db["password"] else None
        return "pg_dump", [pg_dump, "-h", db["host"], "-p", db["port"], "-U", db["user"], "-Fc", "--no-password",
                           db["db"]], env
    raise BackupError("Neither the Podman database container (hrp-dm-postgres) nor pg_dump was found. "
                      "Start the studio with start.ps1, or install the PostgreSQL command-line tools.")


def _clean(text: str) -> str:
    pw = _db_parts()["password"]
    if pw:
        text = text.replace(pw, "•••")
    return re.sub(r"(?i)(password[=:]\s*)\S+", r"\1•••", text).strip()[:800]


def _dump_to(path: Path) -> str:
    """Run the dump, streaming stdout into `path`. Returns the method used."""
    method, argv, env = choose_command()
    partial = path.with_suffix(path.suffix + ".partial")
    try:
        with open(partial, "wb") as fh:
            r = subprocess.run(argv, stdout=fh, stderr=subprocess.PIPE, env=env, timeout=DUMP_TIMEOUT_S,
                               creationflags=_no_window())
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        raise BackupError("The backup took longer than an hour and was stopped.")
    except OSError as e:
        partial.unlink(missing_ok=True)
        raise BackupError(f"Couldn't run the backup command: {_clean(str(e))}")
    if r.returncode != 0:
        partial.unlink(missing_ok=True)
        err = _clean((r.stderr or b"").decode("utf-8", "replace")) or f"exit code {r.returncode}"
        raise BackupError(f"The backup command failed ({method}): {err}")
    verify(partial)
    partial.replace(path)
    return method


def verify(path: Path) -> None:
    size = path.stat().st_size if path.exists() else 0
    if size == 0:
        path.unlink(missing_ok=True)
        raise BackupError("The backup file came out empty.")
    with open(path, "rb") as fh:
        head = fh.read(len(HEADER))
    if head != HEADER:
        path.unlink(missing_ok=True)
        raise BackupError("The backup file isn't a valid PostgreSQL dump (missing the PGDMP header).")


def _zip_media(path: Path) -> int:
    """Zip storage/orgs (generated and uploaded media), skipping cache folders. Returns the zip size."""
    src = Path(get_settings().storage_dir) / "orgs"
    partial = path.with_suffix(".zip.partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        if src.is_dir():
            for root, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if d.lower() not in ("cache", ".cache", "tmp")]
                for f in files:
                    full = Path(root) / f
                    zf.write(full, full.relative_to(src.parent).as_posix())
    partial.replace(path)
    return path.stat().st_size


# ---- rotation ------------------------------------------------------------------------------------------
def _stamp(name: str) -> datetime | None:
    m = NAME_RX.match(name) or MEDIA_RX.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def files_to_keep(names: list[str], keep_daily: int, keep_weekly: int) -> set[str]:
    """Newest file of each of the newest `keep_daily` days, plus the newest of each of the newest `keep_weekly`
    ISO weeks. Database dumps and media zips are rotated separately (call once per kind)."""
    dated = sorted(((s, n) for n in names if (s := _stamp(n))), reverse=True)
    keep: set[str] = set()
    days: dict[date, str] = {}
    weeks: dict[tuple[int, int], str] = {}
    for st, n in dated:
        days.setdefault(st.date(), n)
        weeks.setdefault(tuple(st.isocalendar())[:2], n)
    keep.update(list(days.values())[:max(1, keep_daily)])
    keep.update(list(weeks.values())[:max(0, keep_weekly)])
    return keep


def rotate(folder: Path, keep_daily: int, keep_weekly: int) -> list[str]:
    """Delete backups outside the retention window in `folder`. Returns the deleted file names."""
    if not folder.is_dir():
        return []
    removed = []
    for rx in (NAME_RX, MEDIA_RX):
        names = [p.name for p in folder.iterdir() if p.is_file() and rx.match(p.name)]
        keep = files_to_keep(names, keep_daily, keep_weekly)
        for n in names:
            if n not in keep:
                try:
                    (folder / n).unlink()
                    removed.append(n)
                except OSError:
                    log.warning("couldn't delete old backup %s", n)
    return removed


def _bucket_names() -> list[str]:
    from app.core import storage

    out = []
    for page in storage._s3().get_paginator("list_objects_v2").paginate(Bucket=storage.bucket(), Prefix="backups/"):
        out += [o["Key"].split("/", 1)[1] for o in page.get("Contents", []) or []]
    return out


def _to_bucket(path: Path) -> None:
    from app.core import storage

    storage._upload(path, f"backups/{path.name}")
    path.unlink(missing_ok=True)  # the container's disk is only a cache


def rotate_bucket(folder: Path, keep_daily: int, keep_weekly: int) -> list[str]:
    """Cloud: rotate the dumps kept in the bucket (backups/)."""
    from app.core import storage

    names = [n for n in _bucket_names() if NAME_RX.match(n)]
    keep = files_to_keep(names, keep_daily, keep_weekly)
    removed = []
    for n in names:
        if n not in keep:
            storage.delete(f"backups/{n}")
            removed.append(n)
    return removed


# ---- running -------------------------------------------------------------------------------------------
async def _fail_stale() -> None:
    """Runs left 'running' by a stopped app are marked failed."""
    async with platform_session() as s:
        rows = (await s.scalars(select(BackupRun).where(
            BackupRun.status == "running", BackupRun.started_at < datetime.now(UTC) - timedelta(hours=2)))).all()
        for r in rows:
            r.status, r.finished_at = "failed", datetime.now(UTC)
            r.error = "The app stopped while this backup was running."
        await s.commit()


async def running() -> BackupRun | None:
    await _fail_stale()
    async with platform_session() as s:
        return await s.scalar(select(BackupRun).where(BackupRun.status == "running").limit(1))


async def start_run(kind: str, triggered_by=None) -> BackupRun:
    """Create the BackupRun row (status running). Raises BackupError when one is already running."""
    if await running():
        raise BackupError("A backup is already running. It usually takes under a minute.")
    async with platform_session() as s:
        run = BackupRun(kind=kind, status="running", triggered_by=triggered_by, started_at=datetime.now(UTC))
        s.add(run)
        await s.commit()
        await s.refresh(run)
        return run


async def execute(run_id) -> BackupRun:
    """Do the backup for an existing BackupRun row and record the outcome. Never raises."""
    async with _lock:
        cfg = await get_config()
        async with platform_session() as s:
            run = await s.get(BackupRun, run_id)
            kind = run.kind
        now_local = datetime.now()
        base = f"dm-{now_local:%Y%m%d-%H%M%S}-{kind}"
        folder = backup_dir()
        dump = folder / f"{base}.dump"
        values: dict = {"finished_at": None}
        warnings: list[str] = []
        try:
            values["method"] = await asyncio.to_thread(_dump_to, dump)
            values["file_name"], values["size_bytes"] = dump.name, dump.stat().st_size
            if _cloud():
                await asyncio.to_thread(_to_bucket, dump)
            if cfg["include_media"] and not _cloud():
                try:
                    media = folder / f"{base}-media.zip"
                    values["media_size_bytes"] = await asyncio.to_thread(_zip_media, media)
                    values["media_file"] = media.name
                except OSError as e:
                    warnings.append(f"Media files weren't zipped: {e.strerror or e}")
            if cfg["copy_to"] and not _cloud():
                try:
                    dest = Path(cfg["copy_to"])
                    await asyncio.to_thread(shutil.copy2, dump, dest / dump.name)
                    if values.get("media_file"):
                        await asyncio.to_thread(shutil.copy2, folder / values["media_file"], dest / values["media_file"])
                    values["copied_to"] = str(dest)[:500]
                    await asyncio.to_thread(rotate, dest, cfg["keep_daily"], cfg["keep_weekly"])
                except OSError as e:
                    warnings.append(f"Couldn't copy to {cfg['copy_to']}: {e.strerror or e}")
            removed = await asyncio.to_thread(rotate_bucket if _cloud() else rotate, folder, cfg["keep_daily"],
                                              cfg["keep_weekly"])
            values["status"] = "ok"
        except BackupError as e:
            values.update(status="failed", error=str(e))
            removed = []
        except Exception as e:  # noqa: BLE001
            log.exception("backup failed")
            values.update(status="failed", error=_clean(f"{type(e).__name__}: {e}"))
            removed = []
        values["finished_at"] = datetime.now(UTC)
        values["warning"] = "; ".join(warnings) or None
        async with platform_session() as s:
            run = await s.get(BackupRun, run_id)
            for k, v in values.items():
                setattr(run, k, v)
            if removed:
                old = (await s.scalars(select(BackupRun).where(BackupRun.file_name.in_(removed),
                                                               BackupRun.removed_at.is_(None)))).all()
                for o in old:
                    o.removed_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(run)
        if run.status == "failed" or run.warning:
            await alert(run)
        else:
            log.info("backup %s written (%s bytes, %s)", run.file_name, run.size_bytes, run.method)
        return run


async def run_backup(kind: str = "manual", triggered_by=None) -> BackupRun:
    run = await start_run(kind, triggered_by)
    return await execute(run.id)


async def alert(run: BackupRun) -> None:
    """Tell the platform administrators (in the app, and by email/Telegram per their notification settings)."""
    from app.models import Membership
    from app.services.notify import notify_users, platform_admin_ids

    title = "Database backup failed" if run.status == "failed" else "Database backup finished with a problem"
    body = (run.error or run.warning or "")[:800]
    try:
        admins = await platform_admin_ids()
        async with platform_session() as s:
            rows = (await s.execute(select(Membership.user_id, Membership.organization_id)
                                    .where(Membership.user_id.in_(admins), Membership.is_active.is_(True))
                                    .order_by(Membership.created_at))).all()
        first_org: dict = {}
        for uid, oid in rows:
            first_org.setdefault(uid, oid)
        for uid, oid in first_org.items():
            await notify_users(oid, [uid], "backup_failed", title, body, link="/platform/backups")
    except Exception:  # noqa: BLE001
        log.exception("couldn't notify about the backup problem")


def _due_today(cfg: dict, now_local: datetime) -> datetime | None:
    try:
        h, m = parse_time(cfg["time"])
    except ValueError:
        h, m = 2, 30
    at = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    return at if now_local >= at else None


async def maybe_run_daily(now_local: datetime | None = None) -> BackupRun | None:
    """Periodic check: run today's backup once it's past the set time (catches up after the laptop was off).
    A failed attempt is retried after an hour, at most 3 times a day."""
    cfg = await get_config()
    if not cfg["enabled"]:
        return None
    now_local = now_local or datetime.now()
    due = _due_today(cfg, now_local)
    if due is None:
        return None
    since = due.astimezone(UTC)
    async with platform_session() as s:
        today = (await s.scalars(select(BackupRun).where(BackupRun.kind == "daily", BackupRun.started_at >= since)
                                 .order_by(BackupRun.started_at.desc()))).all()
    if any(r.status in ("ok", "running") for r in today):
        return None
    if len(today) >= 3 or (today and today[0].started_at > datetime.now(UTC) - timedelta(hours=1)):
        return None
    try:
        return await run_backup("daily")
    except BackupError:
        return None


def resolve_file(name: str) -> Path:
    if not (NAME_RX.match(name) or MEDIA_RX.match(name)):
        raise BackupError("Unknown backup file.")
    if _cloud():
        from app.core import storage

        p = storage.abs_path(f"backups/{name}")  # fetched from the bucket
    else:
        p = backup_dir() / name
    if not p.is_file():
        raise BackupError("That backup file is no longer on this laptop (it was rotated out).")
    return p


def disk_usage() -> dict:
    folder = backup_dir()
    if _cloud():
        from app.core import storage

        objs = []
        try:
            pages = storage._s3().get_paginator("list_objects_v2").paginate(Bucket=storage.bucket(), Prefix="backups/")
            for page in pages:
                objs += page.get("Contents", []) or []
        except Exception:  # noqa: BLE001 - the page still shows the runs
            log.warning("couldn't list the backups in the bucket", exc_info=True)
        return {"folder": f"R2 bucket {storage.bucket()}/backups", "files": len(objs),
                "total_bytes": sum(o["Size"] for o in objs), "free_bytes": None}
    files = [p for p in folder.iterdir() if p.is_file() and (NAME_RX.match(p.name) or MEDIA_RX.match(p.name))]
    total = sum(p.stat().st_size for p in files)
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        free = None
    return {"folder": str(folder), "files": len(files), "total_bytes": total, "free_bytes": free}
