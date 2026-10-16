from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.core.deps import Context, require
from app.core.permissions import ALL_CODES
from app.models import MembershipRole, Permission, Role, RolePermission
from app.schemas import PermissionOut, RoleIn, RoleOut, RoleUpdate
from app.services import audit

router = APIRouter(tags=["roles"])


async def _out(ctx: Context, role: Role) -> RoleOut:
    count = await ctx.session.scalar(select(func.count()).where(MembershipRole.role_id == role.id)) or 0
    return RoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=sorted(rp.permission_code for rp in role.permissions),
        member_count=count,
    )


def _check_codes(codes: list[str]) -> list[str]:
    unknown = sorted(set(codes) - set(ALL_CODES))
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown permission: {unknown[0]}")
    return sorted(set(codes))


async def _get(ctx: Context, role_id: UUID) -> Role:
    role = await ctx.session.scalar(select(Role).where(Role.id == role_id))
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found.")
    return role


async def _name_taken(ctx: Context, name: str, exclude: UUID | None = None) -> bool:
    q = select(Role.id).where(func.lower(Role.name) == name.lower())
    if exclude:
        q = q.where(Role.id != exclude)
    return bool(await ctx.session.scalar(q))


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(ctx: Context = Depends(require("role.view"))):
    rows = (await ctx.session.scalars(select(Permission))).all()
    order = {c: i for i, c in enumerate(ALL_CODES)}
    return sorted(rows, key=lambda p: order.get(p.code, 999))


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(ctx: Context = Depends(require("role.view"))):
    roles = (await ctx.session.scalars(select(Role).order_by(Role.is_system.desc(), Role.name))).all()
    return [await _out(ctx, r) for r in roles]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleIn, ctx: Context = Depends(require("role.manage"))):
    if await _name_taken(ctx, body.name):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A role named '{body.name}' already exists.")
    role = Role(organization_id=ctx.org_id, name=body.name, description=body.description)
    role.permissions = [RolePermission(organization_id=ctx.org_id, permission_code=c) for c in _check_codes(body.permissions)]
    ctx.session.add(role)
    audit.record(ctx.session, action="role.created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="role", entity_id=role.id, summary=f"Created role {body.name}", ip=ctx.ip)
    await ctx.session.commit()
    return await _out(ctx, await _get(ctx, role.id))


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: UUID, body: RoleUpdate, ctx: Context = Depends(require("role.manage"))):
    role = await _get(ctx, role_id)
    if role.is_system:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The Administrator role always has every permission and can't be edited.")
    if body.name and body.name != role.name:
        if await _name_taken(ctx, body.name, exclude=role.id):
            raise HTTPException(status.HTTP_409_CONFLICT, f"A role named '{body.name}' already exists.")
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permissions is not None:
        role.permissions = [RolePermission(organization_id=ctx.org_id, permission_code=c) for c in _check_codes(body.permissions)]
    audit.record(ctx.session, action="role.updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="role", entity_id=role.id, summary=f"Updated role {role.name}", ip=ctx.ip)
    await ctx.session.commit()
    return await _out(ctx, await _get(ctx, role.id))


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: UUID, ctx: Context = Depends(require("role.manage"))):
    role = await _get(ctx, role_id)
    if role.is_system:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The Administrator role can't be deleted.")
    in_use = await ctx.session.scalar(select(func.count()).where(MembershipRole.role_id == role.id))
    if in_use:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"{in_use} user(s) still have this role. Give them another role first.")
    audit.record(ctx.session, action="role.deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="role", entity_id=role.id, summary=f"Deleted role {role.name}", ip=ctx.ip)
    await ctx.session.delete(role)
    await ctx.session.commit()
