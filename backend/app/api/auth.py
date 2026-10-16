from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.deps import Context, get_context, permission_codes
from app.core.permissions import ALL_CODES
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models import Membership, Organization, RefreshToken, Role, User
from app.schemas import (
    ChangePasswordIn,
    LoginIn,
    MembershipSummary,
    OrganizationOut,
    RefreshIn,
    SessionOut,
    SwitchOrgIn,
    TokenOut,
    UserOut,
)
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

BAD_LOGIN = "Email or password is incorrect."


def _hard_revoked_at() -> datetime:
    """Timestamp for revocations that must never get the multi-tab grace (sign-out, theft, password change)."""
    return datetime.now(UTC) - timedelta(days=1)


async def _memberships(session: AsyncSession, user: User) -> list[tuple[Membership, Organization]]:
    rows = await session.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == user.id, Membership.is_active.is_(True))
        .order_by(Organization.name)
    )
    return [(m, o) for m, o in rows.all()]


async def _session_out(session: AsyncSession, user: User, org: Organization | None) -> SessionOut:
    """Build the session payload. `session` must be a platform (bypass) session."""
    pairs = await _memberships(session, user)
    summaries = []
    for m, o in pairs:
        role_names = (
            await session.scalars(select(Role.name).where(Role.id.in_([r.role_id for r in m.roles])))
        ).all()
        summaries.append(
            MembershipSummary(
                organization_id=o.id, organization_name=o.name, organization_slug=o.slug, roles=list(role_names)
            )
        )
    perms: set[str] = set()
    if org is not None:
        current = next((m for m, o in pairs if o.id == org.id), None)
        perms = await permission_codes(session, current.id) if current else set(ALL_CODES)
    return SessionOut(
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(org) if org else None,
        permissions=sorted(perms),
        memberships=summaries,
    )


async def _resolve_org(
    session: AsyncSession, user: User, requested: UUID | None
) -> Organization | None:
    pairs = [(m, o) for m, o in await _memberships(session, user) if o.status == "active" or user.is_platform_admin]
    if requested:
        match = next((o for _, o in pairs if o.id == requested), None)
        if match:
            return match
        if user.is_platform_admin:
            org = await session.get(Organization, requested)
            if org:
                return org
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to that organization.")
    if pairs:
        return pairs[0][1]
    if user.is_platform_admin:
        return None
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account isn't part of any active organization.")


async def _issue(
    session: AsyncSession, user: User, org: Organization | None, device: str | None
) -> TokenOut:
    s = get_settings()
    raw, digest = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            organization_id=org.id if org else None,
            token_hash=digest,
            device_name=device,
            expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days),
        )
    )
    out = await _session_out(session, user, org)
    return TokenOut(
        **out.model_dump(),
        access_token=create_access_token(user.id, org.id if org else None, user.token_version),
        refresh_token=raw,
        expires_in=s.access_token_minutes * 60,
    )


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request) -> TokenOut:
    async with platform_session() as session:
        user = await session.scalar(select(User).where(User.email == body.email.lower()))
        if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
        org = await _resolve_org(session, user, body.organization_id)
        user.last_login_at = datetime.now(UTC)
        tokens = await _issue(session, user, org, body.device_name)
        audit.record(
            session,
            action="auth.login",
            organization_id=org.id if org else None,
            actor=user,
            summary=f"Signed in{f' on {body.device_name}' if body.device_name else ''}",
            ip=request.client.host if request.client else None,
        )
        await session.commit()
        return tokens


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn) -> TokenOut:
    async with platform_session() as session:
        token = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token))
        )
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again to continue.")
        if token.revoked_at is not None and datetime.now(UTC) - token.revoked_at < timedelta(seconds=60):
            # Two tabs refreshed at the same moment with the same token: give the second one a session too.
            user = await session.get(User, token.user_id)
            if user is None or not user.is_active:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again to continue.")
            org = await _resolve_org(session, user, token.organization_id) if token.organization_id else None
            tokens = await _issue(session, user, org, token.device_name)
            await session.commit()
            return tokens
        if token.revoked_at is not None:
            # A rotated token was reused later: treat as theft and sign the user out everywhere.
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == token.user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=_hard_revoked_at())
            )
            await session.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again to continue.")
        if token.expires_at < datetime.now(UTC):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session expired. Sign in again.")
        user = await session.get(User, token.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again to continue.")
        token.revoked_at = datetime.now(UTC)
        org = await _resolve_org(session, user, token.organization_id) if token.organization_id else None
        tokens = await _issue(session, user, org, token.device_name)
        await session.commit()
        return tokens


@router.post("/switch-organization", response_model=TokenOut)
async def switch_organization(body: SwitchOrgIn, ctx: Context = Depends(get_context)) -> TokenOut:
    async with platform_session() as session:
        user = await session.get(User, ctx.user.id)
        org = await _resolve_org(session, user, body.organization_id)
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == hash_refresh_token(body.refresh_token),
                RefreshToken.user_id == user.id,
            )
            .values(revoked_at=_hard_revoked_at())
        )
        tokens = await _issue(session, user, org, None)
        audit.record(session, action="auth.switch_org", organization_id=org.id if org else None, actor=user,
                     summary=f"Switched to {org.name if org else 'platform'}", ip=ctx.ip)
        await session.commit()
        return tokens


@router.post("/logout", status_code=204)
async def logout(body: RefreshIn) -> None:
    async with platform_session() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token))
            .values(revoked_at=_hard_revoked_at())
        )
        await session.commit()


@router.get("/me", response_model=SessionOut)
async def me(ctx: Context = Depends(get_context)) -> SessionOut:
    async with platform_session() as session:
        user = await session.get(User, ctx.user.id)
        org = await session.get(Organization, ctx.org.id) if ctx.org else None
        return await _session_out(session, user, org)


@router.post("/change-password", response_model=TokenOut)
async def change_password(body: ChangePasswordIn, ctx: Context = Depends(get_context)) -> TokenOut:
    if not verify_password(body.current_password, ctx.user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if body.current_password == body.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a password you haven't used here.")
    async with platform_session() as session:
        user = await session.get(User, ctx.user.id)
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = False
        user.token_version += 1  # invalidates access tokens on other devices
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_hard_revoked_at())
        )
        org = await session.get(Organization, ctx.org.id) if ctx.org else None
        tokens = await _issue(session, user, org, None)
        audit.record(session, action="auth.password_changed", organization_id=org.id if org else None,
                     actor=user, summary="Changed password", ip=ctx.ip)
        await session.commit()
        return tokens
