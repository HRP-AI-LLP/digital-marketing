"""Operations commands used by the cloud deploy and the one-time move from the laptop.

  python -m app.cli grants                  give the runtime role (dm_app) access to every table (after migrations)
  python -m app.cli app-role <password>     create or update the dm_app login role (first setup of a database)
  python -m app.cli ensure-app-role         same, with the user and password in DATABASE_URL (the deploy runs it)
  python -m app.cli check                   print what the database holds (organizations, accounts, posts)
  python -m app.cli move-to-uat             one-time move of the laptop's data to the cloud UAT (infra/move-to-uat.ps1)

Uses MIGRATION_DATABASE_URL (the database owner).
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

APP_ROLE = "dm_app"


async def _run(sql: list[str], params: dict | None = None) -> list:
    eng = create_async_engine(get_settings().migration_database_url)
    out = []
    async with eng.begin() as c:
        for q in sql:
            r = await c.execute(text(q), params or {})
            if r.returns_rows:
                out.append(r.fetchall())
    await eng.dispose()
    return out


async def app_role(password: str) -> None:
    if len(password) < 16:
        raise SystemExit("Use a password of at least 16 characters.")
    await _run([
        "SELECT set_config('dm.pw', :pw, true)",
        """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dm_app') THEN
          EXECUTE format('CREATE ROLE dm_app LOGIN NOBYPASSRLS PASSWORD %L', current_setting('dm.pw'));
        ELSE
          EXECUTE format('ALTER ROLE dm_app WITH LOGIN NOBYPASSRLS PASSWORD %L', current_setting('dm.pw'));
        END IF; END $$""",
    ], {"pw": password})
    print("dm_app role is ready.")
    await grants()


async def ensure_app_role() -> None:
    from urllib.parse import unquote, urlsplit

    p = urlsplit(get_settings().database_url)
    # Supabase's pooler signs in as <role>.<project ref>
    if (p.username or "").split(".", 1)[0] != APP_ROLE or not p.password:
        raise SystemExit(f"DATABASE_URL must sign in as {APP_ROLE} with a password.")
    await app_role(unquote(p.password))


def grant_statements() -> list[str]:
    """Access for the runtime role, and (on Supabase) none for its public API roles anon / authenticated: the studio
    doesn't use Supabase's Data API, so its tables must not be reachable through it."""
    return [
        "GRANT USAGE ON SCHEMA public TO dm_app",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO dm_app",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO dm_app",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dm_app",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO dm_app",
        """DO $$ DECLARE r text; BEGIN
          FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
              EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', r);
              EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', r);
              EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', r);
              EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
              EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
              EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I', r);
            END IF;
          END LOOP; END $$""",
    ]


async def grants() -> None:
    await _run(grant_statements())
    print("Grants for dm_app are up to date.")


async def check() -> None:
    rows = await _run([
        "SELECT name, slug, status FROM organizations ORDER BY created_at",
        "SELECT platform, name, status FROM social_accounts ORDER BY platform",
        "SELECT status, count(*) FROM posts GROUP BY status ORDER BY 2 DESC",
        "SELECT count(*) FROM media_assets",
    ])
    for title, r in zip(("Organizations", "Social accounts", "Posts by status", "Media files"), rows):
        print(f"\n{title}:")
        for line in r:
            print("  " + " | ".join(str(x) for x in line))


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    cmd = argv[0]
    if cmd == "grants":
        asyncio.run(grants())
    elif cmd == "app-role" and len(argv) == 2:
        asyncio.run(app_role(argv[1]))
    elif cmd == "ensure-app-role":
        asyncio.run(ensure_app_role())
    elif cmd == "check":
        asyncio.run(check())
    elif cmd == "move-to-uat":
        from app.move_to_cloud import main as move

        move()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
