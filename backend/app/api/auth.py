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
    TwoFactorChallengeOut,
    TwoFactorLoginIn,
    MembershipSummary,
    OrganizationOut,
    RefreshIn,
    SessionOut,
    SwitchOrgIn,
    TokenOut,
    UserOut,
)
from app.services import audit, twofa
from app.services.jobs import enqueue

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
        two_factor_setup_required=twofa.setup_required(user, org),
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
    session: AsyncSession, user: User, org: Organization | None, device: str | None, *,
    previous: RefreshToken | None = None, ip: str | None = None,
) -> TokenOut:
    """New device session. `previous`: the token being rotated (keeps its sign-in time, device and address)."""
    s = get_settings()
    raw, digest = new_refresh_token()
    now = datetime.now(UTC)
    token = RefreshToken(
        user_id=user.id,
        organization_id=org.id if org else None,
        token_hash=digest,
        device_name=device or (previous.device_name if previous else None),
        expires_at=now + timedelta(days=s.refresh_token_days),
        signed_in_at=(previous.signed_in_at or previous.created_at) if previous else now,
        ip_address=ip or (previous.ip_address if previous else None),
    )
    session.add(token)
    await session.flush()
    out = await _session_out(session, user, org)
    return TokenOut(
        **out.model_dump(),
        access_token=create_access_token(user.id, org.id if org else None, user.token_version, token.id),
        refresh_token=raw,
        expires_in=s.access_token_minutes * 60,
    )


def device_label(name: str | None, user_agent: str | None) -> str | None:
    """A readable device name: what the app sent, or one worked out from the browser (\"Chrome on Windows\")."""
    if name and name.strip() and name.strip().lower() != "web":
        return name.strip()[:120]
    ua = user_agent or ""
    if not ua:
        return name
    browser = next((b for k, b in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                   ("Safari/", "Safari")) if k in ua), "Browser")
    system = next((o for k, o in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                  ("Mac OS", "Mac"), ("Linux", "Linux")) if k in ua), None)
    return f"{browser} on {system}" if system else browser


async def _complete_login(session: AsyncSession, user: User, org: Organization | None, device: str | None,
                          ip: str | None, via: str | None = None) -> TokenOut:
    user.last_login_at = datetime.now(UTC)
    tokens = await _issue(session, user, org, device, ip=ip)
    audit.record(
        session,
        action="auth.login",
        organization_id=org.id if org else None,
        actor=user,
        summary=f"Signed in{f' on {device}' if device else ''}{f' ({via})' if via else ''}",
        ip=ip,
    )
    known = [d for d in (user.known_devices or []) if isinstance(d, str)]
    key = (device or "").strip().lower()
    if key and key not in known:
        if known and org is not None:
            # A device we haven't seen this person sign in from: tell them by email (sent by the worker).
            await enqueue(session, "security.signin_notice", org.id,
                          {"user_id": str(user.id), "device": device, "ip": ip,
                           "at": datetime.now(UTC).isoformat()}, max_attempts=2)
        user.known_devices = (known + [key])[-20:]
    return tokens


@router.post("/login", response_model=TokenOut | TwoFactorChallengeOut)
async def login(body: LoginIn, request: Request) -> TokenOut | TwoFactorChallengeOut:
    ip = request.client.host if request.client else None
    device = device_label(body.device_name, request.headers.get("user-agent"))
    async with platform_session() as session:
        user = await session.scalar(select(User).where(User.email == body.email.lower()))
        if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
        org = await _resolve_org(session, user, body.organization_id)
        if user.totp_enabled:
            return TwoFactorChallengeOut(challenge=twofa.challenge_token(user, org.id if org else None, device),
                                         expires_in=twofa.CHALLENGE_MINUTES * 60)
        tokens = await _complete_login(session, user, org, device, ip)
        await session.commit()
        return tokens


@router.post("/login/2fa", response_model=TokenOut)
async def login_second_step(body: TwoFactorLoginIn, request: Request) -> TokenOut:
    """Second step: the 6-digit code from the authenticator app, or one of the recovery codes."""
    claims = twofa.read_challenge(body.challenge)
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That sign-in took too long. Enter your email and password again.")
    if not (body.code or body.recovery_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the 6-digit code from your authenticator app.")
    ip = request.client.host if request.client else None
    async with platform_session() as session:
        user = await session.get(User, UUID(claims["sub"]))
        if user is None or not user.is_active or user.token_version != claims.get("tv") or not user.totp_enabled:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again to continue.")
        if wait := twofa.locked_minutes(user):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"Too many wrong codes. Try again in {wait} minute{'s' if wait != 1 else ''}.")
        via = twofa.check(user, body.code, body.recovery_code)
        if via is None:
            locked = twofa.locked_minutes(user)
            audit.record(session, action="auth.2fa_failed", organization_id=UUID(claims["org"]) if claims.get("org") else None,
                         actor=user, summary="Wrong two-step sign-in code" + ("; locked for 10 minutes" if locked else ""), ip=ip)
            await session.commit()
            if locked:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                    f"Too many wrong codes. Try again in {twofa.LOCK_MINUTES} minutes.")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                "That code didn't work. Check the code in your authenticator app and try again."
                                if body.code else "That recovery code isn't valid or was already used.")
        org = await _resolve_org(session, user, UUID(claims["org"]) if claims.get("org") else None)
        tokens = await _complete_login(session, user, org, claims.get("dev"), ip,
                                       via="with a recovery code" if via == "recovery" else None)
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
            tokens = await _issue(session, user, org, token.device_name, previous=token)
            await session.commit()
            return tokens
        if token.revoked_at is not None and token.revoked_reason == "signed_out":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "You were signed out on this device. Sign in again.")
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
        tokens = await _issue(session, user, org, token.device_name, previous=token)
        await session.commit()
        return tokens


@router.post("/switch-organization", response_model=TokenOut)
async def switch_organization(body: SwitchOrgIn, ctx: Context = Depends(get_context)) -> TokenOut:
    async with platform_session() as session:
        user = await session.get(User, ctx.user.id)
        org = await _resolve_org(session, user, body.organization_id)
        old = await session.scalar(select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(body.refresh_token), RefreshToken.user_id == user.id))
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == hash_refresh_token(body.refresh_token),
                RefreshToken.user_id == user.id,
            )
            .values(revoked_at=_hard_revoked_at())
        )
        tokens = await _issue(session, user, org, None, previous=old, ip=ctx.ip)
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
            .values(revoked_at=_hard_revoked_at(), revoked_reason="signed_out")
        )
        org = await session.get(Organization, ctx.org.id) if ctx.org else None
        current = await session.get(RefreshToken, ctx.session_id) if ctx.session_id else None
        tokens = await _issue(session, user, org, current.device_name if current else None, ip=ctx.ip)
        audit.record(session, action="auth.password_changed", organization_id=org.id if org else None,
                     actor=user, summary="Changed password", ip=ctx.ip)
        await session.commit()
        return tokens
