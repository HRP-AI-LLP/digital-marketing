import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select

from app.core.deps import Context, require
from app.models import AuditLog, Organization
from app.schemas import AuditOut, OrganizationOut, OrganizationUpdate, Page
from app.services import audit

router = APIRouter(tags=["organization"])


def check_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown time zone: {tz}")


@router.get("/organization", response_model=OrganizationOut)
async def get_org(ctx: Context = Depends(require("org.settings.view"))):
    return ctx.org


REQUIRED = {"name", "timezone", "currency"}


@router.patch("/organization", response_model=OrganizationOut)
async def update_org(body: OrganizationUpdate, ctx: Context = Depends(require("org.settings.manage"))):
    org = await ctx.session.get(Organization, ctx.org_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("timezone"):
        check_timezone(data["timezone"])
    changed = []
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip() or None
        if v is None and k in REQUIRED:
            continue  # required fields can't be cleared
        if v is None and k in ("address", "registration", "social_links"):
            v = {}
        if v is None and k == "contacts":
            v = []
        if getattr(org, k) != v:
            setattr(org, k, v)
            changed.append(k)
    audit.record(ctx.session, action="org.updated", organization_id=org.id, actor=ctx.user,
                 entity_type="organization", entity_id=org.id,
                 summary=f"Updated organization details ({', '.join(changed) or 'no changes'})", ip=ctx.ip)
    await ctx.session.commit()
    await ctx.session.refresh(org)
    return org


@router.post("/organization/logo", response_model=OrganizationOut)
async def upload_logo(request: Request, variant: Literal["main", "dark"] = "main", use_for_brand: bool = True,
                      ctx: Context = Depends(require("org.settings.manage"))):
    """Upload the organization logo (PNG, JPG or WebP; multipart `files`/`file` or a raw body).

    variant=dark is a version for dark backgrounds (the sidebar). With use_for_brand, the main logo also becomes the
    logo on generated images when the brand kit doesn't have one yet.
    """
    from app.api.media import MAX_UPLOAD_BYTES, _parse_multipart, _read_body
    from app.services import content as content_svc
    from app.services import media as media_svc

    ctype = request.headers.get("content-type", "")
    body = await _read_body(request, min(MAX_UPLOAD_BYTES, 10 * 1024 * 1024))
    files = await asyncio.to_thread(_parse_multipart, body, ctype) if ctype.startswith("multipart/") else \
        [(request.headers.get("x-filename") or "logo.png", body)]
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an image file for the logo.")
    name, data = files[0]
    try:
        asset = await media_svc.save_upload(ctx.session, ctx.org_id, data, name)
    except media_svc.MediaUnavailable as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, e.message) from None
    if asset.kind != "image":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The logo must be a PNG, JPG or WebP image.")
    asset.alt_text = asset.alt_text or "Logo"
    org = await ctx.session.get(Organization, ctx.org_id)
    if variant == "dark":
        org.logo_dark_asset_id = asset.id
    else:
        org.logo_asset_id = asset.id
        if use_for_brand:
            brand = await content_svc.get_brand(ctx.session, ctx.org_id)
            if not brand.logo_asset_id:
                brand.logo_asset_id = asset.id
    audit.record(ctx.session, action="org.logo", organization_id=org.id, actor=ctx.user, entity_type="organization",
                 entity_id=org.id, summary=f"Uploaded the {'dark-background ' if variant == 'dark' else ''}logo", ip=ctx.ip)
    await ctx.session.commit()
    await ctx.session.refresh(org)
    return org


@router.delete("/organization/logo", response_model=OrganizationOut)
async def remove_logo(variant: Literal["main", "dark"] = "main", ctx: Context = Depends(require("org.settings.manage"))):
    org = await ctx.session.get(Organization, ctx.org_id)
    if variant == "dark":
        org.logo_dark_asset_id = None
    else:
        org.logo_asset_id = None
    audit.record(ctx.session, action="org.logo", organization_id=org.id, actor=ctx.user, entity_type="organization",
                 entity_id=org.id, summary=f"Removed the {'dark-background ' if variant == 'dark' else ''}logo", ip=ctx.ip)
    await ctx.session.commit()
    await ctx.session.refresh(org)
    return org


_AUDIT_SORT = {"created_at": AuditLog.created_at, "action": AuditLog.action, "actor_email": AuditLog.actor_email,
               "entity_type": AuditLog.entity_type, "ip_address": AuditLog.ip_address, "summary": AuditLog.summary}


def _audit_filters(ctx: Context, *, action: str | None, q: str | None, actor: str | None, entity_type: str | None,
                   start: date | None, end: date | None, tz: str | None):
    conds = [AuditLog.organization_id == ctx.org_id]
    if action:
        conds.append(AuditLog.action.startswith(action))
    if actor:
        conds.append(AuditLog.actor_email.is_(None) if actor == "__system__" else AuditLog.actor_email == actor)
    if entity_type:
        conds.append(AuditLog.entity_type.is_(None) if entity_type == "__none__" else AuditLog.entity_type == entity_type)
    try:
        zone = ZoneInfo(tz) if tz else UTC
    except ZoneInfoNotFoundError:
        zone = UTC
    if start:
        conds.append(AuditLog.created_at >= datetime.combine(start, time.min, zone))
    if end:
        conds.append(AuditLog.created_at < datetime.combine(end + timedelta(days=1), time.min, zone))
    if q and q.strip():
        like = f"%{q.strip().replace('%', r'\%').replace('_', r'\_')}%"
        conds.append(or_(AuditLog.summary.ilike(like), AuditLog.action.ilike(like), AuditLog.actor_email.ilike(like),
                         AuditLog.entity_type.ilike(like), AuditLog.entity_id.ilike(like), AuditLog.ip_address.ilike(like)))
    return conds


@router.get("/audit", response_model=Page[AuditOut])
async def list_audit(
    ctx: Context = Depends(require("audit.view")),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=80),
    q: str | None = Query(None, max_length=200),
    actor: str | None = Query(None, max_length=254),
    entity_type: str | None = Query(None, max_length=60),
    start: date | None = None,
    end: date | None = None,
    sort: Literal["created_at", "action", "actor_email", "entity_type", "ip_address", "summary"] = "created_at",
    dir: Literal["asc", "desc"] = "desc",
):
    """The activity log, with search, filters and sorting (dates are days in the organization's timezone)."""
    org = await ctx.session.get(Organization, ctx.org_id)
    conds = _audit_filters(ctx, action=action, q=q, actor=actor, entity_type=entity_type, start=start, end=end,
                           tz=getattr(org, "timezone", None))
    total = await ctx.session.scalar(select(func.count()).select_from(AuditLog).where(*conds)) or 0
    col = _AUDIT_SORT[sort]
    order = [col.asc().nulls_last() if dir == "asc" else col.desc().nulls_last()]
    if sort != "created_at":
        order.append(AuditLog.created_at.desc())
    rows = (await ctx.session.scalars(select(AuditLog).where(*conds).order_by(*order, AuditLog.id).limit(limit).offset(offset))).all()
    return Page[AuditOut](items=[AuditOut.model_validate(r) for r in rows], total=total)


@router.get("/audit/facets")
async def audit_facets(ctx: Context = Depends(require("audit.view"))):
    """Values for the activity log's filters, with how many entries each has."""
    base = AuditLog.organization_id == ctx.org_id
    cat = func.split_part(AuditLog.action, ".", 1)
    cats = (await ctx.session.execute(select(cat, func.count()).where(base).group_by(cat).order_by(func.count().desc()))).all()
    actions = (await ctx.session.execute(select(AuditLog.action, func.count()).where(base).group_by(AuditLog.action)
                                         .order_by(AuditLog.action))).all()
    actors = (await ctx.session.execute(select(AuditLog.actor_email, func.count()).where(base).group_by(AuditLog.actor_email)
                                        .order_by(func.count().desc()))).all()
    types = (await ctx.session.execute(select(AuditLog.entity_type, func.count()).where(base).group_by(AuditLog.entity_type)
                                       .order_by(func.count().desc()))).all()
    span = (await ctx.session.execute(select(func.min(AuditLog.created_at), func.max(AuditLog.created_at)).where(base))).one()
    return {
        "categories": [{"key": c, "count": n} for c, n in cats if c],
        "actions": [{"key": a, "count": n} for a, n in actions],
        "actors": [{"key": a or "__system__", "email": a, "count": n} for a, n in actors],
        "entity_types": [{"key": t or "__none__", "type": t, "count": n} for t, n in types],
        "first_at": span[0], "last_at": span[1],
    }
