"""Social presence audit background work.

Job:      presence.audit   {trigger: manual|weekly}  collect every profile, score, store a PresenceRun + suggestions
Periodic: presence_weekly  a fresh audit when the last one is more than a week old
Testers:  youtube_data, x_api, github_api (Settings → Integrations → Test)
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select

from app.api.integrations import tester
from app.core.crypto import decrypt_json
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import Organization, PresenceRun, ProviderConfig, SocialAccount
from app.services import presence
from app.services.jobs import JobContext, enqueue_now, handler, periodic
from app.services.social import net

log = logging.getLogger(__name__)

WEEK_DAYS = 7


@handler("presence.audit")
async def h_audit(ctx: JobContext) -> dict:
    payload = ctx.payload or {}
    run = await presence.audit(ctx.session, ctx.org_id, trigger=payload.get("trigger") or "manual",
                               created_by=uuid.UUID(str(payload["created_by"])) if payload.get("created_by") else None, progress=ctx.progress)
    await ctx.session.commit()
    change = run.summary.get("change")
    if change is not None and abs(change) >= presence.NOTIFY_CHANGE:
        from app.services.notify import notify_org

        title = f"Social presence score {'up' if change > 0 else 'down'} {abs(change)} points to {run.score}"
        await notify_org(ctx.org_id, "presence_change", title,
                         "See what changed and the suggestions on the Social presence page.", link="/presence",
                         permission="presence.view")
    s = run.summary
    return {"run_id": str(run.id), "score": run.score, "profiles": s.get("profiles"),
            "message": f"Checked {s.get('profiles', 0)} profile(s). Score {run.score if run.score is not None else '—'}/100."}


async def _orgs() -> list[Organization]:
    async with platform_session() as s:
        return list((await s.scalars(select(Organization).where(Organization.status == "active"))).all())


@periodic(3600, name="presence_weekly")
async def presence_weekly() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=WEEK_DAYS)
    for org in await _orgs():
        has_links = any(isinstance(v, str) and v.strip() for v in (org.social_links or {}).values())
        async with SessionLocal() as s:
            set_tenant(s, org.id)
            last = await s.scalar(select(func.max(PresenceRun.created_at)))
            accounts = await s.scalar(select(func.count()).select_from(SocialAccount).where(SocialAccount.status != "removed",
                                                                                            SocialAccount.platform != "google"))
        if (has_links or accounts) and (last is None or last < cutoff):
            await enqueue_now("presence.audit", org.id, {"trigger": "weekly"}, dedupe_key=f"presence.audit:{org.id}", max_attempts=1)


# ------------------------------------------------------------------ Integrations testers
def _key(cfg: ProviderConfig) -> str:
    k = (decrypt_json(cfg.secrets_enc).get("api_key") or "").strip()
    if not k:
        raise ValueError("Add the key first.")
    return k


@tester("youtube_data")
async def test_youtube(cfg: ProviderConfig) -> str:
    from app.services.social.google import YOUTUBE_API

    async with net.client(timeout=20.0) as c:
        r = await c.get(f"{YOUTUBE_API}/channels", params={"part": "id", "forHandle": "@YouTube", "key": _key(cfg)})
    if r.status_code != 200:
        raise ValueError(f"YouTube rejected the key (HTTP {r.status_code}). Enable the YouTube Data API v3 for this key's project.")
    return "Connected. Public YouTube channels can now be read."


@tester("x_api")
async def test_x(cfg: ProviderConfig) -> str:
    async with net.client(timeout=20.0) as c:
        r = await c.get("https://api.x.com/2/users/by/username/X", headers={"Authorization": f"Bearer {_key(cfg)}"})
    if r.status_code != 200:
        raise ValueError(f"X rejected the token (HTTP {r.status_code}). Reading profiles needs a paid X API plan.")
    return "Connected. Public X profiles can now be read."


@tester("github_api")
async def test_github(cfg: ProviderConfig) -> str:
    async with net.client(timeout=20.0) as c:
        try:
            r = await c.get("https://api.github.com/rate_limit", headers={"Authorization": f"Bearer {_key(cfg)}"})
        except httpx.HTTPError as e:
            raise ValueError(f"GitHub couldn't be reached ({type(e).__name__}).") from e
    if r.status_code != 200:
        raise ValueError(f"GitHub rejected the token (HTTP {r.status_code}).")
    left = ((r.json().get("resources") or {}).get("core") or {}).get("remaining")
    return f"Connected. {left} GitHub requests left this hour." if left is not None else "Connected."
