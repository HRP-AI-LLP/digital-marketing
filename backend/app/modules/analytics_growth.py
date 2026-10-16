"""Growth analytics background work.

Jobs:
  analytics.collect   daily account snapshots + competitor benchmarks + today's health score  {scope: all|accounts|competitors}
  analytics.advisor   a fresh batch of growth tips (AI, or built-in rules without a model)
  analytics.report    build the PDF report, store it and email the recipients            {report_id}
Periodic:
  analytics_growth_daily     collect once a day after COLLECT_HOUR local time
  analytics_advisor_weekly   new tips when the last batch is more than a week old
  analytics_report_schedules send due weekly/monthly reports
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import AccountMetric, AnalyticsReport, AnalyticsSnapshot, Organization, ReportSchedule, SocialAccount
from app.services import analytics_competitors as comp
from app.services import analytics_growth as growth
from app.services import analytics_health as health
from app.services import analytics_reports as reports
from app.services.jobs import JobContext, PermanentError, enqueue_now, handler, periodic

log = logging.getLogger(__name__)

COLLECT_HOUR = 5  # org local time
ADVISOR_DAYS = 7


@handler("analytics.collect")
async def h_collect(ctx: JobContext) -> dict:
    scope = (ctx.payload or {}).get("scope") or "all"
    out: dict = {}
    if scope in ("all", "accounts"):
        await ctx.progress("Reading follower counts from your accounts")
        out["accounts"] = await growth.collect_accounts(ctx.session, ctx.org_id)
    if scope in ("all", "competitors"):
        await ctx.progress("Reading competitors' public numbers")
        out["competitors"] = await comp.collect_competitors(ctx.session, ctx.org_id,
                                                            websites=True if (ctx.payload or {}).get("websites") else None)
    await ctx.progress("Updating the marketing health score")
    org = await ctx.session.get(Organization, ctx.org_id)
    score = await health.compute(ctx.session, org)
    await health.save_snapshot(ctx.session, ctx.org_id, score)
    out["score"] = score["score"]
    acc = out.get("accounts") or {}
    bits = []
    if "accounts" in out:
        bits.append(f"Updated {len(acc.get('collected', []))} account(s)")
        if acc.get("failed"):
            bits.append("couldn't read " + ", ".join(f"{f['account']} ({f['error']})" for f in acc["failed"][:3]))
    if "competitors" in out:
        c = out["competitors"]
        bits.append(f"{c['collected']} competitor profile(s) refreshed")
        if c["failed"]:
            bits.append(f"{len(c['failed'])} competitor check(s) failed")
    out["message"] = "; ".join(bits).capitalize() + "." if bits else "Done."
    return out


@handler("analytics.advisor")
async def h_advisor(ctx: JobContext) -> dict:
    org = await ctx.session.get(Organization, ctx.org_id)
    await ctx.progress("Reading your numbers and writing tips")
    return await health.generate(ctx.session, org)


@handler("analytics.report")
async def h_report(ctx: JobContext) -> dict:
    rid = (ctx.payload or {}).get("report_id")
    if not rid:
        raise PermanentError("No report id.")
    await ctx.progress("Building the report")
    result = await reports.produce(ctx.session, uuid.UUID(str(rid)))
    report = await ctx.session.get(AnalyticsReport, uuid.UUID(str(rid)))
    if report is not None and report.status == "ready" and report.trigger == "schedule":
        await ctx.session.commit()
        from app.services.notify import notify_org

        body = (f"Emailed to {', '.join(report.recipients)}." if report.sent_at else report.email_error or "Ready to download.")
        await notify_org(ctx.org_id, "analytics_report", report.title, body, link="/analytics?tab=reports",
                         permission="analytics.view")
    return result


# ------------------------------------------------------------------ periodic
async def _orgs() -> list[Organization]:
    async with platform_session() as s:
        return list((await s.scalars(select(Organization).where(Organization.status == "active"))).all())


@periodic(1800, name="analytics_growth_daily")
async def growth_daily() -> None:
    for org in await _orgs():
        now = datetime.now(growth.org_zone(org))
        if now.hour < COLLECT_HOUR:
            continue
        async with SessionLocal() as s:
            set_tenant(s, org.id)
            has_accounts = await s.scalar(select(func.count()).select_from(SocialAccount)
                                          .where(SocialAccount.platform != "google", SocialAccount.status != "removed"))
            if not has_accounts:
                continue
            done_today = await s.scalar(select(func.count()).select_from(AccountMetric).where(AccountMetric.day == now.date()))
        if not done_today:
            await enqueue_now("analytics.collect", org.id, {"scope": "all", "trigger": "daily"},
                              dedupe_key=f"analytics.collect:{org.id}")


@periodic(3600, name="analytics_advisor_weekly")
async def advisor_weekly() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=ADVISOR_DAYS)
    for org in await _orgs():
        async with SessionLocal() as s:
            set_tenant(s, org.id)
            last = await s.scalar(select(AnalyticsSnapshot.created_at).where(AnalyticsSnapshot.source == "advisor")
                                  .order_by(AnalyticsSnapshot.created_at.desc()))
            has_accounts = await s.scalar(select(func.count()).select_from(SocialAccount).where(SocialAccount.platform != "google"))
        if has_accounts and (last is None or last < cutoff):
            await enqueue_now("analytics.advisor", org.id, {"trigger": "weekly"}, dedupe_key=f"analytics.advisor:{org.id}")


async def due_schedules(now: datetime | None = None) -> list[uuid.UUID]:
    """Create report rows for schedules that are due and move each schedule to its next run. Returns report ids."""
    now = now or datetime.now(UTC)
    created: list[tuple[uuid.UUID, uuid.UUID]] = []
    async with platform_session() as s:
        rows = (await s.scalars(select(ReportSchedule).where(ReportSchedule.active.is_(True),
                                                             ReportSchedule.next_run_at <= now))).all()
        orgs = {o.id: o for o in (await s.scalars(select(Organization).where(
            Organization.id.in_({r.organization_id for r in rows}) if rows else Organization.id.is_(None)))).all()}
        for sch in rows:
            org = orgs.get(sch.organization_id)
            if org is None or org.status != "active":
                continue
            tz = growth.org_zone(org)
            start, end = reports.default_period(sch.frequency, now.astimezone(tz).date())
            rep = AnalyticsReport(organization_id=org.id, title=reports.report_title(org, start, end), period_start=start,
                                  period_end=end, status="queued", trigger="schedule", schedule_id=sch.id,
                                  recipients=list(sch.recipients or []), created_by=sch.created_by)
            s.add(rep)
            sch.next_run_at = reports.next_run(sch, tz, now)
            await s.flush()
            created.append((org.id, rep.id))
        await s.commit()
    for org_id, rid in created:
        await enqueue_now("analytics.report", org_id, {"report_id": str(rid)}, dedupe_key=f"analytics.report:{rid}")
    return [rid for _, rid in created]


@periodic(300, name="analytics_report_schedules")
async def report_schedules() -> None:
    await due_schedules()
