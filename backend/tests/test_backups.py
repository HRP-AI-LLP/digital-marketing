"""Database backups: command selection, dump verification, rotation, copies, media zip, daily schedule,
failure alerts and the platform admin API. subprocess is always mocked: Podman / pg_dump never run here."""

import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import platform_session
from app.models import BackupRun, Notification
from app.services import backups
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"


class FakeShell:
    """Stands in for subprocess.run: answers `podman ps` and writes a dump to the stdout file."""

    def __init__(self, podman=True, container=True, payload=b"PGDMP\x01\x0e\x00fake-dump", code=0, stderr=b""):
        self.podman, self.container, self.payload, self.code, self.stderr = podman, container, payload, code, stderr
        self.calls: list[tuple[list[str], dict | None]] = []

    def which(self, name):
        if name == "podman":
            return "/usr/bin/podman" if self.podman else None
        if name == "pg_dump":
            return "/usr/bin/pg_dump"
        return None

    def run(self, argv, stdout=None, stderr=None, env=None, **kw):
        self.calls.append((list(argv), env))
        if argv[1] == "ps":
            return subprocess.CompletedProcess(argv, 0, stdout="hrp-dm-postgres\n" if self.container else "", stderr="")
        if self.code == 0 and hasattr(stdout, "write"):
            stdout.write(self.payload)
        return subprocess.CompletedProcess(argv, self.code, stdout=None, stderr=self.stderr)


@pytest.fixture
def shell(monkeypatch, tmp_path):
    fake = FakeShell()
    monkeypatch.setattr(backups.shutil, "which", fake.which)
    monkeypatch.setattr(backups.subprocess, "run", fake.run)
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path / "storage"))
    return fake


@pytest.fixture(autouse=True)
async def clean_runs():
    async with platform_session() as s:
        await s.execute(delete(BackupRun))
        await s.commit()
    from app.services.uptime import set_state

    await set_state({}, backups.STATE_KEY)
    yield


async def test_command_prefers_podman_container(shell):
    method, argv, env = backups.choose_command()
    assert method == "podman" and env is None
    db = backups._db_parts()
    assert argv == ["/usr/bin/podman", "exec", "hrp-dm-postgres", "pg_dump", "-U", db["user"], "-Fc", db["db"]]


FAKE_URL = "postgresql+asyncpg://dm_owner:S3cr3t%21pw@localhost:5433/digital_marketing"


async def test_command_falls_back_to_local_pg_dump(shell, monkeypatch):
    monkeypatch.setattr(get_settings(), "migration_database_url", FAKE_URL)
    shell.container = False
    method, argv, env = backups.choose_command()
    db = backups._db_parts()
    assert method == "pg_dump" and argv[0] == "/usr/bin/pg_dump" and "-Fc" in argv and argv[-1] == db["db"]
    assert db["password"] == "S3cr3t!pw" and "S3cr3t" not in " ".join(argv)  # only in the environment
    assert env["PGPASSWORD"] == "S3cr3t!pw" and argv[argv.index("-p") + 1] == "5433"
    shell.podman = False
    assert backups.choose_command()[0] == "pg_dump"


async def test_no_tool_available(shell, monkeypatch):
    shell.podman = False
    monkeypatch.setattr(backups, "_find_pg_dump", lambda: None)
    with pytest.raises(backups.BackupError, match="Neither"):
        backups.choose_command()


async def test_backup_writes_verified_dump(shell, client, admin):
    run = await backups.run_backup("manual")
    assert run.status == "ok", run.error
    assert run.method == "podman" and run.file_name.endswith("-manual.dump")
    path = backups.backup_dir() / run.file_name
    assert path.read_bytes().startswith(b"PGDMP") and run.size_bytes == path.stat().st_size
    assert not list(backups.backup_dir().glob("*.partial"))
    # A second backup can't start while one is running.
    async with platform_session() as s:
        s.add(BackupRun(kind="manual", status="running"))
        await s.commit()
    with pytest.raises(backups.BackupError, match="already running"):
        await backups.run_backup("manual")


async def test_bad_dump_fails_and_alerts_platform_admins(shell, client, admin):
    shell.payload = b"<html>not a dump"
    run = await backups.run_backup("daily")
    assert run.status == "failed" and "PGDMP" in run.error
    assert not list(backups.backup_dir().glob("dm-*"))
    async with platform_session() as s:
        n = (await s.scalars(select(Notification).where(Notification.event == "backup_failed"))).all()
    assert n and n[-1].title == "Database backup failed" and n[-1].link == "/platform/backups"

    shell.payload = b""
    run = await backups.run_backup("daily")
    assert run.status == "failed" and "empty" in run.error


async def test_failed_command_never_shows_password(shell, client, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "migration_database_url", FAKE_URL)
    pw = backups._db_parts()["password"]
    shell.code = 1
    shell.stderr = f'pg_dump: error: connection failed: password authentication failed for "x" password={pw}'.encode()
    shell.container = False
    run = await backups.run_backup("manual")
    assert run.status == "failed" and "pg_dump" in run.error
    assert pw not in run.error


def _names(start: datetime, days: int, kind="daily") -> list[str]:
    return [f"dm-{(start - timedelta(days=i)):%Y%m%d}-023000-{kind}.dump" for i in range(days)]


async def test_rotation_keeps_daily_and_weekly():
    start = datetime(2026, 10, 18, 2, 30)  # a Sunday
    names = _names(start, 120)
    names.append(f"dm-{start:%Y%m%d}-090000-manual.dump")  # a later manual backup on the same day
    keep = backups.files_to_keep(names, keep_daily=14, keep_weekly=8)
    newest_days = {n[3:11] for n in keep}
    assert f"dm-{start:%Y%m%d}-090000-manual.dump" in keep  # newest of today
    assert f"dm-{start:%Y%m%d}-023000-daily.dump" not in keep
    assert len([d for d in newest_days if d >= f"{start - timedelta(days=13):%Y%m%d}"]) == 14
    # 8 weekly backups: the newest of each of the last 8 ISO weeks (some overlap with the daily ones).
    weeks = {datetime.strptime(n[3:11], "%Y%m%d").isocalendar()[:2] for n in keep}
    assert len(weeks) == 8
    assert min(newest_days) >= f"{start - timedelta(weeks=8):%Y%m%d}"
    assert len(keep) == 14 + 6  # weeks 1-2 are already covered by the daily ones


async def test_rotate_deletes_files(tmp_path):
    start = datetime(2026, 10, 18, 2, 30)
    for n in _names(start, 30):
        (tmp_path / n).write_bytes(b"PGDMP")
    (tmp_path / "notes.txt").write_text("keep me")
    removed = backups.rotate(tmp_path, keep_daily=3, keep_weekly=2)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert "notes.txt" in left and len(removed) == 30 - (len(left) - 1)
    assert len(left) - 1 == 4  # 3 days + 1 more week


async def test_copy_and_media_zip(shell, tmp_path, client, admin):
    copy = tmp_path / "OneDrive" / "Backups"
    copy.mkdir(parents=True)
    media = Path(get_settings().storage_dir) / "orgs" / "o1" / "media" / "2026-10"
    media.mkdir(parents=True)
    (media / "a.png").write_bytes(b"png")
    cache = Path(get_settings().storage_dir) / "orgs" / "o1" / "cache"
    cache.mkdir(parents=True)
    (cache / "big.tmp").write_bytes(b"x" * 100)
    await backups.save_config({"copy_to": backups.check_copy_folder(str(copy)), "include_media": True})
    run = await backups.run_backup("manual")
    assert run.status == "ok" and run.warning is None, run.warning
    assert (copy / run.file_name).read_bytes().startswith(b"PGDMP")
    assert run.media_file and (copy / run.media_file).exists()
    with zipfile.ZipFile(backups.backup_dir() / run.media_file) as z:
        names = z.namelist()
    assert "orgs/o1/media/2026-10/a.png" in names and not any("cache" in n for n in names)

    with pytest.raises(ValueError):
        backups.check_copy_folder(str(tmp_path / "missing"))
    with pytest.raises(ValueError):
        backups.check_copy_folder("relative/folder")
    assert backups.check_copy_folder("") == ""


async def _due_a_minute_ago() -> datetime:
    now = datetime.now()
    if now.hour == 0 and now.minute < 3:
        pytest.skip("too close to midnight")
    due = now - timedelta(minutes=1)
    await backups.save_config({"time": f"{due:%H:%M}"})
    return now


async def test_daily_schedule(shell, client, admin):
    now = await _due_a_minute_ago()
    assert await backups.maybe_run_daily(now - timedelta(minutes=3)) is None  # before the set time
    run = await backups.maybe_run_daily(now)
    assert run is not None and run.status == "ok" and run.kind == "daily"
    assert await backups.maybe_run_daily(now) is None  # done for today
    await backups.save_config({"enabled": False})
    async with platform_session() as s:
        await s.execute(delete(BackupRun))
        await s.commit()
    assert await backups.maybe_run_daily(now) is None


async def test_daily_retry_after_failure(shell, client, admin):
    shell.payload = b"nope"
    now = await _due_a_minute_ago()
    run = await backups.maybe_run_daily(now)
    assert run.status == "failed"
    assert await backups.maybe_run_daily(now) is None  # waits an hour before trying again


async def test_backups_api(shell, client, admin):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(a),
                          json={"name": "Backup Api Org", "admin_email": "owner@backup-api.in", "admin_name": "Owner"})
    other = await login(client, "owner@backup-api.in", r.json()["temporary_password"])
    assert (await client.get("/platform/backups", headers=auth(other))).status_code == 403
    assert (await client.post("/platform/backups/run", headers=auth(other))).status_code == 403

    r = await client.post("/platform/backups/run", headers=auth(a))
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    body = (await client.get("/platform/backups", headers=auth(a))).json()
    row = next(x for x in body["runs"] if x["id"] == run_id)
    assert row["status"] == "ok" and row["triggered_by"]  # the background task ran after the response
    assert body["settings"]["time"] == "02:30" and body["disk"]["files"] == 1

    r = await client.get(f"/platform/backups/{run_id}/download", headers=auth(a))
    assert r.status_code == 200 and r.content.startswith(b"PGDMP")
    assert (await client.get(f"/platform/backups/{run_id}/download?media=true", headers=auth(a))).status_code == 404

    r = await client.patch("/platform/backups/settings", headers=auth(a), json={"time": "25:00"})
    assert r.status_code == 400
    r = await client.patch("/platform/backups/settings", headers=auth(a), json={"copy_to": "/no/such/folder"})
    assert r.status_code == 400
    r = await client.patch("/platform/backups/settings", headers=auth(a), json={"time": "3:05", "keep_daily": 7})
    assert r.status_code == 200 and r.json()["time"] == "03:05" and r.json()["keep_daily"] == 7
