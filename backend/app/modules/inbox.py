"""Inbox background module: comment/mention sync every 10 minutes, listening every hour."""

import logging
import uuid

from sqlalchemy import select

from app.core.db import platform_session
from app.models import SocialAccount
from app.models.inbox import InboxSettings
from app.services.inbox import listening, sentiment
from app.services.inbox.base import SYNC_PLATFORMS
from app.services.inbox.sync import get_settings, notify_new, sync_accounts
from app.services.jobs import JobContext, enqueue_now, handler, org_ids_active, periodic

log = logging.getLogger("inbox")

SYNC_EVERY_SECONDS = 600
LISTEN_EVERY_SECONDS = 3600


@handler("inbox.sync")
async def inbox_sync(ctx: JobContext) -> dict:
    """payload: {"account_id": "..."} to sync one account only."""
    aid = ctx.payload.get("account_id")
    r = await sync_accounts(ctx.session, ctx.org_id, uuid.UUID(str(aid)) if aid else None)
    sent = await notify_new(ctx.session, ctx.org_id, r["notify"])
    accounts = r["accounts"]
    return {"new": len([c for c in r["created"] if not c.is_own]), "notified": sent,
            "problems": [v.get("message") for v in accounts.values() if not v.get("ok", True) and v.get("message")][:5]}


@handler("inbox.listen")
async def inbox_listen(ctx: JobContext) -> dict:
    """payload: {"force": true} for an on-demand refresh (still at most every 10 minutes per source)."""
    settings = await get_settings(ctx.session, ctx.org_id)
    r = await listening.run(ctx.session, ctx.org_id, settings, force=bool(ctx.payload.get("force")),
                            only=ctx.payload.get("sources"))
    await ctx.session.commit()
    created = r["created"]
    if created:
        await sentiment.classify(ctx.session, created)
        await ctx.session.commit()
        await notify_new(ctx.session, ctx.org_id, created)
    return {"new": len(created), "sources": {k: {"ok": v.get("ok"), "found": v.get("found"), "message": v.get("message")}
                                             for k, v in r["sources"].items()}}


async def _orgs_with_accounts() -> set[uuid.UUID]:
    async with platform_session() as s:
        return set((await s.scalars(select(SocialAccount.organization_id).where(
            SocialAccount.platform.in_(SYNC_PLATFORMS), SocialAccount.status == "connected").distinct())).all())


async def _orgs_listening() -> set[uuid.UUID]:
    async with platform_session() as s:
        rows = (await s.execute(select(InboxSettings.organization_id, InboxSettings.keywords).where(
            InboxSettings.listening_enabled.is_(True)))).all()
    return {org for org, kws in rows if kws}


@periodic(SYNC_EVERY_SECONDS, name="inbox_sync")
async def schedule_inbox_sync() -> None:
    wanted = await _orgs_with_accounts()
    for org_id in await org_ids_active():
        if org_id in wanted:
            await enqueue_now("inbox.sync", org_id, {}, dedupe_key=f"inbox.sync:{org_id}", max_attempts=1, priority=7)


@periodic(LISTEN_EVERY_SECONDS, name="inbox_listen")
async def schedule_inbox_listen() -> None:
    wanted = await _orgs_listening()
    for org_id in await org_ids_active():
        if org_id in wanted:
            await enqueue_now("inbox.listen", org_id, {}, dedupe_key=f"inbox.listen:{org_id}", max_attempts=1, priority=8)
