"""Analytics background jobs: intel.analytics (GA4 + Search Console + PageSpeed) and a daily refresh."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import Organization
from app.services import analytics
from app.services.jobs import JobContext, enqueue_now, handler, periodic

log = logging.getLogger(__name__)


@handler("intel.analytics")
async def refresh_analytics(ctx: JobContext) -> dict:
    return await analytics.refresh_all(ctx.session, ctx.org_id, ctx.progress)


def _local_now(tz: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz or "Asia/Kolkata"))
    except Exception:  # noqa: BLE001
        return datetime.now(ZoneInfo("Asia/Kolkata"))


async def daily_due_orgs() -> list:
    """Orgs where it is past 06:00 local time and the last refresh happened before today's 06:00
    (or more than 20 hours ago)."""
    async with platform_session() as s:
        orgs = (await s.scalars(select(Organization).where(Organization.status == "active"))).all()
    due = []
    for org in orgs:
        now = _local_now(org.timezone)
        if now.hour < analytics.DAILY_HOUR:
            continue
        today_run = now.replace(hour=analytics.DAILY_HOUR, minute=0, second=0, microsecond=0)
        async with SessionLocal() as s:
            set_tenant(s, org.id)
            if not org.website and not (await analytics.google_status(s))["connected"]:
                continue
            last = await analytics.latest(s, "refresh")
            if last is None or last.created_at < today_run or await analytics.needs_refresh(s):
                due.append(org.id)
    return due


@periodic(1800, name="analytics_daily_refresh")
async def analytics_daily() -> None:
    for org_id in await daily_due_orgs():
        await enqueue_now("intel.analytics", org_id, {"trigger": "daily"}, dedupe_key=f"intel.analytics:{org_id}")
