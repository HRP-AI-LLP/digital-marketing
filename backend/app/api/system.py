"""Is the app able to post right now, and is it catching up after downtime? Plus the catch-up settings."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import Organization, Post
from app.services import audit, uptime
from app.services import content_actions as actions
from app.services.content import AUTO_APPROVE_DELAY, hold_reasons
from app.services.platforms import PLATFORMS

router = APIRouter(tags=["system"])
STALE_SECONDS = 120


@router.get("/system/status")
async def system_status(ctx: Context = Depends(require("content.view"))):
    now = datetime.now(UTC)
    st = await uptime.get_state()
    last_seen = uptime._parse(st.get("last_seen"))
    worker_running = bool(last_seen and (now - last_seen).total_seconds() < uptime.stale_after())
    periods = await uptime.recent_downtime(now - timedelta(hours=24))
    rows = (await ctx.session.scalars(select(Post).where(
        Post.status.in_(["scheduled", "approved", "publishing"]), Post.scheduled_at.is_not(None),
        Post.scheduled_at <= now).order_by(Post.scheduled_at).limit(50))).all()
    catching_up = []
    for p in rows:
        at = uptime._parse((p.meta or {}).get("catch_up_at"))
        if at is None and p.status != "publishing":
            continue
        spec = PLATFORMS.get(p.platform)
        catching_up.append({
            "id": str(p.id), "platform": p.platform, "platform_label": spec.label if spec else p.platform,
            "title": (p.title or p.text or p.topic or "")[:90], "due_at": p.scheduled_at.isoformat(),
            "posting_at": (at or now).isoformat(), "status": p.status,
        })
    return {
        "worker_running": worker_running,
        "online": bool(st.get("online", True)) if worker_running else None,
        "offline_since": st.get("offline_since") if worker_running else None,
        "last_seen": st.get("last_seen"),
        "clock_skew_seconds": st.get("clock_skew_seconds"),
        "wake_at": st.get("wake_at"),
        "public_base": st.get("public_base"),
        "public_ok": st.get("public_ok") if worker_running else None,
        "recent_downtime": [{"reason": d.reason, "started_at": d.started_at.isoformat(),
                             "ended_at": d.ended_at.isoformat()} for d in periods[-5:]],
        "catching_up": catching_up,
        "settings": uptime.publishing_settings(ctx.org),
    }


class BlogAutopilotIn(BaseModel):
    enabled: bool
    days: list[int] = Field(default_factory=lambda: [0, 3], max_length=7)
    time: str = Field("10:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class PublishingIn(BaseModel):
    catch_up_gap_minutes: int | None = Field(None, ge=0, le=240)
    wake_for_posts: bool | None = None
    auto_approve_posts: bool | None = None
    auto_publish_articles: bool | None = None
    publish_held_posts: bool | None = None  # also send posts the checks would hold (too similar, low reviewer score)
    blog_autopilot: BlogAutopilotIn | None = None
    approve_waiting: bool = False  # with auto_approve_posts: also approve the posts already waiting


async def _waiting(ctx: Context) -> list[Post]:
    return list((await ctx.session.scalars(select(Post).where(Post.status == "in_review")
                                           .order_by(Post.scheduled_at.nulls_last()))).all())


@router.get("/organization/publishing/waiting")
async def waiting_summary(ctx: Context = Depends(require("content.view"))):
    """How many posts wait for approval, and how many of them auto-approval would send (the rest need a person)."""
    rows = await _waiting(ctx)
    ok = [p for p in rows if not hold_reasons(p, ctx.org)]
    return {"waiting": len(rows), "would_approve": len(ok)}


@router.patch("/organization/publishing")
async def update_publishing(body: PublishingIn, ctx: Context = Depends(require("org.settings.manage"))):
    org = await ctx.session.get(Organization, ctx.org_id)
    changes = body.model_dump(exclude_none=True, exclude={"approve_waiting"})
    if "auto_approve_posts" in changes and "content.approve" not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only people who can approve posts can turn on auto-approval.")
    if "auto_publish_articles" in changes and "seo.publish" not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only people who can publish articles can turn this on.")
    if "blog_autopilot" in changes and "seo.manage" not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only people who can write articles can turn on the blog autopilot.")
    if "blog_autopilot" in changes:
        days = sorted({d for d in changes["blog_autopilot"]["days"] if 0 <= d <= 6})
        if changes["blog_autopilot"]["enabled"] and not days:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick at least one day for the blog autopilot.")
        keep = ((org.publishing or {}).get("blog_autopilot") or {}).get("last_run")
        changes["blog_autopilot"] = {**changes["blog_autopilot"], "days": days, "last_run": keep}
    org.publishing = {**(org.publishing or {}), **changes}
    approved = held = 0
    if body.approve_waiting and uptime.publishing_settings(org)["auto_approve_posts"]:
        for p in await _waiting(ctx):
            if hold_reasons(p, org):
                held += 1
                continue
            try:
                when = None if p.scheduled_at else datetime.now(UTC) + AUTO_APPROVE_DELAY
                await actions.approve_by_id(ctx.session, p.id, ctx.user, via="auto-approval", scheduled_at=when)
                approved += 1
            except ValueError:
                held += 1
    summary = ", ".join(f"{k.replace('_', ' ')} {'on' if v is True else 'off' if v is False else v}"
                        for k, v in changes.items() if k != "blog_autopilot")
    if "blog_autopilot" in changes:
        summary = ", ".join(filter(None, [summary, f"blog autopilot {'on' if changes['blog_autopilot']['enabled'] else 'off'}"]))
    audit.record(ctx.session, action="org.updated", organization_id=org.id, actor=ctx.user,
                 entity_type="organization", entity_id=org.id, ip=ctx.ip,
                 summary=f"Publishing settings: {summary or 'no changes'}"
                         + (f"; approved {approved} waiting post(s)" if approved else ""))
    await ctx.session.commit()
    return {**uptime.publishing_settings(org), "approved": approved, "still_waiting": held}
