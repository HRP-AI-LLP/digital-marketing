"""Home dashboard: what needs attention, what's coming up, and setup progress."""

from datetime import UTC, datetime, timedelta

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.core.deps import Context, get_context, require
from app.services import dashboard_insights, system_map
from app.services.platforms import PLATFORMS
from app.models import (
    CompanyProfile, Job, Membership, PresenceAudit, Post, ProviderConfig, Schedule, SocialAccount,
)

router = APIRouter(tags=["dashboard"])

HIDDEN_ACCOUNT_PLATFORMS = ("google",)


def _dismissed_at(org) -> datetime | None:
    v = ((org.publishing if org is not None else None) or {}).get("alerts_dismissed_at")
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


@router.post("/dashboard/alerts/dismiss")
async def dismiss_alerts(ctx: Context = Depends(require("content.view"))):
    """Hide the failed-posts / failed-tasks warning for what has failed so far. Expired accounts keep showing."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.models import Organization
    from app.services import audit

    org = await ctx.session.get(Organization, ctx.org_id)
    pub = dict(org.publishing or {})
    pub["alerts_dismissed_at"] = datetime.now(UTC).isoformat()
    org.publishing = pub
    flag_modified(org, "publishing")
    audit.record(ctx.session, action="dashboard.alerts_dismissed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="organization", entity_id=ctx.org_id, summary="Dismissed the failure warning on the dashboard",
                 ip=ctx.ip)
    await ctx.session.commit()
    return {"dismissed_at": pub["alerts_dismissed_at"]}


@router.get("/dashboard")
async def dashboard(ctx: Context = Depends(get_context)):
    if ctx.org is None:
        return {"organization": None}
    s = ctx.session
    now = datetime.now(UTC)

    async def count(*where) -> int:
        return await s.scalar(select(func.count()).select_from(Post).where(*where)) or 0

    # failures from before "Dismiss" was pressed on the warning no longer count (new ones show again)
    dismissed = _dismissed_at(ctx.org)
    since_failed = max(now - timedelta(days=14), dismissed) if dismissed else now - timedelta(days=14)
    since_jobs = max(now - timedelta(days=2), dismissed) if dismissed else now - timedelta(days=2)

    counts = {
        "in_review": await count(Post.status == "in_review"),
        "scheduled_7d": await count(Post.status == "scheduled", Post.scheduled_at <= now + timedelta(days=7)),
        "published_7d": await count(Post.status == "published", Post.published_at >= now - timedelta(days=7)),
        "failed": await count(Post.status == "failed", Post.updated_at >= since_failed),
        "manual_pending": await count(Post.status == "manual_pending"),
    }
    upcoming_rows = (await s.execute(
        select(Post, SocialAccount.name)
        .join(SocialAccount, SocialAccount.id == Post.social_account_id, isouter=True)
        .where(Post.status.in_(["scheduled", "in_review", "planned", "generating"]), Post.scheduled_at >= now)
        .order_by(Post.scheduled_at).limit(6)
    )).all()
    upcoming = [
        {"id": p.id, "platform": p.platform, "account": name, "status": p.status, "post_type": p.post_type,
         "scheduled_at": p.scheduled_at, "topic": p.topic, "text": (p.text or "")[:160]}
        for p, name in upcoming_rows
    ]
    accounts = (await s.scalars(select(SocialAccount).where(
        SocialAccount.platform.notin_(HIDDEN_ACCOUNT_PLATFORMS), SocialAccount.status != "removed"))).all()
    llm_count = await s.scalar(select(func.count()).select_from(ProviderConfig).where(
        ProviderConfig.kind == "llm", ProviderConfig.enabled.is_(True))) or 0
    profile = await s.scalar(select(CompanyProfile))
    schedules = await s.scalar(select(func.count()).select_from(Schedule).where(Schedule.active.is_(True))) or 0
    members = await s.scalar(select(func.count()).select_from(Membership).where(Membership.is_active.is_(True))) or 0
    audit = await s.scalar(select(PresenceAudit).order_by(PresenceAudit.created_at.desc()).limit(1))
    from app.models import PresenceRun

    social_runs = (await s.scalars(select(PresenceRun).order_by(PresenceRun.created_at.desc()).limit(2))).all()
    failing_jobs = await s.scalar(select(func.count()).select_from(Job).where(
        Job.status == "failed", Job.finished_at >= since_jobs)) or 0

    setup = [
        {"key": "org", "title": "Add your organization details", "body": "Website and time zone. Posting times use this time zone.",
         "done": bool(ctx.org.website), "to": "/settings/organization"},
        {"key": "ai", "title": "Connect an AI model", "body": "Local Ollama, or a free Gemini or Groq key, writes and reviews your posts.",
         "done": llm_count > 0, "to": "/settings/integrations"},
        {"key": "intel", "title": "Build the company profile", "body": "Reads your website, finds competitors and suggests topics. Confirm what it found.",
         "done": bool(profile and profile.confirmed), "to": "/intelligence"},
        {"key": "accounts", "title": "Connect social accounts", "body": "LinkedIn, Facebook, Instagram, Pinterest, Google Business Profile and more.",
         "done": any(a.status in ("connected", "manual") for a in accounts), "to": "/settings/accounts"},
        {"key": "brand", "title": "Set your brand voice and topics", "body": "Voice, words to avoid, hashtags and the topic mix.",
         "done": False, "to": "/settings/brand"},
        {"key": "schedule", "title": "Create a posting schedule", "body": "How many posts, which days and times, and whether they need approval.",
         "done": schedules > 0, "to": "/settings/schedules"},
        {"key": "team", "title": "Invite your team", "body": "Add reviewers and content managers.", "done": members > 1, "to": "/settings/users"},
    ]
    from app.models import BrandProfile, ContentPillar  # local to keep imports grouped above minimal

    brand = await s.scalar(select(BrandProfile))
    pillars = await s.scalar(select(func.count()).select_from(ContentPillar).where(ContentPillar.active.is_(True))) or 0
    setup[4]["done"] = bool(brand and (brand.voice or brand.audience) and pillars > 0)

    return {
        "organization": {"id": ctx.org.id, "name": ctx.org.name, "timezone": ctx.org.timezone},
        "counts": counts,
        "upcoming": upcoming,
        "accounts": [{"id": a.id, "platform": a.platform, "name": a.name, "status": a.status,
                      "platform_label": PLATFORMS[a.platform].label if a.platform in PLATFORMS
                      else ("Google sign-in" if a.platform == "google" else a.platform.replace("_", " ").title())}
                     for a in accounts],
        "setup": setup,
        "audit_score": audit.score if audit else None,
        "social_presence": ({"score": social_runs[0].score, "grade": social_runs[0].grade,
                             "change": (social_runs[0].score - social_runs[1].score)
                             if len(social_runs) > 1 and social_runs[0].score is not None and social_runs[1].score is not None else None,
                             "at": social_runs[0].created_at} if social_runs else None),
        "failing_jobs": failing_jobs,
    }


@router.get("/dashboard/insights")
async def insights(
    days: int = Query(30, ge=1, le=365), start: date | None = None, end: date | None = None,
    platforms: str | None = Query(None, description="Comma-separated platform ids"), account_id: uuid.UUID | None = None,
    post_type: str | None = None, pillar_id: uuid.UUID | None = None, ctx: Context = Depends(require("content.view")),
):
    """Everything the dashboard charts show, for one filtered slice, compared with the period just before it."""
    if ctx.org is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an organization first.")
    if start and end and (end - start).days > 366:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a range of a year or less.")
    plats = [p.strip() for p in (platforms or "").split(",") if p.strip()] or None
    return await dashboard_insights.insights(ctx.session, ctx.org.timezone, days=days, start=start, end=end,
                                             platforms=plats, account_id=account_id, post_type=post_type or None,
                                             pillar_id=pillar_id)


@router.get("/system/map")
async def map_(ctx: Context = Depends(require("content.view"))):
    """Agents, services and connected accounts with their live state, for the spatial system view."""
    if ctx.org is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an organization first.")
    return await system_map.build(ctx.session, ctx.org)
