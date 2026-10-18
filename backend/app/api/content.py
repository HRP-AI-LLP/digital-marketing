"""Content engine API: brand kit, pillars, campaigns, schedules, posts, approvals, calendar."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select

from app.core.deps import Context, require
from app.models import (
    BrandProfile,
    Campaign,
    ContentPillar,
    MediaAsset,
    Post,
    PostActivity,
    PostMetric,
    Schedule,
    SocialAccount,
)
from app.services import audit, content_actions, scheduler
from app.services import content as svc
from app.services.jobs import enqueue
from app.services.platforms import PLATFORMS, POST_TYPES, supported_types

router = APIRouter(prefix="/content", tags=["content"])

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
Mode = Literal["manual", "approve_first", "auto_with_window", "full_auto"]


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


def _valid_date(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    if not DATE_RE.match(v):
        raise ValueError("Use the format YYYY-MM-DD.")
    date.fromisoformat(v)
    return v


def _tz(ctx: Context) -> ZoneInfo:
    return svc.org_tz(ctx.org)


def _aware(dt: datetime | None, tz: ZoneInfo) -> datetime | None:
    if dt is None:
        return None
    return (dt.replace(tzinfo=tz) if dt.tzinfo is None else dt).astimezone(UTC)


def _str_list(v: Any, limit: int = 100, item_len: int = 300) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in re.split(r"[\n,]", v)]
    out, seen = [], set()
    for x in v:
        s = str(x).strip()[:item_len]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:limit]


# ================================================================== meta
@router.get("/meta")
async def meta(ctx: Context = Depends(require("content.view"))):
    return {
        "platforms": [
            {"id": p.id, "label": p.label, "max_chars": p.max_chars, "max_hashtags": p.max_hashtags,
             "title_chars": p.title_chars, "post_types": list(p.post_types), "max_images": p.max_images,
             "image_size": "%dx%d" % p.image_size, "video_size": "%dx%d" % p.video_size,
             "notes": p.notes, "manual": p.id in svc.MANUAL_PLATFORMS}
            for p in PLATFORMS.values()
        ],
        "post_types": POST_TYPES,
        "needs_media": sorted(svc.NEEDS_MEDIA),
        "statuses": svc.STATUSES,
        "modes": svc.MODES,
        "timezone": ctx.org.timezone if ctx.org else "Asia/Kolkata",
    }


@router.get("/accounts")
async def accounts(ctx: Context = Depends(require("content.view"))):
    """Accounts that can hold posts (for filters and pickers; no secrets)."""
    rows = (await ctx.session.scalars(
        select(SocialAccount).where(SocialAccount.status != "removed", SocialAccount.platform.in_(list(PLATFORMS)))
        .order_by(SocialAccount.platform, SocialAccount.name)
    )).all()
    return [_account_brief(a) for a in rows]


# ================================================================== brand
class BrandIn(BaseModel):
    voice: str | None = Field(None, max_length=4000)
    audience: str | None = Field(None, max_length=4000)
    do_list: list[str] = []
    dont_list: list[str] = []
    hashtags: list[str] = []
    ctas: list[str] = []
    banned_words: list[str] = []
    disclaimer: str | None = Field(None, max_length=1000)
    language: str = Field("en", max_length=20)
    emoji_policy: Literal["none", "light", "rich"] = "light"
    blackout_dates: list[str] = []
    colors: dict | None = None
    fonts: dict | None = None

    @field_validator("blackout_dates")
    @classmethod
    def _dates(cls, v: list[str]) -> list[str]:
        out = sorted({_valid_date(str(d).strip()) for d in v if str(d).strip()})
        return [d for d in out if d]


def brand_out(b: BrandProfile) -> dict:
    return {
        "id": b.id, "voice": b.voice, "audience": b.audience, "do_list": b.do_list or [], "dont_list": b.dont_list or [],
        "hashtags": b.hashtags or [], "ctas": b.ctas or [], "banned_words": b.banned_words or [],
        "disclaimer": b.disclaimer, "language": b.language, "emoji_policy": b.emoji_policy,
        "blackout_dates": b.blackout_dates or [], "colors": b.colors or {}, "fonts": b.fonts or {},
        "logo_asset_id": b.logo_asset_id, "logo_url": _logo_url(b),
        "look": {k: (b.settings or {}).get(k) for k in ("display_name", "website", "handle")},
        "updated_at": b.updated_at,
    }


def _logo_url(b: BrandProfile) -> str | None:
    if not b.logo_asset_id:
        return None
    from app.core.signing import media_url

    return media_url(b.logo_asset_id, b.organization_id)


class BrandLookIn(BaseModel):
    colors: dict[str, str] = {}
    logo_asset_id: uuid.UUID | None = None
    display_name: str | None = Field(None, max_length=120)
    website: str | None = Field(None, max_length=200)
    handle: str | None = Field(None, max_length=120)

    @field_validator("colors")
    @classmethod
    def _colors(cls, v: dict[str, str]) -> dict[str, str]:
        out = {}
        for k in ("primary", "secondary", "dark", "light"):
            c = (v.get(k) or "").strip()
            if c:
                if not COLOR_RE.match(c):
                    raise ValueError(f"{k} must be a hex colour like #075492")
                out[k] = c
        return out


@router.put("/brand/look")
async def put_brand_look(body: BrandLookIn, ctx: Context = Depends(require("content.create"))):
    """Colours, logo and the name/website shown on generated images."""
    b = await svc.get_brand(ctx.session, ctx.org_id)
    if body.logo_asset_id:
        from app.models import MediaAsset

        logo = await ctx.session.scalar(select(MediaAsset).where(MediaAsset.id == body.logo_asset_id))
        if logo is None or logo.kind != "image":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an image file for the logo.")
    b.colors = body.colors
    b.logo_asset_id = body.logo_asset_id
    b.settings = {**(b.settings or {}), "display_name": (body.display_name or "").strip() or None,
                  "website": (body.website or "").strip() or None, "handle": (body.handle or "").strip() or None}
    audit.record(ctx.session, action="brand.look", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="brand", entity_id=b.id, summary="Updated brand colours and logo", ip=ctx.ip)
    await ctx.session.commit()
    return brand_out(b)


@router.get("/brand")
async def get_brand(ctx: Context = Depends(require("content.view"))):
    b = await svc.get_brand(ctx.session, ctx.org_id)
    await ctx.session.commit()
    return brand_out(b)


@router.put("/brand")
async def put_brand(body: BrandIn, ctx: Context = Depends(require("content.create"))):
    b = await svc.get_brand(ctx.session, ctx.org_id)
    b.voice, b.audience = (body.voice or "").strip() or None, (body.audience or "").strip() or None
    b.do_list, b.dont_list = _str_list(body.do_list), _str_list(body.dont_list)
    b.hashtags = [t for t in (svc._clean_tag(h) for h in _str_list(body.hashtags, 30, 60)) if t]
    b.ctas, b.banned_words = _str_list(body.ctas, 20), _str_list(body.banned_words, 200, 100)
    b.disclaimer = (body.disclaimer or "").strip() or None
    b.language, b.emoji_policy, b.blackout_dates = body.language.strip() or "en", body.emoji_policy, body.blackout_dates
    if body.colors is not None:
        b.colors = body.colors
    if body.fonts is not None:
        b.fonts = body.fonts
    audit.record(ctx.session, action="brand.updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="brand", entity_id=b.id, summary="Updated the brand kit", ip=ctx.ip)
    await ctx.session.commit()
    return brand_out(b)


# ================================================================== pillars
class PillarIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    weight: int = Field(10, ge=0, le=100)
    color: str = "#0f8dde"
    active: bool = True

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        if not COLOR_RE.match(v):
            raise ValueError("Use a hex colour like #0f8dde.")
        return v


class PillarPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    weight: int | None = Field(None, ge=0, le=100)
    color: str | None = None
    active: bool | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is not None and not COLOR_RE.match(v):
            raise ValueError("Use a hex colour like #0f8dde.")
        return v


def pillar_out(p: ContentPillar) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "weight": p.weight, "color": p.color,
            "active": p.active}


async def _pillar(ctx: Context, pid: uuid.UUID) -> ContentPillar:
    p = await ctx.session.get(ContentPillar, pid)
    if p is None:
        raise _bad("Topic pillar not found.", 404)
    return p


@router.get("/pillars")
async def list_pillars(ctx: Context = Depends(require("content.view"))):
    rows = (await ctx.session.scalars(select(ContentPillar).order_by(ContentPillar.weight.desc(), ContentPillar.name))).all()
    return [pillar_out(p) for p in rows]


@router.post("/pillars", status_code=201)
async def add_pillar(body: PillarIn, ctx: Context = Depends(require("content.create"))):
    p = ContentPillar(organization_id=ctx.org_id, **body.model_dump())
    ctx.session.add(p)
    await ctx.session.flush()
    audit.record(ctx.session, action="pillar.added", organization_id=ctx.org_id, actor=ctx.user, entity_type="pillar",
                 entity_id=p.id, summary=f"Added topic pillar {p.name}", ip=ctx.ip)
    await ctx.session.commit()
    return pillar_out(p)


@router.post("/pillars/seed-defaults")
async def seed_pillars(ctx: Context = Depends(require("content.create"))):
    have = {n.lower() for n in (await ctx.session.scalars(select(ContentPillar.name))).all()}
    added = []
    for d in svc.DEFAULT_PILLARS:
        if d["name"].lower() not in have:
            p = ContentPillar(organization_id=ctx.org_id, active=True, **d)
            ctx.session.add(p)
            added.append(p)
    if added:
        audit.record(ctx.session, action="pillar.seeded", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="pillar", summary=f"Added {len(added)} suggested topic pillars", ip=ctx.ip)
    await ctx.session.commit()
    return await list_pillars(ctx)


@router.patch("/pillars/{pid}")
async def update_pillar(pid: uuid.UUID, body: PillarPatch, ctx: Context = Depends(require("content.create"))):
    p = await _pillar(ctx, pid)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None or k == "description":
            setattr(p, k, v)
    await ctx.session.commit()
    return pillar_out(p)


@router.delete("/pillars/{pid}", status_code=204)
async def delete_pillar(pid: uuid.UUID, ctx: Context = Depends(require("content.create"))):
    p = await _pillar(ctx, pid)
    await ctx.session.delete(p)
    audit.record(ctx.session, action="pillar.deleted", organization_id=ctx.org_id, actor=ctx.user, entity_type="pillar",
                 entity_id=pid, summary=f"Deleted topic pillar {p.name}", ip=ctx.ip)
    await ctx.session.commit()


# ================================================================== campaigns
class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brief: str | None = Field(None, max_length=4000)
    starts_on: str | None = None
    ends_on: str | None = None
    boost: int = Field(50, ge=0, le=100)
    active: bool = True

    @field_validator("starts_on", "ends_on")
    @classmethod
    def _d(cls, v: str | None) -> str | None:
        return _valid_date(v)


class CampaignPatch(CampaignIn):
    name: str | None = Field(None, min_length=1, max_length=200)  # type: ignore[assignment]
    boost: int | None = Field(None, ge=0, le=100)  # type: ignore[assignment]
    active: bool | None = None  # type: ignore[assignment]


def campaign_out(c: Campaign) -> dict:
    return {"id": c.id, "name": c.name, "brief": c.brief, "starts_on": c.starts_on, "ends_on": c.ends_on,
            "boost": c.boost, "active": c.active}


def _check_range(starts: str | None, ends: str | None) -> None:
    if starts and ends and ends < starts:
        raise _bad("The end date must be on or after the start date.")


@router.get("/campaigns")
async def list_campaigns(ctx: Context = Depends(require("content.view"))):
    rows = (await ctx.session.scalars(select(Campaign).order_by(Campaign.starts_on.desc().nullslast(), Campaign.name))).all()
    return [campaign_out(c) for c in rows]


@router.post("/campaigns", status_code=201)
async def add_campaign(body: CampaignIn, ctx: Context = Depends(require("content.create"))):
    _check_range(body.starts_on, body.ends_on)
    c = Campaign(organization_id=ctx.org_id, **body.model_dump())
    ctx.session.add(c)
    await ctx.session.flush()
    audit.record(ctx.session, action="campaign.added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="campaign", entity_id=c.id, summary=f"Added campaign {c.name}", ip=ctx.ip)
    await ctx.session.commit()
    return campaign_out(c)


@router.patch("/campaigns/{cid}")
async def update_campaign(cid: uuid.UUID, body: CampaignPatch, ctx: Context = Depends(require("content.create"))):
    c = await ctx.session.get(Campaign, cid)
    if c is None:
        raise _bad("Campaign not found.", 404)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if v is None and k in ("name", "boost", "active"):
            continue
        setattr(c, k, v)
    _check_range(c.starts_on, c.ends_on)
    await ctx.session.commit()
    return campaign_out(c)


@router.delete("/campaigns/{cid}", status_code=204)
async def delete_campaign(cid: uuid.UUID, ctx: Context = Depends(require("content.create"))):
    c = await ctx.session.get(Campaign, cid)
    if c is None:
        raise _bad("Campaign not found.", 404)
    await ctx.session.delete(c)
    audit.record(ctx.session, action="campaign.deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="campaign", entity_id=cid, summary=f"Deleted campaign {c.name}", ip=ctx.ip)
    await ctx.session.commit()


# ================================================================== schedules
def _check_days(v: list[int]) -> list[int]:
    if not v:
        raise ValueError("Pick at least one day.")
    if any(d < 0 or d > 6 for d in v):
        raise ValueError("Days go from 0 (Monday) to 6 (Sunday).")
    return sorted(set(v))


def _check_times(v: list[str]) -> list[str]:
    v = [t.strip() for t in v if t and t.strip()]
    if not v:
        raise ValueError("Add at least one posting time.")
    bad = [t for t in v if not TIME_RE.match(t)]
    if bad:
        raise ValueError(f"Use 24-hour HH:MM times (for example 09:30), not {bad[0]}.")
    if len(v) > 24:
        raise ValueError("Use at most 24 times a day.")
    return sorted(set(v))


def _check_link(v: str | None) -> str | None:
    v = (v or "").strip() or None
    if v and not re.match(r"^https?://\S+$", v):
        raise ValueError("Enter a full web address starting with https://")
    return v


class ScheduleIn(BaseModel):
    social_account_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    active: bool = True
    days_of_week: list[int] = [0, 1, 2, 3, 4]
    times: list[str] = ["10:00"]
    post_types: dict[str, float] = {"text": 1}
    pillar_ids: list[uuid.UUID] = []
    mode: Mode = "approve_first"
    approval_window_hours: int = Field(4, ge=1, le=168)
    lead_hours: int = Field(24, ge=1, le=168)
    catch_up_grace_hours: int = Field(3, ge=0, le=48)
    include_link: bool = False
    link_url: str | None = Field(None, max_length=2000)
    instructions: str | None = Field(None, max_length=4000)
    interval_days: int = Field(1, ge=1, le=14)
    interval_start: str | None = None

    @field_validator("interval_start")
    @classmethod
    def _istart(cls, v: str | None) -> str | None:
        return _valid_date(v)

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        return _check_days(v)

    @field_validator("times")
    @classmethod
    def _times(cls, v: list[str]) -> list[str]:
        return _check_times(v)

    @field_validator("link_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        return _check_link(v)


class SchedulePatch(BaseModel):
    social_account_id: uuid.UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=160)
    active: bool | None = None
    days_of_week: list[int] | None = None
    times: list[str] | None = None
    post_types: dict[str, float] | None = None
    pillar_ids: list[uuid.UUID] | None = None
    mode: Mode | None = None
    approval_window_hours: int | None = Field(None, ge=1, le=168)
    lead_hours: int | None = Field(None, ge=1, le=168)
    catch_up_grace_hours: int | None = Field(None, ge=0, le=48)
    include_link: bool | None = None
    link_url: str | None = Field(None, max_length=2000)
    instructions: str | None = Field(None, max_length=4000)
    interval_days: int | None = Field(None, ge=1, le=14)
    interval_start: str | None = None

    @field_validator("interval_start")
    @classmethod
    def _istart(cls, v: str | None) -> str | None:
        return _valid_date(v)

    @field_validator("days_of_week")
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        return None if v is None else _check_days(v)

    @field_validator("times")
    @classmethod
    def _times(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _check_times(v)

    @field_validator("link_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        return _check_link(v)


def schedule_out(s: Schedule, account: SocialAccount | None) -> dict:
    return {
        "id": s.id, "social_account_id": s.social_account_id, "name": s.name, "active": s.active,
        "days_of_week": s.days_of_week or [], "times": s.times or [], "post_types": s.post_types or {},
        "pillar_ids": [str(p) for p in (s.pillar_ids or [])], "mode": s.mode,
        "approval_window_hours": s.approval_window_hours, "lead_hours": s.lead_hours,
        "catch_up_grace_hours": s.catch_up_grace_hours, "include_link": s.include_link, "link_url": s.link_url,
        "instructions": s.instructions, "interval_days": s.interval_days or 1, "interval_start": s.interval_start,
        "created_at": s.created_at, "updated_at": s.updated_at,
        "account": _account_brief(account),
        "ready": scheduler.account_ready(account),
    }


def _account_brief(a: SocialAccount | None) -> dict | None:
    if a is None:
        return None
    return {"id": a.id, "platform": a.platform, "name": a.name, "handle": a.handle, "status": a.status,
            "avatar_url": a.avatar_url, "platform_label": svc.platform_of(a.platform).label}


async def _account(ctx: Context, aid: uuid.UUID) -> SocialAccount:
    a = await ctx.session.get(SocialAccount, aid)
    if a is None:
        raise _bad("That social account doesn't exist. Connect it in Social accounts first.", 404)
    return a


def _check_mode_permission(ctx: Context, mode: str | None) -> None:
    """Automatic modes publish without a reviewer, so only people who can approve and publish may enable them."""
    if mode in ("full_auto", "auto_with_window") and not {"content.approve", "content.publish"} <= ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only people who can approve and publish posts can turn on automatic publishing.")


async def _validate_schedule(ctx: Context, s: Schedule, account: SocialAccount) -> None:
    supported = supported_types(account.platform)
    types = {t: float(w) for t, w in (s.post_types or {}).items() if float(w) > 0}
    if not types:
        raise _bad("Give at least one post type a weight above zero.")
    unknown = [t for t in types if t not in POST_TYPES]
    if unknown:
        raise _bad(f"Unknown post type: {unknown[0]}.")
    unsupported = [t for t in types if t not in supported]
    if unsupported:
        label = svc.platform_of(account.platform).label
        raise _bad(f"{label} doesn't support {POST_TYPES[unsupported[0]].lower()} posts. "
                   f"Choose from: {', '.join(POST_TYPES[t].lower() for t in supported)}.")
    s.post_types = types
    if s.include_link and not s.link_url:
        raise _bad("Add the link to include, or turn off “Include a link”.")
    if s.pillar_ids:
        ids = [svc.as_uuid(p) for p in s.pillar_ids]
        found = set((await ctx.session.scalars(select(ContentPillar.id).where(ContentPillar.id.in_([i for i in ids if i])))).all())
        missing = [i for i in ids if i not in found]
        if missing:
            raise _bad("One of the chosen topic pillars no longer exists. Refresh the page and pick again.")
        s.pillar_ids = [str(i) for i in ids]


@router.get("/schedules")
async def list_schedules(ctx: Context = Depends(require("schedule.view"))):
    rows = (await ctx.session.execute(
        select(Schedule, SocialAccount).outerjoin(SocialAccount, SocialAccount.id == Schedule.social_account_id)
        .order_by(Schedule.created_at)
    )).all()
    return [schedule_out(s, a) for s, a in rows]


@router.post("/schedules", status_code=201)
async def add_schedule(body: ScheduleIn, ctx: Context = Depends(require("schedule.manage"))):
    _check_mode_permission(ctx, body.mode)
    account = await _account(ctx, body.social_account_id)
    data = body.model_dump()
    data["pillar_ids"] = [str(p) for p in data["pillar_ids"]]
    s = Schedule(organization_id=ctx.org_id, **data)
    await _validate_schedule(ctx, s, account)
    ctx.session.add(s)
    await ctx.session.flush()
    audit.record(ctx.session, action="schedule.added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="schedule", entity_id=s.id, summary=f"Added schedule {s.name} for {account.name}", ip=ctx.ip)
    await ctx.session.commit()
    return schedule_out(s, account)


async def _schedule(ctx: Context, sid: uuid.UUID) -> Schedule:
    s = await ctx.session.get(Schedule, sid)
    if s is None:
        raise _bad("Schedule not found.", 404)
    return s


@router.get("/schedules/{sid}")
async def get_schedule(sid: uuid.UUID, ctx: Context = Depends(require("schedule.view"))):
    s = await _schedule(ctx, sid)
    return schedule_out(s, await ctx.session.get(SocialAccount, s.social_account_id))


@router.patch("/schedules/{sid}")
async def update_schedule(sid: uuid.UUID, body: SchedulePatch, ctx: Context = Depends(require("schedule.manage"))):
    s = await _schedule(ctx, sid)
    data = body.model_dump(exclude_unset=True)
    if "mode" in data:
        _check_mode_permission(ctx, data["mode"])
    for k, v in data.items():
        if v is None and k not in ("link_url", "instructions", "interval_start"):
            continue
        if k == "pillar_ids":
            v = [str(p) for p in v]
        setattr(s, k, v)
    account = await _account(ctx, s.social_account_id)
    await _validate_schedule(ctx, s, account)
    audit.record(ctx.session, action="schedule.updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="schedule", entity_id=s.id, summary=f"Updated schedule {s.name}",
                 details={"fields": sorted(data)}, ip=ctx.ip)
    await ctx.session.commit()
    return schedule_out(s, account)


@router.delete("/schedules/{sid}", status_code=204)
async def delete_schedule(sid: uuid.UUID, ctx: Context = Depends(require("schedule.manage"))):
    s = await _schedule(ctx, sid)
    # Unwritten future slots go with the schedule; finished posts stay.
    planned = (await ctx.session.scalars(select(Post).where(Post.schedule_id == sid, Post.status.in_(["planned", "skipped"])))).all()
    for p in planned:
        await ctx.session.delete(p)
    await ctx.session.delete(s)
    audit.record(ctx.session, action="schedule.deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="schedule", entity_id=sid, summary=f"Deleted schedule {s.name}", ip=ctx.ip)
    await ctx.session.commit()


@router.get("/schedules/{sid}/preview")
async def preview_schedule(sid: uuid.UUID, days: int = Query(7, ge=1, le=60),
                           ctx: Context = Depends(require("schedule.view"))):
    s = await _schedule(ctx, sid)
    tz = _tz(ctx)
    now = svc.now_utc()
    end = now + timedelta(days=days)
    blackout = await scheduler.blackout_dates(ctx.session)
    all_slots = scheduler.slot_times(s, tz, now, end)
    posts = {p.scheduled_at: p for p in (await ctx.session.scalars(
        select(Post).where(Post.schedule_id == sid, Post.scheduled_at.in_(all_slots or [now]))
    )).all()}
    lead_end = now + timedelta(hours=s.lead_hours or 24)
    out = []
    for t in all_slots:
        local = t.astimezone(tz)
        p = posts.get(t)
        out.append({
            "at": t, "local": local.strftime("%a %d %b, %H:%M"), "date": local.date().isoformat(),
            "blackout": local.date().isoformat() in blackout, "within_lead": t <= lead_end,
            "post_id": p.id if p else None, "status": p.status if p else None,
        })
    return {"timezone": str(tz), "slots": out}


@router.post("/schedules/{sid}/run-now")
async def run_schedule_now(sid: uuid.UUID, ctx: Context = Depends(require("schedule.manage", "content.create"))):
    s = await _schedule(ctx, sid)
    account = await _account(ctx, s.social_account_id)
    if not scheduler.account_ready(account):
        raise _bad(f"{account.name} isn't connected. Reconnect it in Social accounts first.")
    at = await scheduler.next_free_slot(ctx.session, s, _tz(ctx), svc.now_utc())
    if at is None:
        raise _bad("This schedule has no free posting slot in the next 60 days. Add days or times, or check blackout dates.")
    post = scheduler.new_slot_post(s, account, at)
    post.created_by = ctx.user.id
    ctx.session.add(post)
    await ctx.session.flush()
    svc.add_activity(ctx.session, post, "planned", "Created with Run now", user=ctx.user)
    job = await enqueue(ctx.session, "post.generate", ctx.org_id, {"post_id": str(post.id)}, dedupe_key=f"gen:{post.id}",
                        priority=1, created_by=ctx.user.id)
    await ctx.session.commit()
    return {"post_id": post.id, "job_id": job.id, "scheduled_at": at}


# ================================================================== posts: output helpers
async def _media_map(ctx: Context, posts: list[Post]) -> dict[str, dict]:
    ids = {svc.as_uuid(m) for p in posts for m in (p.media_ids or [])}
    ids.discard(None)
    if not ids:
        return {}
    assets = (await ctx.session.scalars(select(MediaAsset).where(MediaAsset.id.in_(ids)))).all()
    try:
        from app.services.media import asset_out
    except ImportError:
        asset_out = None
    out = {}
    for a in assets:
        try:
            d = asset_out(a) if asset_out else None
        except Exception:  # noqa: BLE001
            d = None
        out[str(a.id)] = d or {"id": str(a.id), "kind": a.kind, "mime": a.mime, "width": a.width, "height": a.height,
                               "url": None, "alt_text": a.alt_text}
    return out


async def _lookups(ctx: Context, posts: list[Post]) -> tuple[dict, dict]:
    aids = {p.social_account_id for p in posts if p.social_account_id}
    pids = {p.pillar_id for p in posts if p.pillar_id}
    accounts = {a.id: a for a in (await ctx.session.scalars(select(SocialAccount).where(SocialAccount.id.in_(aids)))).all()} if aids else {}
    pillars = {p.id: p for p in (await ctx.session.scalars(select(ContentPillar).where(ContentPillar.id.in_(pids)))).all()} if pids else {}
    return accounts, pillars


def post_out(p: Post, accounts: dict, pillars: dict, media: dict, *, compact: bool = False) -> dict:
    a = accounts.get(p.social_account_id)
    pl = pillars.get(p.pillar_id)
    plat = svc.platform_of(p.platform)
    caption = svc.full_caption(p.text, p.hashtags)
    d: dict[str, Any] = {
        "id": p.id, "platform": p.platform, "platform_label": plat.label, "post_type": p.post_type,
        "status": p.status, "mode": p.mode, "topic": p.topic, "title": p.title,
        "scheduled_at": p.scheduled_at, "approval_deadline": p.approval_deadline, "published_at": p.published_at,
        "account": _account_brief(a), "social_account_id": p.social_account_id, "schedule_id": p.schedule_id,
        "pillar": {"id": pl.id, "name": pl.name, "color": pl.color} if pl else None,
        "external_url": p.external_url, "error": p.error,
        "qa_score": (p.qa or {}).get("score"), "qa_passed": (p.qa or {}).get("passed"),
        "similarity": p.similarity, "char_count": len(caption), "max_chars": plat.max_chars,
        "media": [media[m] for m in (p.media_ids or []) if m in media],
        "warnings": (p.meta or {}).get("warnings") or [],
    }
    if compact:
        d["text"] = (p.text or "")[:280]
        d["hashtags"] = p.hashtags or []
        return d
    d.update({
        "text": p.text, "hashtags": p.hashtags or [], "link_url": p.link_url, "first_comment": p.first_comment,
        "media_ids": p.media_ids or [], "qa": p.qa or {}, "campaign_id": p.campaign_id,
        "rejection_reason": p.rejection_reason, "approved_at": p.approved_at, "approved_by": p.approved_by,
        "external_id": p.external_id, "attempts": p.attempts, "created_at": p.created_at, "updated_at": p.updated_at,
        "caption": caption, "max_hashtags": plat.max_hashtags, "title_chars": plat.title_chars,
        "brief": {k: (p.meta or {}).get(k) for k in ("image_brief", "alt_text", "slides", "video_script", "angle", "brief")},
    })
    return d


async def _post(ctx: Context, pid: uuid.UUID) -> Post:
    p = await ctx.session.get(Post, pid)
    if p is None:
        raise _bad("Post not found.", 404)
    return p


async def _out_one(ctx: Context, p: Post) -> dict:
    accounts, pillars = await _lookups(ctx, [p])
    media = await _media_map(ctx, [p])
    d = post_out(p, accounts, pillars, media)
    acts = (await ctx.session.scalars(select(PostActivity).where(PostActivity.post_id == p.id)
                                      .order_by(PostActivity.created_at.desc()).limit(100))).all()
    d["activities"] = [{"id": a.id, "action": a.action, "note": a.note, "actor": a.actor, "created_at": a.created_at}
                       for a in acts]
    mets = (await ctx.session.scalars(select(PostMetric).where(PostMetric.post_id == p.id)
                                      .order_by(PostMetric.fetched_at.desc()).limit(50))).all()
    keys = ("impressions", "reach", "likes", "comments", "shares", "clicks", "saves", "video_views")
    d["metrics"] = [{"fetched_at": m.fetched_at, **{k: getattr(m, k) for k in keys}} for m in mets]
    return d


# ================================================================== posts: list & detail
def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


@router.get("/posts")
async def list_posts(
    status_: str | None = Query(None, alias="status"), platform: str | None = None,
    account_id: uuid.UUID | None = None, pillar_id: uuid.UUID | None = None,
    start: datetime | None = None, end: datetime | None = None, q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ctx: Context = Depends(require("content.view")),
):
    tz = _tz(ctx)
    conds = []
    statuses = _csv(status_)
    conds.append(Post.status.in_(statuses) if statuses else Post.status != "skipped")
    if platform:
        conds.append(Post.platform.in_(_csv(platform)))
    if account_id:
        conds.append(Post.social_account_id == account_id)
    if pillar_id:
        conds.append(Post.pillar_id == pillar_id)
    when = func.coalesce(Post.published_at, Post.scheduled_at, Post.created_at)
    if start:
        conds.append(when >= _aware(start, tz))
    if end:
        conds.append(when < _aware(end, tz))
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(Post.text.ilike(like), Post.topic.ilike(like), Post.title.ilike(like)))
    total = await ctx.session.scalar(select(func.count()).select_from(Post).where(*conds))
    rows = list((await ctx.session.scalars(
        select(Post).where(*conds).order_by(when.desc(), Post.created_at.desc()).limit(limit).offset(offset)
    )).all())
    accounts, pillars = await _lookups(ctx, rows)
    media = await _media_map(ctx, rows)
    return {"items": [post_out(p, accounts, pillars, media, compact=True) for p in rows], "total": total or 0}


@router.get("/posts/{pid}")
async def get_post(pid: uuid.UUID, ctx: Context = Depends(require("content.view"))):
    return await _out_one(ctx, await _post(ctx, pid))


# ================================================================== posts: create / compose / edit
class PostCreate(BaseModel):
    account_id: uuid.UUID
    post_type: str = "text"
    text: str = Field("", max_length=20000)
    title: str | None = Field(None, max_length=300)
    hashtags: list[str] = []
    link_url: str | None = Field(None, max_length=2000)
    first_comment: str | None = Field(None, max_length=4000)
    media_ids: list[uuid.UUID] = []
    pillar_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    status: Literal["draft", "in_review", "scheduled"] = "draft"

    @field_validator("link_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        return _check_link(v)


class ComposeIn(BaseModel):
    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    brief: str = Field(min_length=3, max_length=4000)
    post_type: str | None = None
    pillar_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    then: Literal["draft", "review"] = "draft"
    link_url: str | None = Field(None, max_length=2000)

    @field_validator("link_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        return _check_link(v)


class PostPatch(BaseModel):
    text: str | None = Field(None, max_length=20000)
    title: str | None = Field(None, max_length=300)
    hashtags: list[str] | None = None
    link_url: str | None = Field(None, max_length=2000)
    first_comment: str | None = Field(None, max_length=4000)
    scheduled_at: datetime | None = None
    media_ids: list[uuid.UUID] | None = None
    pillar_id: uuid.UUID | None = None
    post_type: str | None = None

    @field_validator("link_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        return _check_link(v)


async def _check_media(ctx: Context, ids: list[uuid.UUID]) -> list[str]:
    if not ids:
        return []
    found = set((await ctx.session.scalars(select(MediaAsset.id).where(MediaAsset.id.in_(ids)))).all())
    if len(found) != len(set(ids)):
        raise _bad("One of the media files doesn't exist. Upload it again.")
    return [str(i) for i in ids]


def _check_type(platform: str, post_type: str) -> None:
    if post_type not in POST_TYPES:
        raise _bad(f"Unknown post type: {post_type}.")
    if post_type not in supported_types(platform):
        p = svc.platform_of(platform)
        raise _bad(f"{p.label} doesn't support {POST_TYPES[post_type].lower()} posts.")


def _clean_tags(tags: list[str]) -> list[str]:
    out, seen = [], set()
    for t in tags:
        c = svc._clean_tag(t)
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out[:30]


@router.post("/posts", status_code=201)
async def create_post(body: PostCreate, ctx: Context = Depends(require("content.create"))):
    account = await _account(ctx, body.account_id)
    _check_type(account.platform, body.post_type)
    if not body.text.strip() and not body.media_ids:
        raise _bad("Write some text or add media first.")
    now = svc.now_utc()
    at = _aware(body.scheduled_at, _tz(ctx))
    if body.status == "scheduled":
        if "content.approve" not in ctx.permissions:
            raise _bad("You need the 'content.approve' permission to schedule without review. Send it for approval instead.", 403)
        if at is None or at <= now:
            raise _bad("Pick a future date and time to schedule this post.")
    if body.status == "in_review" and at is not None and at <= now:
        raise _bad("Pick a future date and time, or save it as a draft.")
    if body.pillar_id:
        await _pillar(ctx, body.pillar_id)
    p = Post(
        organization_id=ctx.org_id, social_account_id=account.id, platform=account.platform, post_type=body.post_type,
        text=body.text.strip(), title=(body.title or "").strip() or None, hashtags=_clean_tags(body.hashtags),
        link_url=(body.link_url or "").strip() or None, first_comment=(body.first_comment or "").strip() or None,
        media_ids=await _check_media(ctx, body.media_ids), pillar_id=body.pillar_id, scheduled_at=at,
        status=body.status, mode="manual" if body.status == "draft" else "approve_first", created_by=ctx.user.id,
        meta={"source": "manual"}, qa={},
    )
    if body.status == "scheduled":
        p.approved_by, p.approved_at = ctx.user.id, now
    if body.status == "in_review":
        p.approval_deadline = at
    ctx.session.add(p)
    await ctx.session.flush()
    await svc.recheck_post(ctx.session, p)
    svc.add_activity(ctx.session, p, "created", f"Written by hand ({svc.STATUSES[p.status].lower()})", user=ctx.user)
    audit.record(ctx.session, action="post.created", organization_id=ctx.org_id, actor=ctx.user, entity_type="post",
                 entity_id=p.id, summary=f"Created a {account.name} post", ip=ctx.ip)
    await ctx.session.commit()
    if p.status == "in_review":
        await svc.notify_review(p, _tz(ctx))
    return await _out_one(ctx, p)


@router.post("/posts/compose")
async def compose(body: ComposeIn, ctx: Context = Depends(require("content.create"))):
    tz = _tz(ctx)
    at = _aware(body.scheduled_at, tz)
    if at is not None and at <= svc.now_utc():
        raise _bad("Pick a future date and time, or leave it empty.")
    if body.pillar_id:
        await _pillar(ctx, body.pillar_id)
    link = (body.link_url or "").strip() or None
    out = []
    for aid in dict.fromkeys(body.account_ids):
        account = await _account(ctx, aid)
        supported = supported_types(account.platform)
        post_type = body.post_type or ("text" if "text" in supported else supported[0])
        if post_type not in supported:
            label = svc.platform_of(account.platform).label
            raise _bad(f"{label} ({account.name}) doesn't support {POST_TYPES.get(post_type, post_type).lower()} posts. "
                       "Pick another post type or remove that account.")
        p = Post(
            organization_id=ctx.org_id, social_account_id=account.id, platform=account.platform, post_type=post_type,
            topic=body.brief.strip()[:300], pillar_id=body.pillar_id, scheduled_at=at, link_url=link,
            status="planned", mode="manual" if body.then == "draft" else "approve_first", created_by=ctx.user.id,
            hashtags=[], media_ids=[], qa={}, meta={"source": "compose", "brief": body.brief.strip(), "post_type_fixed": True},
        )
        ctx.session.add(p)
        await ctx.session.flush()
        svc.add_activity(ctx.session, p, "planned", "Composed with AI", user=ctx.user)
        job = await enqueue(ctx.session, "post.generate", ctx.org_id, {"post_id": str(p.id)}, dedupe_key=f"gen:{p.id}",
                            priority=1, created_by=ctx.user.id)
        out.append({"post_id": p.id, "job_id": job.id, "account_id": account.id})
    await ctx.session.commit()
    return {"posts": out}


@router.patch("/posts/{pid}")
async def update_post(pid: uuid.UUID, body: PostPatch, ctx: Context = Depends(require("content.create"))):
    p = await _post(ctx, pid)
    if p.status in ("published", "publishing"):
        raise _bad("Published posts can't be edited here. Edit it on the platform instead.", 409)
    if p.status in ("planned", "generating"):
        raise _bad("This post is still being written. Wait until it's ready, then edit it.", 409)
    data = body.model_dump(exclude_unset=True)
    tz = _tz(ctx)
    now = svc.now_utc()
    changed: list[str] = []
    if data.get("post_type"):
        _check_type(p.platform, data["post_type"])
        p.post_type = data["post_type"]
        changed.append("post type")
    for k in ("text", "title", "link_url", "first_comment"):
        if k in data:
            v = (data[k] or "").strip()
            setattr(p, k, v if k == "text" else (v or None))
            changed.append(k.replace("_", " "))
    if "hashtags" in data and data["hashtags"] is not None:
        p.hashtags = _clean_tags(data["hashtags"])
        changed.append("hashtags")
    if "media_ids" in data and data["media_ids"] is not None:
        p.media_ids = await _check_media(ctx, data["media_ids"])
        set_keep = bool(p.media_ids)
        svc.set_meta(p, keep_media=set_keep)
        changed.append("media")
    if "pillar_id" in data:
        if data["pillar_id"]:
            await _pillar(ctx, data["pillar_id"])
        p.pillar_id = data["pillar_id"]
        changed.append("pillar")
    if "scheduled_at" in data:
        at = _aware(data["scheduled_at"], tz)
        if at is not None and at <= now and p.status not in ("draft", "rejected", "failed", "manual_pending"):
            raise _bad("Pick a time in the future.")
        p.scheduled_at = at
        changed.append("time")
        if p.status == "missed" and at is not None:
            p.status, p.mode, p.error = "in_review", "approve_first", None
        if p.status == "in_review":
            if p.mode == "auto_with_window" and at is not None:
                window = 4
                if p.schedule_id:
                    s = await ctx.session.get(Schedule, p.schedule_id)
                    window = s.approval_window_hours if s else 4
                p.approval_deadline = max(min(now + timedelta(hours=window), at - timedelta(minutes=5)), now)
            else:
                p.approval_deadline = at
        if p.status in ("scheduled", "approved") and at is None:
            p.status = "in_review"
    if not changed:
        return await _out_one(ctx, p)
    content_changed = any(c in changed for c in ("text", "title", "hashtags", "media", "link url", "post type"))
    if content_changed and p.status in ("scheduled", "approved") and "content.approve" not in ctx.permissions:
        p.status, p.approved_by, p.approved_at = "in_review", None, None
        p.approval_deadline = p.scheduled_at
        svc.add_warning(p, "Edited after approval, so it needs approving again.")
    if content_changed:
        await svc.recheck_post(ctx.session, p)
    svc.add_activity(ctx.session, p, "edited", "Changed " + ", ".join(changed), user=ctx.user)
    audit.record(ctx.session, action="post.edited", organization_id=ctx.org_id, actor=ctx.user, entity_type="post",
                 entity_id=p.id, summary="Edited a post: " + ", ".join(changed), ip=ctx.ip)
    await ctx.session.commit()
    return await _out_one(ctx, p)


# ================================================================== posts: actions
class ApproveIn(BaseModel):
    scheduled_at: datetime | None = None


class RejectIn(BaseModel):
    reason: str | None = Field(None, max_length=1000)


class RegenerateIn(BaseModel):
    note: str | None = Field(None, max_length=500)


class MarkPublishedIn(BaseModel):
    external_url: str | None = Field(None, max_length=2000)


async def _run_action(ctx: Context, fn, *args, **kw) -> str:
    try:
        msg = await fn(*args, **kw)
    except ValueError as e:
        await ctx.session.rollback()
        raise _bad(str(e), 409) from None
    await ctx.session.commit()
    return msg


@router.post("/posts/{pid}/approve")
async def approve(pid: uuid.UUID, body: ApproveIn | None = None, ctx: Context = Depends(require("content.approve"))):
    p = await _post(ctx, pid)
    at = _aware(body.scheduled_at, _tz(ctx)) if body and body.scheduled_at else None
    msg = await _run_action(ctx, content_actions.approve_by_id, ctx.session, p.id, ctx.user, "app", scheduled_at=at)
    return {"message": msg, "post": await _out_one(ctx, await _post(ctx, pid))}


@router.post("/posts/{pid}/reject")
async def reject(pid: uuid.UUID, body: RejectIn | None = None, ctx: Context = Depends(require("content.approve"))):
    p = await _post(ctx, pid)
    msg = await _run_action(ctx, content_actions.reject_by_id, ctx.session, p.id, ctx.user, body.reason if body else None, "app")
    return {"message": msg, "post": await _out_one(ctx, await _post(ctx, pid))}


@router.post("/posts/{pid}/regenerate")
async def regenerate(pid: uuid.UUID, body: RegenerateIn | None = None, ctx: Context = Depends(require("content.create"))):
    p = await _post(ctx, pid)
    msg = await _run_action(ctx, content_actions.regenerate_by_id, ctx.session, p.id, ctx.user, "app",
                            note=body.note if body else None)
    from app.models import Job

    job = await ctx.session.scalar(select(Job).where(Job.dedupe_key == f"gen:{pid}", Job.status.in_(["queued", "running"])))
    return {"message": msg, "job_id": job.id if job else None}


@router.post("/posts/{pid}/publish-now")
async def publish_now(pid: uuid.UUID, ctx: Context = Depends(require("content.publish"))):
    p = await _post(ctx, pid)
    job = await _publish_now(ctx, p)
    await ctx.session.commit()
    return {"message": "Publishing now.", "job_id": job.id}


async def _publish_now(ctx: Context, p: Post):
    """Queue a post for immediate publishing (caller commits). Raises HTTPException when it can't be."""
    if p.status in ("published", "publishing"):
        raise _bad("This post is already published or being published.", 409)
    if p.status in ("planned", "generating"):
        raise _bad("This post is still being written. Try again in a minute.", 409)
    if not (p.text or "").strip() and not p.media_ids:
        raise _bad("This post has no content. Write the text first.", 409)
    now = svc.now_utc()
    if p.status not in ("scheduled", "approved", "manual_pending", "failed") or not p.approved_at:
        if "content.approve" not in ctx.permissions:
            raise _bad("This post hasn't been approved. Ask someone who can approve posts.", 403)
        p.approved_by, p.approved_at = ctx.user.id, now
    p.status, p.scheduled_at, p.error, p.approval_deadline = "scheduled", now, None, None
    svc.add_activity(ctx.session, p, "publish_now", None, user=ctx.user)
    audit.record(ctx.session, action="post.publish_now", organization_id=ctx.org_id, actor=ctx.user, entity_type="post",
                 entity_id=p.id, summary=f"Published a {p.platform} post now", ip=ctx.ip)
    return await enqueue(ctx.session, "post.publish", ctx.org_id, {"post_id": str(p.id)}, dedupe_key=f"pub:{p.id}",
                         priority=1, created_by=ctx.user.id)


@router.post("/posts/{pid}/mark-published")
async def mark_published(pid: uuid.UUID, body: MarkPublishedIn | None = None,
                         ctx: Context = Depends(require("content.publish"))):
    p = await _post(ctx, pid)
    if p.status not in ("manual_pending", "failed", "scheduled", "missed"):
        raise _bad("Only posts waiting to be posted by hand can be marked as published.", 409)
    url = ((body.external_url if body else None) or "").strip() or None
    if url and not re.match(r"^https?://\S+$", url):
        raise _bad("Paste the full link to the post, starting with https://")
    p.status, p.published_at, p.external_url, p.error = "published", svc.now_utc(), url, None
    svc.add_activity(ctx.session, p, "marked_published", url, user=ctx.user)
    audit.record(ctx.session, action="post.marked_published", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="post", entity_id=p.id, summary="Marked a post as published by hand", ip=ctx.ip)
    await ctx.session.commit()
    return await _out_one(ctx, p)


@router.delete("/posts/{pid}", status_code=204)
async def delete_post(pid: uuid.UUID, ctx: Context = Depends(require("content.create"))):
    p = await _post(ctx, pid)
    await _delete_post(ctx, p)
    await ctx.session.commit()


async def _delete_post(ctx: Context, p: Post) -> None:
    """Delete a post, or keep a skipped tombstone for a future schedule slot (caller commits)."""
    if p.status in ("published", "publishing"):
        raise _bad("Published posts can't be deleted here. Delete it on the platform.", 409)
    audit.record(ctx.session, action="post.deleted", organization_id=ctx.org_id, actor=ctx.user, entity_type="post",
                 entity_id=p.id, summary=f"Deleted a {p.platform} post", ip=ctx.ip)
    if p.schedule_id and p.scheduled_at and p.scheduled_at > svc.now_utc():
        # Keep a tombstone so the planner doesn't refill this slot.
        p.status, p.text, p.media_ids, p.embedding = "skipped", None, [], None
        svc.add_activity(ctx.session, p, "deleted", "Slot skipped", user=ctx.user)
    else:
        await ctx.session.delete(p)


class DuplicateIn(BaseModel):
    account_id: uuid.UUID
    scheduled_at: datetime | None = None


@router.post("/posts/{pid}/duplicate", status_code=201)
async def duplicate(pid: uuid.UUID, body: DuplicateIn, ctx: Context = Depends(require("content.create"))):
    src = await _post(ctx, pid)
    if src.status in ("planned", "generating"):
        raise _bad("This post is still being written. Duplicate it when it's ready.", 409)
    account = await _account(ctx, body.account_id)
    supported = supported_types(account.platform)
    post_type = src.post_type if src.post_type in supported else ("text" if "text" in supported else None)
    if post_type is None:
        raise _bad(f"{svc.platform_of(account.platform).label} doesn't support this kind of post.")
    at = _aware(body.scheduled_at, _tz(ctx))
    p = Post(
        organization_id=ctx.org_id, social_account_id=account.id, platform=account.platform, post_type=post_type,
        pillar_id=src.pillar_id, campaign_id=src.campaign_id, topic=src.topic, title=src.title, text=src.text,
        hashtags=list(src.hashtags or [])[: svc.platform_of(account.platform).max_hashtags], link_url=src.link_url,
        first_comment=src.first_comment, media_ids=list(src.media_ids or []) if post_type != "text" else [],
        scheduled_at=at, status="draft", mode="manual", created_by=ctx.user.id, qa={},
        meta={"source": "duplicate", "duplicated_from": str(src.id), "keep_media": True,
              **{k: (src.meta or {}).get(k) for k in ("image_brief", "alt_text", "slides", "video_script") if (src.meta or {}).get(k)}},
    )
    ctx.session.add(p)
    await ctx.session.flush()
    await svc.recheck_post(ctx.session, p)
    svc.add_activity(ctx.session, p, "created", f"Copied from a {svc.platform_of(src.platform).label} post", user=ctx.user)
    audit.record(ctx.session, action="post.duplicated", organization_id=ctx.org_id, actor=ctx.user, entity_type="post",
                 entity_id=p.id, summary=f"Copied a post to {account.name}", ip=ctx.ip)
    await ctx.session.commit()
    return await _out_one(ctx, p)


# ================================================================== approvals
@router.get("/approvals")
async def approvals(ctx: Context = Depends(require("content.view"))):
    order = func.coalesce(Post.approval_deadline, Post.scheduled_at)
    rows = list((await ctx.session.scalars(
        select(Post).where(Post.status == "in_review").order_by(order.asc().nullslast(), Post.created_at)
    )).all())
    accounts, pillars = await _lookups(ctx, rows)
    media = await _media_map(ctx, rows)
    return [post_out(p, accounts, pillars, media) for p in rows]


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    action: Literal["approve", "reject"]
    reason: str | None = Field(None, max_length=1000)


@router.post("/approvals/bulk")
async def bulk(body: BulkIn, ctx: Context = Depends(require("content.approve"))):
    results = []
    for pid in dict.fromkeys(body.ids):
        try:
            async with ctx.session.begin_nested():
                if body.action == "approve":
                    msg = await content_actions.approve_by_id(ctx.session, pid, ctx.user)
                else:
                    msg = await content_actions.reject_by_id(ctx.session, pid, ctx.user, body.reason)
            results.append({"id": pid, "ok": True, "message": msg})
        except ValueError as e:
            results.append({"id": pid, "ok": False, "message": str(e)})
    await ctx.session.commit()
    return {"results": results, "ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}


class PostsBulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    action: Literal["approve", "reject", "publish", "delete"]
    reason: str | None = Field(None, max_length=1000)


BULK_PERMS = {"approve": "content.approve", "reject": "content.approve", "publish": "content.publish",
              "delete": "content.create"}
BULK_DONE = {"approve": "Approved", "reject": "Rejected", "publish": "Publishing now", "delete": "Deleted"}


@router.post("/posts/bulk")
async def posts_bulk(body: PostsBulkIn, ctx: Context = Depends(require("content.view"))):
    """Approve, reject, publish now or delete several posts at once. Each post succeeds or fails on its own."""
    perm = BULK_PERMS[body.action]
    if perm not in ctx.permissions:
        raise _bad("You don't have permission to do that.", 403)
    results = []
    for pid in dict.fromkeys(body.ids):
        try:
            async with ctx.session.begin_nested():
                p = await ctx.session.scalar(select(Post).where(Post.id == pid))
                if p is None:
                    raise ValueError("Post not found.")
                if body.action == "approve":
                    msg = await content_actions.approve_by_id(ctx.session, pid, ctx.user, "app")
                elif body.action == "reject":
                    msg = await content_actions.reject_by_id(ctx.session, pid, ctx.user, body.reason, "app")
                elif body.action == "publish":
                    await _publish_now(ctx, p)
                    msg = BULK_DONE["publish"]
                else:
                    await _delete_post(ctx, p)
                    msg = BULK_DONE["delete"]
            results.append({"id": pid, "ok": True, "message": msg})
        except HTTPException as e:
            results.append({"id": pid, "ok": False, "message": str(e.detail)})
        except ValueError as e:
            results.append({"id": pid, "ok": False, "message": str(e)})
    if body.action == "delete" and any(r["ok"] for r in results):
        audit.record(ctx.session, action="post.bulk_deleted", organization_id=ctx.org_id, actor=ctx.user,
                     summary=f"Deleted {sum(1 for r in results if r['ok'])} posts", ip=ctx.ip)
    await ctx.session.commit()
    return {"results": results, "ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}


# ================================================================== calendar
@router.get("/calendar")
async def calendar(
    start: datetime, end: datetime, account_id: uuid.UUID | None = None, platform: str | None = None,
    status_: str | None = Query(None, alias="status"), ctx: Context = Depends(require("content.view")),
):
    tz = _tz(ctx)
    s, e = _aware(start, tz), _aware(end, tz)
    if e <= s:
        raise _bad("The end must be after the start.")
    if e - s > timedelta(days=62):
        raise _bad("Show at most two months at a time.")
    when = func.coalesce(Post.published_at, Post.scheduled_at)
    conds = [when >= s, when < e, Post.status != "skipped"]
    if account_id:
        conds.append(Post.social_account_id == account_id)
    if platform:
        conds.append(Post.platform.in_(_csv(platform)))
    if status_:
        conds.append(Post.status.in_(_csv(status_)))
    rows = list((await ctx.session.scalars(select(Post).where(and_(*conds)).order_by(when))).all())
    accounts, pillars = await _lookups(ctx, rows)
    out = []
    for p in rows:
        a, pl = accounts.get(p.social_account_id), pillars.get(p.pillar_id)
        out.append({
            "id": p.id, "at": p.published_at or p.scheduled_at, "status": p.status, "platform": p.platform,
            "platform_label": svc.platform_of(p.platform).label, "post_type": p.post_type,
            "account_name": a.name if a else None, "account_id": p.social_account_id,
            "title": (p.title or p.topic or (p.text or "")[:80] or "Untitled").strip()[:120],
            "pillar_name": pl.name if pl else None, "color": pl.color if pl else "#64748b",
        })
    return out
