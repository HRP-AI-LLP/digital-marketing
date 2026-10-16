from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.core.db import platform_session
from app.core.deps import Context, require
from app.core.security import generate_password, hash_password
from app.models import Membership, MembershipRole, Role, User
from app.schemas import MemberCreate, MemberCreated, MemberOut, MemberUpdate, PasswordReset, RoleRef
from app.services import audit

router = APIRouter(prefix="/users", tags=["users"])


async def _roles_by_id(ctx: Context) -> dict[UUID, Role]:
    return {r.id: r for r in (await ctx.session.scalars(select(Role))).all()}


def _out(m: Membership, roles: dict[UUID, Role]) -> MemberOut:
    return MemberOut(
        user_id=m.user.id,
        email=m.user.email,
        full_name=m.user.full_name,
        is_active=m.is_active and m.user.is_active,
        roles=[RoleRef.model_validate(roles[r.role_id]) for r in m.roles if r.role_id in roles],
        last_login_at=m.user.last_login_at,
        joined_at=m.created_at,
    )


async def _get_membership(ctx: Context, user_id: UUID) -> Membership:
    m = await ctx.session.scalar(
        select(Membership).where(Membership.organization_id == ctx.org_id, Membership.user_id == user_id)
    )
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That user isn't part of this organization.")
    return m


async def _validated_roles(ctx: Context, role_ids: list[UUID]) -> list[Role]:
    roles = (await ctx.session.scalars(select(Role).where(Role.id.in_(role_ids)))).all()
    if len(roles) != len(set(role_ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more roles don't exist in this organization.")
    # Nobody can hand out more access than they have: granting a role requires holding all of its permissions.
    for r in roles:
        missing = {rp.permission_code for rp in r.permissions} - ctx.permissions
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"You can't give the {r.name} role because it includes permissions you don't have.")
    return list(roles)


async def _active_admin_count(ctx: Context, excluding: UUID | None = None) -> int:
    q = (
        select(func.count(func.distinct(Membership.id)))
        .join(MembershipRole, MembershipRole.membership_id == Membership.id)
        .join(Role, Role.id == MembershipRole.role_id)
        .where(Role.is_system.is_(True), Membership.is_active.is_(True))
    )
    if excluding:
        q = q.where(Membership.id != excluding)
    return await ctx.session.scalar(q) or 0


async def _is_only_in_this_org(user_id: UUID, org_id: UUID) -> bool:
    async with platform_session() as s:
        orgs = (await s.scalars(select(Membership.organization_id).where(Membership.user_id == user_id))).all()
    return set(orgs) <= {org_id}


@router.get("", response_model=list[MemberOut])
async def list_users(ctx: Context = Depends(require("user.view"))):
    roles = await _roles_by_id(ctx)
    members = (await ctx.session.scalars(select(Membership))).unique().all()
    return sorted((_out(m, roles) for m in members), key=lambda x: x.full_name.lower())


@router.post("", response_model=MemberCreated, status_code=201)
async def add_user(body: MemberCreate, ctx: Context = Depends(require("user.manage"))):
    roles = await _validated_roles(ctx, body.role_ids)
    email = body.email.lower()
    user = await ctx.session.scalar(select(User).where(User.email == email))
    temp = None
    if user is None:
        temp = generate_password()
        user = User(email=email, full_name=body.full_name, password_hash=hash_password(temp), must_change_password=True)
        ctx.session.add(user)
        await ctx.session.flush()
    elif await ctx.session.scalar(select(Membership.id).where(Membership.user_id == user.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{email} is already part of this organization.")

    m = Membership(organization_id=ctx.org_id, user_id=user.id)
    m.roles = [MembershipRole(organization_id=ctx.org_id, role_id=r.id) for r in roles]
    ctx.session.add(m)
    audit.record(ctx.session, action="user.added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="user", entity_id=user.id,
                 summary=f"Added {email} as {', '.join(r.name for r in roles)}", ip=ctx.ip)
    await ctx.session.commit()
    m = await _get_membership(ctx, user.id)
    return MemberCreated(member=_out(m, await _roles_by_id(ctx)), temporary_password=temp)


@router.patch("/{user_id}", response_model=MemberOut)
async def update_user(user_id: UUID, body: MemberUpdate, ctx: Context = Depends(require("user.manage"))):
    m = await _get_membership(ctx, user_id)
    changes = []

    if body.full_name is not None and body.full_name != m.user.full_name:
        if not await _is_only_in_this_org(user_id, ctx.org_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "This person also belongs to another organization, so only they can change their name.")
        m.user.full_name = body.full_name
        changes.append("name")

    if body.role_ids is not None:
        roles = await _validated_roles(ctx, body.role_ids)
        removes_admin = not any(r.is_system for r in roles)
        if removes_admin and await _active_admin_count(ctx, excluding=m.id) == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "This is the last Administrator. Make someone else an Administrator first.")
        m.roles = [MembershipRole(organization_id=ctx.org_id, role_id=r.id) for r in roles]
        changes.append("roles → " + ", ".join(r.name for r in roles))

    if body.is_active is not None and body.is_active != m.is_active:
        if user_id == ctx.user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't deactivate your own account.")
        if not body.is_active and await _active_admin_count(ctx, excluding=m.id) == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "This is the last Administrator. Make someone else an Administrator first.")
        m.is_active = body.is_active
        changes.append("activated" if body.is_active else "deactivated")

    if changes:
        audit.record(ctx.session, action="user.updated", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="user", entity_id=user_id,
                     summary=f"Updated {m.user.email}: {'; '.join(changes)}", ip=ctx.ip)
    await ctx.session.commit()
    m = await _get_membership(ctx, user_id)
    return _out(m, await _roles_by_id(ctx))


@router.post("/{user_id}/reset-password", response_model=PasswordReset)
async def reset_password(user_id: UUID, ctx: Context = Depends(require("user.manage"))):
    m = await _get_membership(ctx, user_id)
    if m.user.is_platform_admin or not await _is_only_in_this_org(user_id, ctx.org_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This person also belongs to another organization, so they must reset it themselves.")
    temp = generate_password()
    m.user.password_hash = hash_password(temp)
    m.user.must_change_password = True
    m.user.token_version += 1
    audit.record(ctx.session, action="user.password_reset", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="user", entity_id=user_id, summary=f"Reset password for {m.user.email}", ip=ctx.ip)
    await ctx.session.commit()
    return PasswordReset(temporary_password=temp)
