"""Listening background module: polls each organization's topics every `interval_hours` (2–4 h by default),
sends the daily/weekly digest, and writes the AI summary on request."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.db import platform_session
from app.models import Organization
from app.models.listening import ListeningQuery, ListeningSettings
from app.services.inbox.base import parse_time
from app.services.jobs import JobContext, enqueue_now, handler, periodic
from app.services.listening import engine, insights

log = logging.getLogger("listening")

SCHEDULE_EVERY_SECONDS = 900
DIGEST_EVERY_SECONDS = 1800


@handler("listening.run")
async def listening_run(ctx: JobContext) -> dict:
    """payload: {"force": true} for Run now; {"query_id": "..."} for one topic; {"sources": [...]} to limit sources."""
    p = ctx.payload or {}
    qids = [p["query_id"]] if p.get("query_id") else p.get("query_ids")
    await ctx.progress("Searching your listening sources…")
    return await engine.run(ctx.session, ctx.org_id, force=bool(p.get("force")), query_ids=qids, only=p.get("sources"))


@handler("listening.summary")
async def listening_summary(ctx: JobContext) -> dict:
    days = int((ctx.payload or {}).get("days") or 7)
    r = await insights.summarize(ctx.session, max(1, min(days, 90)))
    return {"themes": len(r.get("themes") or []), "method": r.get("method"), "count": r.get("count")}


@handler("listening.digest")
async def listening_digest(ctx: JobContext) -> dict:
    return await engine.send_digest(ctx.session, ctx.org_id, force=bool((ctx.payload or {}).get("force")))


async def _due_orgs(now: datetime) -> list[uuid.UUID]:
    async with platform_session() as s:
        rows = (await s.execute(
            select(ListeningSettings.organization_id, ListeningSettings.interval_hours, ListeningSettings.state)
            .join(Organization, Organization.id == ListeningSettings.organization_id)
            .where(ListeningSettings.enabled.is_(True), Organization.status == "active"))).all()
        with_queries = set((await s.scalars(select(ListeningQuery.organization_id).where(
            ListeningQuery.enabled.is_(True)).group_by(ListeningQuery.organization_id).having(func.count() > 0))).all())
    due = []
    for org_id, hours, state in rows:
        if org_id not in with_queries:
            continue
        last = parse_time((state or {}).get("last_run_at"))
        if last is None or now - last >= timedelta(hours=max(1, min(int(hours or 3), 24))) - timedelta(minutes=5):
            due.append(org_id)
    return due


@periodic(SCHEDULE_EVERY_SECONDS, name="listening_schedule")
async def schedule_listening() -> None:
    for org_id in await _due_orgs(datetime.now(UTC)):
        await enqueue_now("listening.run", org_id, {}, dedupe_key=f"listening.run:{org_id}", max_attempts=1, priority=8)


@periodic(DIGEST_EVERY_SECONDS, name="listening_digest")
async def schedule_digest() -> None:
    now = datetime.now(UTC)
    async with platform_session() as s:
        rows = (await s.execute(
            select(ListeningSettings, Organization.timezone)
            .join(Organization, Organization.id == ListeningSettings.organization_id)
            .where(ListeningSettings.enabled.is_(True), ListeningSettings.digest.in_(("daily", "weekly")),
                   Organization.status == "active"))).all()
    for settings, tz in rows:
        if engine.digest_due(settings, tz, now):
            await enqueue_now("listening.digest", settings.organization_id, {},
                              dedupe_key=f"listening.digest:{settings.organization_id}", max_attempts=1, priority=8)
