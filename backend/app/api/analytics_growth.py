"""Analytics extensions: growth trends, competitor benchmarks, marketing health score and tips, reports.

Mounted under /api/v1/analytics next to the original analytics router (GA4, Search Console, PageSpeed).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field, TypeAdapter, field_validator
from sqlalchemy import select

from app.core import storage
from app.core.db import SessionLocal, set_tenant
from app.core.deps import Context, require
from app.models import (
    AnalyticsReport, AnalyticsSnapshot, Competitor, Opportunity, Recommendation, ReportSchedule, Schedule,
)
from app.services import analytics_competitors as comp
from app.services import analytics_growth as growth
from app.services import analytics_health as health
from app.services import analytics_reports as reports
from app.services import audit
from app.services.jobs import enqueue

router = APIRouter(prefix="/analytics", tags=["analytics"])
VIEW, MANAGE = "analytics.view", "analytics.manage"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_RECIPIENTS = 20
_EMAILS = TypeAdapter(list[EmailStr])


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


# ================================================================== growth
@router.get("/growth")
async def get_growth(days: int = Query(30, ge=7, le=365), ctx: Context = Depends(require(VIEW))):
    return await growth.growth(ctx.session, ctx.org, days)


@router.post("/growth/refresh", status_code=202)
async def refresh_growth(scope: Literal["all", "accounts", "competitors"] = "all", ctx: Context = Depends(require(MANAGE))):
    job = await enqueue(ctx.session, "analytics.collect", ctx.org_id, {"scope": scope, "trigger": "manual",
                                                                       "websites": scope == "competitors"},
                        created_by=ctx.user.id, dedupe_key=f"analytics.collect:{ctx.org_id}")
    await ctx.session.commit()
    return {"job_id": job.id}


# ================================================================== competitors
class HandlesIn(BaseModel):
    instagram: str | None = Field(None, max_length=200)
    youtube: str | None = Field(None, max_length=200)
    bluesky: str | None = Field(None, max_length=200)
    mastodon: str | None = Field(None, max_length=200)


@router.get("/competitors")
async def get_competitors(ctx: Context = Depends(require(VIEW))):
    return await comp.benchmark(ctx.session, ctx.org)


@router.put("/competitors/{cid}/handles")
async def put_handles(cid: uuid.UUID, body: HandlesIn, ctx: Context = Depends(require(MANAGE))):
    c = await ctx.session.scalar(select(Competitor).where(Competitor.id == cid))
    if c is None:
        raise _bad("Competitor not found.", 404)
    social = {}
    for ch in comp.CHANNELS:
        try:
            h = comp.clean_handle(ch, getattr(body, ch))
        except comp.HandleError as e:
            raise _bad(f"{comp.CHANNEL_LABELS[ch]}: {e}")
        if h:
            social[ch] = h
    c.data = {**(c.data or {}), "social": social}
    audit.record(ctx.session, action="analytics.competitor_handles", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="competitor", entity_id=c.id, summary=f"Updated social handles for {c.name}", ip=ctx.ip)
    await ctx.session.commit()
    return {"id": str(c.id), "handles": social}


# ================================================================== health & tips
@router.get("/health")
async def get_health(ctx: Context = Depends(require(VIEW))):
    return await health.health(ctx.session, ctx.org)


@router.get("/health/summary")
async def health_summary(ctx: Context = Depends(require(VIEW))):
    """Small payload for the dashboard card: latest saved score (computed once a day) plus the top open tip."""
    snap = await ctx.session.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "health")
                                    .order_by(AnalyticsSnapshot.created_at.desc()))
    data = snap.data if snap else await health.compute(ctx.session, ctx.org)
    top = await ctx.session.scalar(select(Recommendation).where(Recommendation.status == "new").order_by(Recommendation.rank))
    return {"score": data.get("score"), "grade": data.get("grade"), "day": data.get("day"),
            "weakest": sorted([p for p in data.get("parts", []) if p.get("score") is not None], key=lambda p: p["score"])[:1],
            "top_tip": top.title if top else None}


@router.get("/recommendations")
async def list_recommendations(status_: Literal["new", "done", "dismissed", "all"] = Query("all", alias="status"),
                               ctx: Context = Depends(require(VIEW))):
    q = select(Recommendation).order_by(Recommendation.status != "new", Recommendation.rank, Recommendation.created_at.desc())
    if status_ != "all":
        q = q.where(Recommendation.status == status_)
    rows = (await ctx.session.scalars(q.limit(100))).all()
    last = await ctx.session.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "advisor")
                                    .order_by(AnalyticsSnapshot.created_at.desc()))
    return {"items": [health.rec_out(r) for r in rows],
            "last_run": {"at": last.created_at.isoformat(), **(last.data or {})} if last else None,
            "can_manage": MANAGE in ctx.permissions}


@router.post("/recommendations/generate", status_code=202)
async def generate_recommendations(ctx: Context = Depends(require(MANAGE))):
    job = await enqueue(ctx.session, "analytics.advisor", ctx.org_id, {"trigger": "manual"}, created_by=ctx.user.id,
                        dedupe_key=f"analytics.advisor:{ctx.org_id}")
    await ctx.session.commit()
    return {"job_id": job.id}


class RecPatch(BaseModel):
    status: Literal["new", "done", "dismissed"]


async def _rec(ctx: Context, rid: uuid.UUID) -> Recommendation:
    r = await ctx.session.scalar(select(Recommendation).where(Recommendation.id == rid))
    if r is None:
        raise _bad("That tip no longer exists. Refresh the page.", 404)
    return r


@router.patch("/recommendations/{rid}")
async def patch_recommendation(rid: uuid.UUID, body: RecPatch, ctx: Context = Depends(require(MANAGE))):
    r = await _rec(ctx, rid)
    r.status, r.status_changed_at, r.status_changed_by = body.status, datetime.now(UTC), ctx.user.id
    await ctx.session.commit()
    return health.rec_out(r)


@router.post("/recommendations/{rid}/apply")
async def apply_recommendation(rid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    """Run a tip's one-click action through the same logic (and permission checks) as the rest of the app."""
    r = await _rec(ctx, rid)
    if not r.action:
        raise _bad("This tip has no one-click action. Follow the steps described instead.")
    if r.status == "done" and r.result:
        raise _bad("This tip was already applied.", status.HTTP_409_CONFLICT)
    action = await health.validate_action(ctx.session, {"type": r.action.get("type"), "params": r.action.get("params")})
    if action is None:
        raise _bad("This action no longer fits your setup (a schedule or account may have changed). Generate fresh tips.")
    missing = sorted(health.ACTION_PERMS[action["type"]] - ctx.permissions)
    if missing:
        raise _bad(f"You need the '{missing[0]}' permission for this.", status.HTTP_403_FORBIDDEN)
    result, link, job_ids = await _apply(ctx, action)
    r = await _rec(ctx, rid)  # the content endpoints commit; reload
    r.status, r.status_changed_at, r.status_changed_by, r.result = "done", datetime.now(UTC), ctx.user.id, result
    audit.record(ctx.session, action="analytics.tip_applied", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="recommendation", entity_id=r.id, summary=f"Applied tip: {r.title[:120]}", ip=ctx.ip)
    await ctx.session.commit()
    return {"message": result, "link": link, "job_ids": job_ids, "recommendation": health.rec_out(r)}


async def _apply(ctx: Context, action: dict) -> tuple[str, str | None, list]:
    from app.api import content as content_api

    t, p = action["type"], action["params"]
    if t == "add_schedule_times":
        s = await ctx.session.get(Schedule, uuid.UUID(p["schedule_id"]))
        times = sorted(set((s.times or []) + p["times"]))
        await content_api.update_schedule(s.id, content_api.SchedulePatch(times=times), ctx)
        return f"Added {', '.join(p['times'])} to “{s.name}”.", "/settings/schedules", []
    if t == "set_post_mix":
        s = await ctx.session.get(Schedule, uuid.UUID(p["schedule_id"]))
        await content_api.update_schedule(s.id, content_api.SchedulePatch(post_types=p["post_types"]), ctx)
        mix = ", ".join(f"{k} {v:g}" for k, v in p["post_types"].items())
        return f"“{s.name}” now uses this format mix: {mix}.", "/settings/schedules", []
    if t == "create_schedule":
        out = await content_api.add_schedule(content_api.ScheduleIn(
            social_account_id=uuid.UUID(p["social_account_id"]), name="Suggested schedule",
            days_of_week=p["days_of_week"], times=p["times"], post_types=p["post_types"], mode="approve_first"), ctx)
        return (f"Created a schedule for {out['account']['name'] if out.get('account') else 'the account'}. "
                "Posts wait for your approval before they go out.", "/settings/schedules", [])
    if t == "draft_posts":
        out = await content_api.compose(content_api.ComposeIn(
            account_ids=[uuid.UUID(a) for a in p["account_ids"]], brief=p["topic"], post_type=p.get("post_type"),
            then="draft"), ctx)
        n = len(out["posts"])
        return (f"Writing {n} draft post{'s' if n != 1 else ''}. They'll appear in All posts as drafts.", "/posts",
                [str(x["job_id"]) for x in out["posts"]])
    if t == "add_topic_ideas":
        existing = {o.lower() for o in (await ctx.session.scalars(select(Opportunity.title))).all()}
        added = 0
        for i in p["ideas"]:
            if i["title"].lower() in existing:
                continue
            ctx.session.add(Opportunity(organization_id=ctx.org_id, title=i["title"], angle=i.get("angle"), source="manual",
                                        score=0.7, status="new", data={"origin": "growth_advisor"}))
            added += 1
        await ctx.session.commit()
        return f"Added {added} topic idea{'s' if added != 1 else ''} to Company intelligence.", "/intelligence", []
    raise _bad("Unknown action.")


# ================================================================== reports
class ReportIn(BaseModel):
    period: Literal["7", "30", "90", "last_month", "custom"] = "30"
    start: date | None = None
    end: date | None = None
    recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)

    @field_validator("recipients")
    @classmethod
    def _emails(cls, v: list[str]) -> list[str]:
        return clean_emails(v)


def clean_emails(v: list[str]) -> list[str]:
    v = [x.strip() for x in v if x and x.strip()]
    try:
        return list(dict.fromkeys(str(e).lower() for e in _EMAILS.validate_python(v)))
    except Exception as e:  # noqa: BLE001 - pydantic error -> friendly message
        bad = next((x for x in v if "@" not in x or "." not in x.split("@")[-1]), v[0] if v else "")
        raise ValueError(f"“{bad}” isn't a valid email address.") from e


def _period(body: ReportIn, today: date) -> tuple[date, date]:
    if body.period == "custom":
        if not body.start or not body.end:
            raise _bad("Pick a start and an end date.")
        if body.end < body.start:
            raise _bad("The end date must be after the start date.")
        if body.end > today:
            raise _bad("The end date can't be in the future.")
        if (body.end - body.start).days > 366:
            raise _bad("Reports can cover at most a year.")
        return body.start, body.end
    if body.period == "last_month":
        return reports.default_period("monthly", today)
    n = int(body.period)
    end = today - timedelta(days=1)
    return end - timedelta(days=n - 1), end


@router.get("/reports")
async def list_reports(ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(AnalyticsReport).order_by(AnalyticsReport.created_at.desc()).limit(50))).all()
    return {"items": [reports.report_out(r) for r in rows], "email_ready": await reports.smtp_config(ctx.session) is not None,
            "can_manage": MANAGE in ctx.permissions}


@router.post("/reports", status_code=202)
async def create_report(body: ReportIn, ctx: Context = Depends(require(MANAGE))):
    start, end = _period(body, growth.local_today(ctx.org))
    r = AnalyticsReport(organization_id=ctx.org_id, title=reports.report_title(ctx.org, start, end), period_start=start,
                        period_end=end, status="queued", trigger="manual", recipients=body.recipients, created_by=ctx.user.id)
    ctx.session.add(r)
    await ctx.session.flush()
    job = await enqueue(ctx.session, "analytics.report", ctx.org_id, {"report_id": str(r.id)}, created_by=ctx.user.id,
                        priority=2, dedupe_key=f"analytics.report:{r.id}")
    await ctx.session.commit()
    return {"report": reports.report_out(r), "job_id": job.id}


async def _report(ctx: Context, rid: uuid.UUID) -> AnalyticsReport:
    r = await ctx.session.scalar(select(AnalyticsReport).where(AnalyticsReport.id == rid))
    if r is None:
        raise _bad("Report not found.", 404)
    return r


@router.get("/reports/{rid}")
async def get_report(rid: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    return reports.report_out(await _report(ctx, rid))


@router.get("/reports/{rid}/file", include_in_schema=False)
async def report_file(rid: str, o: str, e: int, s: str):
    """Signed link from report_out()['download_url'], so the browser can download without an auth header."""
    try:
        rid_u, org_u = uuid.UUID(rid), uuid.UUID(o)
    except ValueError:
        raise _bad("Link not valid.", 404)
    if not reports.verify(str(rid_u), str(org_u), e, s):
        raise _bad("This link has expired. Reload the page to get a fresh one.", status.HTTP_403_FORBIDDEN)
    async with SessionLocal() as session:
        set_tenant(session, org_u)
        r = await session.scalar(select(AnalyticsReport).where(AnalyticsReport.id == rid_u))
    if r is None or not r.path:
        raise _bad("Report not found.", 404)
    try:
        path = storage.abs_path(r.path)
    except ValueError:
        raise _bad("Report not found.", 404)
    if not path.exists():
        raise _bad("The report file is missing. Generate it again.", 404)
    name = f"marketing-report-{r.period_start.isoformat()}-to-{r.period_end.isoformat()}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=name, headers={"Cache-Control": "private, max-age=600"})


class SendIn(BaseModel):
    recipients: list[str] = Field(min_length=1, max_length=MAX_RECIPIENTS)

    @field_validator("recipients")
    @classmethod
    def _emails(cls, v: list[str]) -> list[str]:
        return clean_emails(v)


@router.post("/reports/{rid}/send", status_code=202)
async def send_report(rid: uuid.UUID, body: SendIn, ctx: Context = Depends(require(MANAGE))):
    """Email an existing report (it's re-rendered so the numbers are current for the same period)."""
    old = await _report(ctx, rid)
    r = AnalyticsReport(organization_id=ctx.org_id, title=old.title, period_start=old.period_start, period_end=old.period_end,
                        status="queued", trigger="send_now", recipients=body.recipients, created_by=ctx.user.id)
    ctx.session.add(r)
    await ctx.session.flush()
    job = await enqueue(ctx.session, "analytics.report", ctx.org_id, {"report_id": str(r.id)}, created_by=ctx.user.id,
                        priority=2, dedupe_key=f"analytics.report:{r.id}")
    await ctx.session.commit()
    return {"report": reports.report_out(r), "job_id": job.id}


@router.delete("/reports/{rid}", status_code=204)
async def delete_report(rid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    r = await _report(ctx, rid)
    if r.path:
        try:
            storage.abs_path(r.path).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    await ctx.session.delete(r)
    await ctx.session.commit()


# ------------------------------------------------------------------ report schedules
def check_time(v: str) -> str:
    v = v.strip()
    if not TIME_RE.match(v):
        raise ValueError("Use a 24-hour time like 09:00.")
    return v


def check_recipients(v: list[str]) -> list[str]:
    v = clean_emails(v)
    if not v:
        raise ValueError("Add at least one email address.")
    return v


class ScheduleIn(BaseModel):
    name: str = Field("Weekly marketing report", min_length=1, max_length=160)
    frequency: Literal["weekly", "monthly"] = "weekly"
    weekday: int = Field(0, ge=0, le=6)
    month_day: int = Field(1, ge=1, le=28)
    time: str = "09:00"
    recipients: list[str] = Field(min_length=1, max_length=MAX_RECIPIENTS)
    active: bool = True

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        return check_time(v)

    @field_validator("recipients")
    @classmethod
    def _emails(cls, v: list[str]) -> list[str]:
        return check_recipients(v)


class SchedulePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)
    frequency: Literal["weekly", "monthly"] | None = None
    weekday: int | None = Field(None, ge=0, le=6)
    month_day: int | None = Field(None, ge=1, le=28)
    time: str | None = None
    recipients: list[str] | None = Field(None, min_length=1, max_length=MAX_RECIPIENTS)
    active: bool | None = None

    @field_validator("time")
    @classmethod
    def _time(cls, v: str | None) -> str | None:
        return None if v is None else check_time(v)

    @field_validator("recipients")
    @classmethod
    def _emails(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else check_recipients(v)


async def _sched(ctx: Context, sid: uuid.UUID) -> ReportSchedule:
    s = await ctx.session.scalar(select(ReportSchedule).where(ReportSchedule.id == sid))
    if s is None:
        raise _bad("Report schedule not found.", 404)
    return s


@router.get("/report-schedules")
async def list_report_schedules(ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(ReportSchedule).order_by(ReportSchedule.created_at))).all()
    return [reports.schedule_out(s) for s in rows]


@router.post("/report-schedules", status_code=201)
async def add_report_schedule(body: ScheduleIn, ctx: Context = Depends(require(MANAGE))):
    s = ReportSchedule(organization_id=ctx.org_id, created_by=ctx.user.id, **body.model_dump())
    s.next_run_at = reports.next_run(s, growth.org_zone(ctx.org), datetime.now(UTC))
    ctx.session.add(s)
    await ctx.session.flush()
    audit.record(ctx.session, action="analytics.report_schedule_added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="report_schedule", entity_id=s.id, summary=f"Added report schedule {s.name}", ip=ctx.ip)
    await ctx.session.commit()
    return reports.schedule_out(s)


@router.patch("/report-schedules/{sid}")
async def patch_report_schedule(sid: uuid.UUID, body: SchedulePatch, ctx: Context = Depends(require(MANAGE))):
    s = await _sched(ctx, sid)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(s, k, v)
    s.next_run_at = reports.next_run(s, growth.org_zone(ctx.org), datetime.now(UTC))
    audit.record(ctx.session, action="analytics.report_schedule_updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="report_schedule", entity_id=s.id, summary=f"Updated report schedule {s.name}", ip=ctx.ip)
    await ctx.session.commit()
    return reports.schedule_out(s)


@router.delete("/report-schedules/{sid}", status_code=204)
async def delete_report_schedule(sid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    s = await _sched(ctx, sid)
    audit.record(ctx.session, action="analytics.report_schedule_deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="report_schedule", entity_id=s.id, summary=f"Deleted report schedule {s.name}", ip=ctx.ip)
    await ctx.session.delete(s)
    await ctx.session.commit()


@router.post("/report-schedules/{sid}/send-now", status_code=202)
async def send_schedule_now(sid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    s = await _sched(ctx, sid)
    start, end = reports.default_period(s.frequency, growth.local_today(ctx.org))
    r = AnalyticsReport(organization_id=ctx.org_id, title=reports.report_title(ctx.org, start, end), period_start=start,
                        period_end=end, status="queued", trigger="send_now", schedule_id=s.id,
                        recipients=list(s.recipients or []), created_by=ctx.user.id)
    ctx.session.add(r)
    await ctx.session.flush()
    job = await enqueue(ctx.session, "analytics.report", ctx.org_id, {"report_id": str(r.id)}, created_by=ctx.user.id,
                        priority=2, dedupe_key=f"analytics.report:{r.id}")
    await ctx.session.commit()
    return {"report": reports.report_out(r), "job_id": job.id}
