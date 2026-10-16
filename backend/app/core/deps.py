from dataclasses import dataclass, field
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, set_tenant
from app.core.permissions import ALL_CODES
from app.core.security import decode_access_token
from app.models import Membership, MembershipRole, Organization, RolePermission, User

bearer = HTTPBearer(auto_error=False)


@dataclass
class Context:
    session: AsyncSession
    user: User
    org: Organization | None
    membership: Membership | None
    permissions: set[str] = field(default_factory=set)
    ip: str | None = None

    @property
    def org_id(self) -> UUID:
        if self.org is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select an organization first.")
        return self.org.id


def _unauthorized(msg: str = "Sign in again to continue.") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, msg, headers={"WWW-Authenticate": "Bearer"})


async def permission_codes(session: AsyncSession, membership_id: UUID) -> set[str]:
    rows = await session.scalars(
        select(RolePermission.permission_code)
        .join(MembershipRole, MembershipRole.role_id == RolePermission.role_id)
        .where(MembershipRole.membership_id == membership_id)
    )
    return set(rows.all())


async def get_context(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> Context:
    if creds is None:
        raise _unauthorized()
    try:
        payload = decode_access_token(creds.credentials)
        user_id = UUID(payload["sub"])
        org_id = UUID(payload["org"]) if payload.get("org") else None
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized()

    # Scope every query in this request to the token's organization (enforced by RLS).
    set_tenant(session, org_id)

    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.token_version != payload.get("tv"):
        raise _unauthorized()

    org = membership = None
    perms: set[str] = set()
    if org_id:
        org = await session.get(Organization, org_id)
        if org is None:
            raise _unauthorized()
        if org.status != "active" and not user.is_platform_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This organization is suspended.")
        membership = await session.scalar(
            select(Membership).where(Membership.organization_id == org_id, Membership.user_id == user.id)
        )
        if membership and membership.is_active:
            perms = await permission_codes(session, membership.id)
        elif user.is_platform_admin:
            perms = set(ALL_CODES)  # platform support access
        else:
            raise _unauthorized("You no longer have access to this organization.")

    return Context(
        session=session,
        user=user,
        org=org,
        membership=membership,
        permissions=perms,
        ip=request.client.host if request.client else None,
    )


def require(*codes: str):
    """Dependency: current user must hold every listed permission in the current organization."""

    async def checker(ctx: Context = Depends(get_context)) -> Context:
        if ctx.org is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select an organization first.")
        missing = [c for c in codes if c not in ctx.permissions]
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"You need the '{missing[0]}' permission for this."
            )
        return ctx

    return checker


async def require_platform_admin(ctx: Context = Depends(get_context)) -> Context:
    if not ctx.user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only platform administrators can do this.")
    return ctx
