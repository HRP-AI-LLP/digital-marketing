"""One-time move of the laptop's studio to the cloud UAT environment (run on the laptop by infra/move-to-uat.ps1).

  1. copies the whole local database (the HRP AI LLP organization, users, connections, posts, history) into the
     Neon UAT project (dm-uat), in one transaction (all or nothing);
  2. creates the runtime role dm_app there (not a superuser, so row-level security applies) with a new random
     password, and grants it access;
  3. uploads the media library (storage/orgs, music, …) to the R2 bucket, skipping files already there (re-runnable);
  4. hands the values the cloud needs to Secret Manager: with gcloud on this laptop it stores them directly,
     otherwise it copies them one by one to the clipboard for you to paste into Cloud Shell (infra/gcp/secrets.sh).

Nothing secret is printed. Connection strings and keys are typed into hidden prompts.
"""

from __future__ import annotations

import asyncio
import base64
import getpass
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from app.core.config import asyncpg_url, get_settings

CONTAINER = "hrp-dm-postgres"
SKIP_DIRS = {"backups", "logs", "cache", "tmp", "_updates", "_cloud"}
APP_ROLE = "dm_app"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"\n==> {msg}")


def fail(msg: str) -> None:
    say(f"\n[x] {msg}")
    raise SystemExit(1)


def libpq_url(raw: str) -> str:
    """A cloud connection string as libpq wants it, with sslmode=require and a host that keeps one server connection
    per client (advisory locks and prepared statements need that): Neon's direct host (no -pooler), or Supabase's
    Session pooler on port 5432."""
    raw = raw.strip().strip('"').strip("'")
    if raw.startswith("psql "):
        raw = raw[5:].strip().strip("'\"")
    if "[YOUR-PASSWORD]" in raw:
        fail("Put the database password in place of [YOUR-PASSWORD] first.")
    p = urlsplit(raw)
    if p.scheme not in ("postgres", "postgresql", "postgresql+asyncpg"):
        fail("That doesn't look like a connection string (it should start with postgresql://).")
    host = (p.hostname or "")
    if not host or host in ("localhost", "127.0.0.1"):
        fail("That is a local database. Paste the Neon dm-uat connection string.")
    if host.startswith("db.") and host.endswith(".supabase.co"):
        fail("That is the Direct connection (IPv6 only). In Supabase → Connect, pick \"Session pooler\" and copy that one.")
    netloc = p.netloc
    if host.endswith(".neon.tech"):
        netloc = netloc.replace("-pooler.", ".")  # Neon's pooler runs in transaction mode: use the direct host
    if host.endswith("pooler.supabase.com") and p.port == 6543:
        netloc = netloc.rsplit(":", 1)[0] + ":5432"  # transaction pooler -> session pooler
    q = dict(parse_qsl(p.query))
    q["sslmode"] = "require"
    q.pop("ssl", None)
    return urlunsplit(("postgresql", netloc, p.path if p.path not in ("", "/") else "/postgres", urlencode(q), ""))


def with_user(url: str, user: str, password: str) -> str:
    """Same server and database, another login. Supabase's pooler names users <role>.<project ref>: keep the ref."""
    p = urlsplit(url)
    host = p.netloc.rsplit("@", 1)[-1]
    if p.username and "." in p.username:
        user = f"{user}.{p.username.split('.', 1)[1]}"
    return urlunsplit((p.scheme, f"{user}:{quote(password, safe='')}@{host}", p.path, p.query, ""))


def encryption_key() -> str:
    s = get_settings()
    if s.encryption_key:
        return s.encryption_key
    return base64.urlsafe_b64encode(hashlib.sha256(("dm-secrets:" + s.secret_key).encode()).digest()).decode()


def local_db() -> tuple[str, str]:
    p = urlsplit(get_settings().migration_database_url)
    return p.username or "dm_owner", (p.path or "/digital_marketing").lstrip("/")


def podman(args: list[str], stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["podman", *args], input=stdin, capture_output=True, text=True)
    if check and r.returncode != 0:
        fail(f"podman {args[0]} {args[1] if len(args) > 1 else ''} failed:\n{(r.stderr or r.stdout)[-1500:]}")
    return r


async def target_state(url: str) -> dict:
    import asyncpg

    conn = await asyncpg.connect(asyncpg_url(url).replace("postgresql+asyncpg://", "postgresql://"))
    try:
        ver = await conn.fetchval("SHOW server_version_num")
        tables = await conn.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        users = 0
        if await conn.fetchval("SELECT to_regclass('public.users') IS NOT NULL"):
            users = await conn.fetchval("SELECT count(*) FROM users")
        return {"version": int(ver), "tables": tables, "users": users}
    finally:
        await conn.close()


async def sql(url: str, statements: list[str], args: list | None = None) -> list:
    import asyncpg

    conn = await asyncpg.connect(asyncpg_url(url).replace("postgresql+asyncpg://", "postgresql://"))
    out = []
    try:
        for i, q in enumerate(statements):
            out.append(await conn.fetch(q, *(args or [])) if i == 0 and args else await conn.fetch(q))
    finally:
        await conn.close()
    return out


async def reset_schema(url: str) -> None:
    """Remove a previous copy (tables, sequences, enum types, functions) but keep the schema itself and its
    Supabase-managed grants."""
    await sql(url, ["""DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', r.tablename); END LOOP;
      FOR r IN SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' LOOP
        EXECUTE format('DROP SEQUENCE IF EXISTS public.%I CASCADE', r.sequence_name); END LOOP;
      FOR r IN SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
               WHERE n.nspname = 'public' AND t.typtype = 'e' LOOP
        EXECUTE format('DROP TYPE IF EXISTS public.%I CASCADE', r.typname); END LOOP;
      FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
               LEFT JOIN pg_depend d ON d.objid = p.oid AND d.deptype = 'e'
               WHERE n.nspname = 'public' AND d.objid IS NULL LOOP
        EXECUTE 'DROP FUNCTION IF EXISTS ' || r.sig || ' CASCADE'; END LOOP;
    END $$"""])


async def make_app_role(url: str, password: str) -> None:
    import asyncpg

    from app.cli import grant_statements

    conn = await asyncpg.connect(asyncpg_url(url).replace("postgresql+asyncpg://", "postgresql://"))
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_ROLE)
        lit = await conn.fetchval("SELECT quote_literal($1)", password)
        await conn.execute(f"{'ALTER' if exists else 'CREATE'} ROLE {APP_ROLE} WITH LOGIN NOBYPASSRLS PASSWORD {lit}")
        for q in grant_statements():
            await conn.execute(q)
    finally:
        await conn.close()


async def counts(url: str) -> dict:
    rows = await sql(url, [
        "SELECT (SELECT count(*) FROM organizations) o, (SELECT count(*) FROM users) u, "
        "(SELECT count(*) FROM social_accounts) a, (SELECT count(*) FROM posts) p, (SELECT count(*) FROM media_assets) m"])
    r = rows[0][0]
    return {"organizations": r["o"], "users": r["u"], "social accounts": r["a"], "posts": r["p"], "media files": r["m"]}


def local_counts() -> dict:
    user, db = local_db()
    q = ("SELECT (SELECT count(*) FROM organizations), (SELECT count(*) FROM users), (SELECT count(*) FROM social_accounts),"
         " (SELECT count(*) FROM posts), (SELECT count(*) FROM media_assets)")
    r = podman(["exec", CONTAINER, "psql", "-U", user, "-d", db, "-At", "-F", ",", "-c", q])
    vals = [int(x) for x in r.stdout.strip().split(",")]
    return dict(zip(["organizations", "users", "social accounts", "posts", "media files"], vals))


# ------------------------------------------------------------------ files
def upload_storage(endpoint: str, bucket: str, key_id: str, secret_key: str) -> None:
    import boto3
    from botocore.config import Config

    from app.core.storage import guess_mime

    root = Path(get_settings().storage_dir)
    files = [p for p in root.rglob("*") if p.is_file() and p.relative_to(root).parts[0] not in SKIP_DIRS
             and not p.name.startswith(".") and len(p.relative_to(root).parts) > 1]
    total = sum(p.stat().st_size for p in files)
    say(f"    {len(files):,} files, {total / 1e9:.2f} GB")
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="auto", aws_access_key_id=key_id,
                      aws_secret_access_key=secret_key,
                      config=Config(retries={"max_attempts": 6, "mode": "standard"}, signature_version="s3v4",
                                    max_pool_connections=16))
    have: dict[str, int] = {}
    try:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for o in page.get("Contents", []) or []:
                have[o["Key"]] = o["Size"]
    except Exception as e:  # noqa: BLE001
        fail(f"Couldn't open the bucket {bucket}: {e}")
    todo = [p for p in files if have.get(p.relative_to(root).as_posix()) != p.stat().st_size]
    if len(todo) < len(files):
        say(f"    {len(files) - len(todo):,} already uploaded, {len(todo):,} to go")
    done = {"n": 0, "b": 0, "err": 0}
    t0 = time.time()

    def put(p: Path) -> None:
        key = p.relative_to(root).as_posix()
        try:
            s3.upload_file(str(p), bucket, key, ExtraArgs={"ContentType": guess_mime(key)})
            done["b"] += p.stat().st_size
        except Exception as e:  # noqa: BLE001
            done["err"] += 1
            say(f"    couldn't upload {key}: {str(e)[:160]}")
        done["n"] += 1
        if done["n"] % 100 == 0 or done["n"] == len(todo):
            rate = done["b"] / max(1, time.time() - t0) / 1e6
            say(f"    {done['n']:,}/{len(todo):,} files, {done['b'] / 1e9:.2f} GB ({rate:.1f} MB/s)")

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(put, todo))
    if done["err"]:
        fail(f"{done['err']} files didn't upload. Run the script again: it continues where it stopped.")


# ------------------------------------------------------------------ hand-off of the values
def gcloud() -> str | None:
    """The Google Cloud CLI: on PATH, or where its Windows installer puts it."""
    found = shutil.which("gcloud.cmd") or shutil.which("gcloud")
    if found:
        return found
    if sys.platform == "win32":
        for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            exe = Path(base or "") / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin" / "gcloud.cmd"
            if base and exe.is_file():
                return str(exe)
    return None


def _gc(gc: str, args: list[str], value: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([gc, *args], input=value, capture_output=True, text=True)


def _has(gc: str, project: str, name: str) -> bool:
    r = _gc(gc, ["secrets", "versions", "list", name, f"--project={project}", "--limit=1", "--format=value(name)"])
    return r.returncode == 0 and bool(r.stdout.strip())


def to_secret_manager(gc: str, project: str, values: list[tuple[str, str]], env: str = "uat") -> None:
    for name, value in values:
        r = _gc(gc, ["secrets", "versions", "add", name, f"--project={project}", "--data-file=-"], value)
        if r.returncode != 0:
            fail(f"Couldn't store {name}: {(r.stderr or '')[-400:]}")
        say(f"    stored {name}")
    # the values the cloud generates for itself (only when missing: changing them later would sign everyone out)
    generated = {
        f"dm-{env}-secret-key": secrets.token_urlsafe(48),
        f"dm-{env}-cron-secret": secrets.token_urlsafe(32),
        f"dm-{env}-origin-verify-secret": secrets.token_urlsafe(32),
        f"dm-{env}-admin-password": secrets.token_urlsafe(18) + "Aa1!",
    }
    for name, value in generated.items():
        if _has(gc, project, name):
            say(f"    {name} already set (kept)")
            continue
        r = _gc(gc, ["secrets", "versions", "add", name, f"--project={project}", "--data-file=-"], value)
        if r.returncode != 0:
            fail(f"Couldn't store {name}: {(r.stderr or '')[-400:]}")
        say(f"    generated {name}")


def _clip(value: str) -> None:
    if sys.platform == "win32":
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                       input=value, text=True, check=False)
    else:
        subprocess.run(["xclip", "-selection", "clipboard"], input=value, text=True, check=False)


def to_clipboard(values: list[tuple[str, str]]) -> None:
    say("    In Cloud Shell run:  ./infra/gcp/secrets.sh uat")
    say("    It asks for these values in this order. For each one: press Enter here (the value is copied to your")
    say("    clipboard), then paste it into the Cloud Shell prompt (Ctrl+V) and press Enter there.")
    for name, value in values:
        input(f"\n    Press Enter to copy  {name}  ... ")
        _clip(value)
        say("    copied. Paste it into Cloud Shell now.")
    input("\n    Press Enter when the last one is pasted (the clipboard is then cleared) ... ")
    _clip(" ")


# ------------------------------------------------------------------ main
def hand_off(owner: str, app_url: str, key_id: str, secret_key: str) -> None:
    step("Values for the cloud (Secret Manager, environment uat)")
    values = [("dm-uat-migrator-url", owner), ("dm-uat-database-url", app_url),
              ("dm-uat-r2-access-key-id", key_id), ("dm-uat-r2-secret-access-key", secret_key),
              ("dm-uat-encryption-key", encryption_key())]
    gc = gcloud()
    project = os.environ.get("GCP_PROJECT_ID", "") or "hrp-digital-marketing"
    if gc and input("    gcloud is installed here. Store the values directly? [Y/n] ").strip().lower() != "n":
        project = input(f"    Google Cloud project [{project}]: ").strip() or project
        to_secret_manager(gc, project, values)
        say("    All 9 dm-uat values are in Secret Manager.")
    else:
        say("    FIRST, in Cloud Shell, start:  cd ~/digital-marketing && bash ./infra/gcp/secrets.sh uat")
        say("    and leave it waiting at its first question (\"Owner connection string\").")
        while input("    Type READY here once it's waiting: ").strip().upper() != "READY":
            pass
        to_clipboard(values)


def handoff_main() -> None:
    """Only step 4 again (the database and media are already in UAT): a new password for dm_app, then the values."""
    say("Hand the UAT values to Google Secret Manager again (the data and media stay as they are).")
    say("Neon console → project dm-uat → Connect → role dm_owner, database digital_marketing, pooling OFF.")
    owner = libpq_url(getpass.getpass("Paste the connection string (hidden) and press Enter: "))
    try:
        st = asyncio.run(target_state(owner))
    except Exception as e:  # noqa: BLE001
        fail(f"Couldn't connect to Neon: {str(e)[:300]}")
    if not st["users"]:
        fail("That database is empty: run infra\\move-to-uat.ps1 first.")
    say(f"    connected: {st['tables']} tables, {st['users']} users")
    app_pw = secrets.token_urlsafe(30)
    asyncio.run(make_app_role(owner, app_pw))  # new password: the old one was never stored
    app_url = with_user(owner, APP_ROLE, app_pw)
    asyncio.run(sql(app_url, ["SELECT 1"]))
    say("    dm_app has a new password, and it can sign in")
    key_id = getpass.getpass("dm-uat-media R2 Access Key ID (hidden): ").strip()
    secret_key = getpass.getpass("dm-uat-media R2 Secret Access Key (hidden): ").strip()
    if not (key_id and secret_key):
        fail("Both R2 values are needed.")
    hand_off(owner, app_url, key_id, secret_key)
    step("Done")
    say("    Check in Cloud Shell that all 9 dm-uat values say 'set', then push to GitHub for the first UAT deploy.")


def set_secret_main(name: str) -> None:
    """Replace one value in Google Secret Manager (typed into a hidden prompt), e.g. dm-uat-r2-secret-access-key."""
    gc = gcloud()
    if not gc:
        fail("Install the Google Cloud CLI first (winget install --id Google.CloudSDK -e), then: gcloud auth login")
    project = os.environ.get("GCP_PROJECT_ID", "") or "hrp-digital-marketing"
    value = getpass.getpass(f"New value for {name} (hidden): ").strip()
    if not value:
        fail("Nothing typed. Nothing was changed.")
    r = _gc(gc, ["secrets", "versions", "add", name, f"--project={project}", "--data-file=-"], value)
    if r.returncode != 0:
        fail(f"Couldn't store {name}: {(r.stderr or '')[-400:]}")
    say(f"    stored a new version of {name} ({len(value)} characters)")


def set_password_main(email: str) -> None:
    """Set a new sign-in password for one user in a cloud database (typed twice into hidden prompts)."""
    import asyncpg

    from app.core.security import hash_password

    email = email.strip().lower()
    say(f"New password for {email} in a cloud database.")
    say("Neon console → the project (dm-uat or dm-prod) → Connect → role dm_owner, database digital_marketing, pooling OFF.")
    owner = libpq_url(getpass.getpass("Paste the connection string (hidden) and press Enter: "))
    pw = getpass.getpass("New password (hidden, at least 10 characters): ")
    if len(pw) < 10:
        fail("Use at least 10 characters.")
    if getpass.getpass("Type it again (hidden): ") != pw:
        fail("The two passwords don't match. Nothing was changed.")

    async def run() -> str:
        conn = await asyncpg.connect(asyncpg_url(owner).replace("postgresql+asyncpg://", "postgresql://"))
        try:
            return await conn.execute(
                "UPDATE users SET password_hash = $1, must_change_password = false, is_active = true, "
                "token_version = token_version + 1 WHERE lower(email) = $2", hash_password(pw), email)
        finally:
            await conn.close()

    try:
        res = asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        fail(f"Couldn't update the password: {str(e)[:300]}")
    if res.endswith(" 0"):
        fail(f"There's no user {email} in that database. Nothing was changed.")
    say(f"    done: {email} can sign in with the new password (other signed-in sessions were signed out).")


def main() -> None:
    say(__doc__)
    if input("Type MOVE to start: ").strip() != "MOVE":
        fail("Nothing was changed.")

    step("Neon UAT database")
    say("    Neon console → project dm-uat → Connect → role dm_owner, database digital_marketing, \"Connection pooling\"")
    say("    OFF → copy the connection string.")
    owner = libpq_url(getpass.getpass("    Paste it here (hidden) and press Enter: "))
    try:
        st = asyncio.run(target_state(owner))
    except Exception as e:  # noqa: BLE001
        fail(f"Couldn't connect to Neon: {str(e)[:300]}")
    if st["version"] // 10000 < 16:
        fail("The project runs PostgreSQL older than 16. Create it with PostgreSQL 16.")
    if st["tables"]:
        say(f"    The UAT database already has {st['tables']} tables and {st['users']} users.")
        if input("    Type RESET to wipe it and copy the laptop's data again: ").strip() != "RESET":
            fail("Nothing was changed.")
        asyncio.run(reset_schema(owner))
        say("    wiped")
    else:
        say("    connected, empty: good")

    step("Copying the laptop database to Neon (one transaction: all or nothing)")
    user, db = local_db()
    before = local_counts()
    say("    laptop: " + ", ".join(f"{v:,} {k}" for k, v in before.items()))
    podman(["exec", CONTAINER, "pg_dump", "-U", user, "-d", db, "-Fc", "--no-owner", "--no-privileges",
            "-f", "/tmp/dm-uat.dump"])
    podman(["exec", CONTAINER, "sh", "-c",
            "pg_restore -l /tmp/dm-uat.dump | grep -v -e 'COMMENT - EXTENSION' > /tmp/dm-uat.list"])
    r = podman(["exec", "-i", CONTAINER, "sh", "-c",
                'read -r U; U=$(printf %s "$U" | tr -d "\\r"); pg_restore --no-owner --no-privileges --single-transaction --exit-on-error '
                '-L /tmp/dm-uat.list -d "$U" /tmp/dm-uat.dump'], stdin=owner + "\n", check=False)
    podman(["exec", CONTAINER, "rm", "-f", "/tmp/dm-uat.dump", "/tmp/dm-uat.list"], check=False)
    if r.returncode != 0:
        fail("The copy failed and was rolled back (UAT is unchanged):\n" + (r.stderr or r.stdout)[-1500:])
    after = asyncio.run(counts(owner))
    say("    UAT:    " + ", ".join(f"{v:,} {k}" for k, v in after.items()))
    if after != before:
        fail("The numbers don't match. Run the script again (type RESET when asked).")

    step("Runtime role dm_app (row-level security applies to it)")
    app_pw = secrets.token_urlsafe(30)
    asyncio.run(make_app_role(owner, app_pw))
    app_url = with_user(owner, APP_ROLE, app_pw)
    asyncio.run(sql(app_url, ["SELECT 1"]))
    say("    created, and it can sign in")

    step("Media files → Cloudflare R2 (bucket dm-uat-media)")
    say("    Cloudflare → R2 → Manage API tokens → the token for dm-uat-media (Object Read & Write).")
    account = input("    Cloudflare account ID: ").strip()
    bucket = input("    Bucket [dm-uat-media]: ").strip() or "dm-uat-media"
    key_id = getpass.getpass("    Access Key ID (hidden): ").strip()
    secret_key = getpass.getpass("    Secret Access Key (hidden): ").strip()
    if not (account and key_id and secret_key):
        fail("All three are needed. Run the script again (type RESET when asked).")
    upload_storage(f"https://{account}.r2.cloudflarestorage.com", bucket, key_id, secret_key)

    hand_off(owner, app_url, key_id, secret_key)

    step("Done")
    say("    The laptop's data is in UAT. Automatic work on this laptop is switched off (AUTOMATION_ENABLED=false).")
    say(f"    Media bucket: {bucket}.  R2 endpoint for GitHub (variable R2_ENDPOINT): "
        f"https://{account}.r2.cloudflarestorage.com")


if __name__ == "__main__":
    main()
