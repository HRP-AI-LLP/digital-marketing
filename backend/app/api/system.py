"""Is the app able to post right now, and is it catching up after downtime? Plus the catch-up settings."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import Organization, Post
from app.services import audit, uptime
from app.services.platforms import PLATFORMS

router = APIRouter(tags=["system"])
STALE_SECONDS = 120


@router.get("/system/status")
async def system_status(ctx: Context = Depends(require("content.view"))):
    now = datetime.now(UTC)
    st = await uptime.get_state()
    last_seen = uptime._parse(st.get("last_seen"))
    worker_running = bool(last_seen and (now - last_seen).total_seconds() < STALE_SECONDS)
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
        "recent_downtime": [{"reason": d.reason, "started_at": d.started_at.isoformat(),
                             "ended_at": d.ended_at.isoformat()} for d in periods[-5:]],
        "catching_up": catching_up,
        "settings": uptime.publishing_settings(ctx.org),
    }


class PublishingIn(BaseModel):
    catch_up_gap_minutes: int | None = Field(None, ge=0, le=240)
    wake_for_posts: bool | None = None


@router.patch("/organization/publishing")
async def update_publishing(body: PublishingIn, ctx: Context = Depends(require("org.settings.manage"))):
    org = await ctx.session.get(Organization, ctx.org_id)
    data = {**(org.publishing or {}), **body.model_dump(exclude_none=True)}
    org.publishing = data
    audit.record(ctx.session, action="org.updated", organization_id=org.id, actor=ctx.user,
                 entity_type="organization", entity_id=org.id, summary="Updated catch-up settings", ip=ctx.ip)
    await ctx.session.commit()
    return uptime.publishing_settings(org)
