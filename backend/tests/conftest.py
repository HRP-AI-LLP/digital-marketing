"""Test setup: a throw-away database `<name>_test` is created, migrated (with RLS) and used
through the non-superuser app role, so tenant isolation is tested for real.

Requires the Podman database to be running (start.ps1) or any PostgreSQL with the same roles.
"""

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import pytest

from app.core.config import get_settings

_base = get_settings()


def _with_db(url: str, db: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{db}"))


TEST_DB = os.environ.get("TEST_DB_NAME") or urlsplit(_base.database_url).path.lstrip("/") + "_test"
os.environ["DATABASE_URL"] = _with_db(_base.database_url, TEST_DB)
os.environ["MIGRATION_DATABASE_URL"] = _with_db(_base.migration_database_url, TEST_DB)
os.environ["DB_NULL_POOL"] = "true"
os.environ["PLATFORM_ADMIN_EMAIL"] = "admin@hrpai.in"
os.environ["PLATFORM_ADMIN_PASSWORD"] = "Admin12345!"
os.environ["SECRET_KEY"] = "test-secret-key-0123456789-abcdefghijklmnop"
get_settings.cache_clear()


def _create_db() -> None:
    import asyncpg

    async def run():
        owner = urlsplit(_base.migration_database_url)
        app_user = urlsplit(_base.database_url).username
        dsn = urlunsplit(owner._replace(scheme="postgresql", path="/postgres"))
        conn = await asyncpg.connect(dsn)
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", TEST_DB
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
        await conn.close()
        conn = await asyncpg.connect(urlunsplit(owner._replace(scheme="postgresql", path=f"/{TEST_DB}")))
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {app_user}")
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_user}"
        )
        await conn.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app_user}")
        await conn.close()

    asyncio.run(run())

    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")


_create_db()


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.asyncio(loop_scope="session"))


@pytest.fixture(scope="session")
async def client():
    import httpx

    from app.main import app
    from app.services.tenancy import bootstrap

    await bootstrap()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


async def login(client, email: str, password: str, org_id: str | None = None) -> dict:
    r = await client.post("/auth/login", json={"email": email, "password": password, "organization_id": org_id})
    assert r.status_code == 200, r.text
    return r.json()


def auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture(scope="session")
async def admin(client) -> dict:
    """Platform admin (also Administrator of HRP AI LLP), with the first-login password changed."""
    t = await login(client, "admin@hrpai.in", "Admin12345!")
    if t["user"]["must_change_password"]:
        r = await client.post("/auth/change-password", headers=auth(t),
                              json={"current_password": "Admin12345!", "new_password": "Str0ngPassw0rd!"})
        assert r.status_code == 200, r.text
        t = r.json()
    return t
