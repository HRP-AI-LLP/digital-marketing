import asyncio
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

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


@router.get("/audit", response_model=Page[AuditOut])
async def list_audit(
    ctx: Context = Depends(require("audit.view")),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = None,
):
    q = select(AuditLog).where(AuditLog.organization_id == ctx.org_id)
    if action:
        q = q.where(AuditLog.action.startswith(action))
    total = await ctx.session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = (await ctx.session.scalars(q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))).all()
    return Page[AuditOut](items=[AuditOut.model_validate(r) for r in rows], total=total)
