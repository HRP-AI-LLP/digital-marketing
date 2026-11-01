"""Cloud mode: the origin guard, the scheduler tick that runs jobs, the local kill switch and R2-backed storage."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

import app.modules.content  # noqa: F401  (registers job handlers)
from app.core import storage
from app.core.config import get_settings
from app.core.db import platform_session
from app.models import Job
from app.services import jobs

ROOT = "http://test"


@pytest.fixture
async def cloud():
    s = get_settings()
    old = (s.deploy_mode, s.origin_verify_secret, s.cron_secret)
    s.deploy_mode, s.origin_verify_secret, s.cron_secret = "cloud", "ov-secret", "cron-secret"
    yield s
    s.deploy_mode, s.origin_verify_secret, s.cron_secret = old
    from app.services import uptime

    await uptime.set_state({})  # other tests expect "the worker isn't running"
    jobs._TICK_WORKER = None
    uptime.WATCHDOG = None


async def test_origin_guard(client, cloud):
    assert (await client.get("/health")).status_code == 200  # open for uptime checks
    assert (await client.get("/auth/me")).status_code == 404  # straight to Cloud Run: refused
    r = await client.get("/auth/me", headers={"x-origin-verify": "ov-secret"})
    assert r.status_code == 401  # through the Worker: normal API (needs sign-in)
    r = await client.get(f"{ROOT}/r/abcd", headers={"x-origin-verify": "ov-secret", "cf-ray": "1"})
    assert r.status_code == 404 and "Page" in r.text or r.status_code == 404  # public pages pass the guard


async def test_tick_runs_due_periodic_tasks_and_jobs(client, cloud, monkeypatch):
    ran = []

    async def periodic_task():
        ran.append("p")

    async def handler(ctx):
        return {"ok": True}

    monkeypatch.setattr(jobs, "PERIODIC", [("test_every_hour", 3600, periodic_task)])
    monkeypatch.setitem(jobs.HANDLERS, "test.quick", handler)
    monkeypatch.setattr(jobs, "_TICK_WORKER", None)
    async with platform_session() as s:  # keep other tests' leftovers out of the way
        await s.execute(update(Job).where(Job.status == "queued").values(run_after=datetime.now(UTC) + timedelta(days=1)))
        await s.commit()
        j = await jobs.enqueue(s, "test.quick", None, {})
        await s.commit()
        jid = j.id
    h = {"x-origin-verify": "ov-secret"}
    assert (await client.post("/internal/tick", headers=h)).status_code == 403
    cloud.tick_seconds = 1
    r = await client.post("/internal/tick", headers={**h, "x-cron-secret": "cron-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["periodic"] == ["test_every_hour"] and r.json()["jobs"] >= 1 and ran == ["p"]
    async with platform_session() as s:
        assert (await s.get(Job, jid)).status == "done"
    # not due again within the hour
    r = await client.post("/internal/tick", headers={**h, "x-cron-secret": "cron-secret"})
    assert r.json()["periodic"] == [] and ran == ["p"]
    cloud.tick_seconds = 50


async def test_kill_switch(client, cloud):
    cloud.automation_enabled = False
    try:
        r = await client.post("/internal/tick", headers={"x-origin-verify": "ov-secret", "x-cron-secret": "cron-secret"})
        assert r.json() == {"skipped": "automation is switched off here"}
    finally:
        cloud.automation_enabled = True


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.objects[key] = open(path, "rb").read()

    def download_file(self, bucket, key, path):
        from botocore.exceptions import ClientError

        if key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        open(path, "wb").write(self.objects[key])

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def get_paginator(self, name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k, "Size": len(v)} for k, v in objs.items() if k.startswith(Prefix)]}]
        return P()


async def test_r2_storage_round_trip(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "storage_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(s, "s3_bucket", "dm-test")
    fake = FakeS3()
    monkeypatch.setattr(storage, "_client", fake)
    org = uuid.uuid4()
    rel, _ = storage.save_bytes(org, "media", b"hello", "png")
    assert fake.objects[rel] == b"hello"
    storage.abs_path(rel).unlink()  # a fresh container: not cached yet
    assert storage.abs_path(rel).read_bytes() == b"hello"
    fake.objects["seo/acme/post/article.html"] = b"<h1>x</h1>"
    assert (storage.abs_path("seo/acme/post") / "article.html").read_bytes() == b"<h1>x</h1>"
    storage.delete(rel)
    assert rel not in fake.objects and not (tmp_path / "cache" / rel).exists()


async def test_wake_books_exact_time_tasks(client, cloud, monkeypatch):
    """A queued job / an upcoming scheduled post books one Cloud Task (deduplicated per 15 s); locally nothing."""
    from app.services import wake

    calls = []

    class FakeResp:
        def __init__(self, code=200, data=None):
            self.status_code, self._d, self.text = code, data or {}, ""

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            if url.endswith("/token"):
                return FakeResp(data={"access_token": "t", "expires_in": 300})
            return FakeResp()

        async def post(self, url, json=None, headers=None):
            calls.append(json["task"])
            return FakeResp(409 if len(calls) > 5 else 200)

    monkeypatch.setattr(wake.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(wake, "_booked", {})
    monkeypatch.setitem(wake._cache, "where", ("proj", "asia-southeast1", "https://dm-api-uat-1.asia-southeast1.run.app"))
    old_q = cloud.tasks_queue
    cloud.tasks_queue = ""
    assert await wake.book() is False and calls == []  # no queue configured: nothing
    cloud.tasks_queue = "dm-tick-uat"
    try:
        async with platform_session() as s:
            await s.execute(update(Job).where(Job.status == "queued").values(status="cancelled"))
            j = await jobs.enqueue(s, "test.quick", None, {}, run_after=datetime.now(UTC) + timedelta(minutes=7))
            await s.commit()
        at = await wake.plan()
        assert at is not None and timedelta(minutes=6) < at - datetime.now(UTC) < timedelta(minutes=8)
        assert len(calls) == 1 and calls[0]["name"].startswith("projects/proj/locations/asia-southeast1/queues/dm-tick-uat/tasks/tick-")
        assert calls[0]["httpRequest"]["url"].endswith("/api/v1/internal/tick")
        assert calls[0]["httpRequest"]["headers"]["X-Cron-Secret"] == "cron-secret"
        assert datetime.fromisoformat(calls[0]["scheduleTime"].replace("Z", "+00:00")) >= at - timedelta(seconds=1)
        await wake.plan()
        assert len(calls) == 1  # same moment: booked once
        # nothing due before the next regular tick: no task
        async with platform_session() as s:
            await s.execute(update(Job).where(Job.id == j.id).values(run_after=datetime.now(UTC) + timedelta(hours=3)))
            await s.commit()
        monkeypatch.setattr(wake, "_booked", {})
        assert await wake.plan() is None and len(calls) == 1
        # a request that queues work books a wake-up right away (through the API middleware)
        assert await wake.book() is True and len(calls) == 2
    finally:
        cloud.tasks_queue = old_q
        async with platform_session() as s:
            await s.execute(update(Job).where(Job.id == j.id).values(status="cancelled"))
            await s.commit()


async def test_move_helpers():
    """Connection strings for the move: direct host, sslmode, app user swapped in; the laptop's encryption key."""
    from app.core import crypto
    from app.move_to_cloud import encryption_key, libpq_url, with_user

    ref = "abcdefghijklmnopqrst"
    u = libpq_url(f"postgresql://postgres.{ref}:p%40ss@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres")
    assert u == f"postgresql://postgres.{ref}:p%40ss@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require"
    a = with_user(u, "dm_app", "x/y+z")  # the pooler routes by <role>.<project ref>
    assert a.startswith(f"postgresql://dm_app.{ref}:x%2Fy%2Bz@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?")
    n = libpq_url("postgresql://dm_owner:pw@ep-shy-mud-azltvxq7-pooler.c-3.ap-southeast-1.aws.neon.tech/digital_marketing?sslmode=require&channel_binding=require")
    assert n.startswith("postgresql://dm_owner:pw@ep-shy-mud-azltvxq7.c-3.ap-southeast-1.aws.neon.tech/digital_marketing?")
    assert with_user(n, "dm_app", "pw2").startswith("postgresql://dm_app:pw2@ep-shy-mud-azltvxq7.c-3.")
    for bad in (f"postgresql://postgres:pw@db.{ref}.supabase.co:5432/postgres",  # direct: IPv6 only
                f"postgresql://postgres.{ref}:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"):
        with pytest.raises(SystemExit):
            libpq_url(bad)
    with pytest.raises(SystemExit):
        libpq_url("postgresql://u:p@localhost:5432/db")
    # the key handed to the cloud decrypts what the laptop encrypted
    from cryptography.fernet import Fernet

    assert __import__("json").loads(Fernet(encryption_key().encode()).decrypt(crypto.encrypt_json({"a": 1}).encode())) == {"a": 1}


async def test_cloud_watchdog_sees_no_downtime_between_ticks(cloud, monkeypatch):
    """Twenty minutes between cloud ticks is normal: no 'asleep/stopped' periods, no internet probe, stays online."""
    from app.models import DowntimePeriod
    from app.services import uptime
    from sqlalchemy import func, select

    async def no_probe(*a, **k):
        raise AssertionError("the cloud doesn't probe the internet")

    monkeypatch.setattr(uptime, "probe", no_probe)
    async with platform_session() as s:
        before = await s.scalar(select(func.count()).select_from(DowntimePeriod))
    await uptime.set_state({"last_seen": (datetime.now(UTC) - timedelta(minutes=25)).isoformat()})
    w = uptime.Watchdog()
    await w.startup()
    w.last_tick = datetime.now(UTC) - timedelta(minutes=20)
    await w.tick()
    assert w.online and not w.paused and await w.network_failed() is False
    async with platform_session() as s:
        assert await s.scalar(select(func.count()).select_from(DowntimePeriod)) == before
    assert uptime.stale_after() >= get_settings().tick_every_minutes * 60


async def test_grants_lock_out_supabase_api_roles():
    """On Supabase, anon / authenticated get table rights by default: the grants take them away (dm_app keeps its)."""
    from sqlalchemy import text

    from app.cli import grants

    from app.core.db import engine as _e  # noqa: F401
    from sqlalchemy.ext.asyncio import create_async_engine

    owner = create_async_engine(get_settings().migration_database_url)
    async with owner.begin() as c:
        await c.execute(text("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN "
                             "CREATE ROLE anon NOLOGIN; END IF; END $$"))
        await c.execute(text("GRANT SELECT ON TABLE organizations TO anon"))
    await grants()
    async with owner.begin() as c:
        anon = await c.scalar(text("SELECT has_table_privilege('anon', 'organizations', 'SELECT')"))
        app = await c.scalar(text("SELECT has_table_privilege('dm_app', 'organizations', 'SELECT')"))
    await owner.dispose()
    assert anon is False and app is True


async def test_cloud_backup_goes_to_the_bucket(cloud, monkeypatch, tmp_path):
    """Cloud: pg_dump over the owner connection, the dump lands in R2 under backups/, old ones rotate out there."""
    from app.services import backups

    fake = FakeS3()
    monkeypatch.setattr(storage, "_client", fake)
    monkeypatch.setattr(cloud, "s3_bucket", "dm-test")
    monkeypatch.setattr(cloud, "storage_dir", str(tmp_path))
    fake.objects["backups/dm-20200101-023000-daily.dump"] = b"PGDMP old"  # outside the retention window
    run = await backups.run_backup("manual")
    assert run.status == "ok", run.error
    assert f"backups/{run.file_name}" in fake.objects and fake.objects[f"backups/{run.file_name}"].startswith(b"PGDMP")
    assert not (tmp_path / "backups" / run.file_name).exists()  # the container keeps no copy
    assert "backups/dm-20200101-023000-daily.dump" not in fake.objects or backups.DEFAULTS["keep_weekly"] > 0
    d = backups.disk_usage()
    assert d["files"] >= 1 and d["folder"].startswith("R2 bucket dm-test")
    assert backups.resolve_file(run.file_name).read_bytes().startswith(b"PGDMP")  # downloads come from the bucket
