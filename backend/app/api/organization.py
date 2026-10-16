from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
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


@router.patch("/organization", response_model=OrganizationOut)
async def update_org(body: OrganizationUpdate, ctx: Context = Depends(require("org.settings.manage"))):
    org = await ctx.session.get(Organization, ctx.org_id)
    data = body.model_dump(exclude_unset=True)
    if "timezone" in data and data["timezone"]:
        check_timezone(data["timezone"])
    for k, v in data.items():
        if v is not None or k == "website":
            setattr(org, k, v)
    audit.record(ctx.session, action="org.updated", organization_id=org.id, actor=ctx.user,
                 entity_type="organization", entity_id=org.id,
                 summary=f"Updated organization settings ({', '.join(data) or 'no changes'})", ip=ctx.ip)
    await ctx.session.commit()
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
