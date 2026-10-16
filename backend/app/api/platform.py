"""Platform administration: manage tenants (organizations). Platform admins only."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.organization import check_timezone
from app.core.db import platform_session
from app.core.deps import Context, require_platform_admin
from app.models import Membership, Organization
from app.schemas import OrganizationAdminUpdate, OrganizationCreate, OrganizationCreated, OrganizationOut
from app.services import audit
from app.services.tenancy import provision_organization

router = APIRouter(prefix="/platform", tags=["platform"])


class OrgWithCounts(OrganizationOut):
    member_count: int


@router.get("/organizations", response_model=list[OrgWithCounts])
async def list_orgs(ctx: Context = Depends(require_platform_admin)):
    async with platform_session() as s:
        counts = dict(
            (await s.execute(select(Membership.organization_id, func.count()).group_by(Membership.organization_id))).all()
        )
        orgs = (await s.scalars(select(Organization).order_by(Organization.name))).all()
        return [OrgWithCounts(**OrganizationOut.model_validate(o).model_dump(), member_count=counts.get(o.id, 0)) for o in orgs]


@router.post("/organizations", response_model=OrganizationCreated, status_code=201)
async def create_org(body: OrganizationCreate, ctx: Context = Depends(require_platform_admin)):
    check_timezone(body.timezone)
    async with platform_session() as s:
        org, admin, temp = await provision_organization(
            s, name=body.name, admin_email=body.admin_email, admin_name=body.admin_name,
            timezone=body.timezone, website=body.website,
        )
        audit.record(s, action="platform.org_created", organization_id=None, actor=ctx.user,
                     entity_type="organization", entity_id=org.id,
                     summary=f"Created organization {org.name} with admin {admin.email}", ip=ctx.ip)
        audit.record(s, action="org.created", organization_id=org.id, actor=ctx.user,
                     entity_type="organization", entity_id=org.id, summary=f"Organization created; {admin.email} is Administrator", ip=ctx.ip)
        await s.commit()
        return OrganizationCreated(organization=OrganizationOut.model_validate(org), admin_email=admin.email, temporary_password=temp)


@router.patch("/organizations/{org_id}", response_model=OrganizationOut)
async def update_org(org_id: UUID, body: OrganizationAdminUpdate, ctx: Context = Depends(require_platform_admin)):
    async with platform_session() as s:
        org = await s.get(Organization, org_id)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")
        data = body.model_dump(exclude_unset=True, exclude_none=True)
        if "timezone" in data:
            check_timezone(data["timezone"])
        for k, v in data.items():
            setattr(org, k, v)
        audit.record(s, action="platform.org_updated", organization_id=None, actor=ctx.user,
                     entity_type="organization", entity_id=org.id,
                     summary=f"Updated {org.name}: {', '.join(f'{k}={v}' for k, v in data.items())}", ip=ctx.ip)
        await s.commit()
        return org
