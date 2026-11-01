"""Account → Security: two-step sign-in (authenticator app), recovery codes and signed-in devices.
Plus the organization's security policy (require two-step sign-in), which only Administrators can change."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.db import platform_session
from app.core.deps import Context, get_context, is_org_admin, require
from app.models import Organization, RefreshToken, User
from app.services import audit, qr, twofa

router = APIRouter(tags=["security"])


def _hard_revoked_at() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)  # no multi-tab grace (see auth.refresh)


async def _active_sessions(s, user_id: UUID) -> list[RefreshToken]:
    rows = (await s.scalars(select(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > datetime.now(UTC)).order_by(RefreshToken.created_at.desc()))).all()
    return list(rows)


def _session_out(t: RefreshToken, current: UUID | None) -> dict:
    return {"id": str(t.id), "device_name": t.device_name or "Unknown device",
            "signed_in_at": (t.signed_in_at or t.created_at).isoformat(), "last_used_at": t.created_at.isoformat(),
            "ip_address": t.ip_address, "current": t.id == current}


def _state(user: User, org: Organization | None) -> dict:
    return {"totp_enabled": user.totp_enabled,
            "enabled_at": user.totp_enabled_at.isoformat() if user.totp_enabled_at else None,
            "recovery_codes_left": len(user.recovery_codes or []) if user.totp_enabled else 0,
            "required_by_org": twofa.security_settings(org)["require_2fa"],
            "setup_pending": bool(not user.totp_enabled and user.totp_secret_enc)}


def _check_code(user: User, body: "CodeIn") -> str:
    """Verify a code for an action on the account (turn off, new recovery codes). Raises when wrong or locked."""
    if wait := twofa.locked_minutes(user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many wrong codes. Try again in {wait} minutes.")
    if not (body.code or body.recovery_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the 6-digit code from your authenticator app.")
    via = twofa.check(user, body.code, body.recovery_code)
    if via is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "That code didn't work. Check the code in your authenticator app and try again.")
    return via


class CodeIn(BaseModel):
    code: str | None = Field(None, max_length=12)
    recovery_code: str | None = Field(None, max_length=40)


@router.get("/account/security")
async def security_overview(ctx: Context = Depends(get_context)):
    async with platform_session() as s:
        user = await s.get(User, ctx.user.id)
        sessions = await _active_sessions(s, user.id)
        return {**_state(user, ctx.org), "sessions": [_session_out(t, ctx.session_id) for t in sessions]}


@router.post("/account/2fa/setup")
async def start_setup(ctx: Context = Depends(get_context)):
    """A new secret for the authenticator app (not active until confirmed with a code)."""
    async with platform_session() as s:
        user = await s.get(User, ctx.user.id)
        if user.totp_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Two-step sign-in is already on. Turn it off first to move it to a new phone.")
        secret = twofa.new_secret()
        twofa.set_secret(user, secret)
        await s.commit()
    uri = twofa.otpauth_uri(secret, ctx.user.email)
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "otpauth_uri": uri,
            "qr_svg": qr.svg(uri), "issuer": twofa.ISSUER, "account": ctx.user.email}


@router.post("/account/2fa/enable")
async def confirm_setup(body: CodeIn, ctx: Context = Depends(get_context)):
    async with platform_session() as s:
        user = await s.get(User, ctx.user.id)
        if user.totp_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two-step sign-in is already on.")
        secret = twofa.get_secret(user)
        if not secret:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start the setup again: press “Set up”.")
        if wait := twofa.locked_minutes(user):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many wrong codes. Try again in {wait} minutes.")
        st = twofa.match_step(secret, body.code or "")
        if st is None:
            twofa.record_failure(user)
            await s.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "That code didn't match. Make sure the phone's clock is set automatically, then "
                                "type the code that's showing now.")
        twofa.record_success(user)
        user.totp_last_step = st
        user.totp_enabled = True
        user.totp_enabled_at = datetime.now(UTC)
        codes = twofa.new_recovery_codes(user)
        audit.record(s, action="auth.2fa_enabled", organization_id=ctx.org.id if ctx.org else None, actor=user,
                     entity_type="user", entity_id=user.id, summary="Turned on two-step sign-in", ip=ctx.ip)
        await s.commit()
        return {**_state(user, ctx.org), "recovery_codes": codes}


@router.post("/account/2fa/disable")
async def disable(body: CodeIn, ctx: Context = Depends(get_context)):
    async with platform_session() as s:
        user = await s.get(User, ctx.user.id)
        if not user.totp_enabled:
            twofa.clear(user)
            await s.commit()
            return _state(user, ctx.org)
        try:
            _check_code(user, body)
        except HTTPException:
            await s.commit()  # keep the failure count
            raise
        twofa.clear(user)
        audit.record(s, action="auth.2fa_disabled", organization_id=ctx.org.id if ctx.org else None, actor=user,
                     entity_type="user", entity_id=user.id, summary="Turned off two-step sign-in", ip=ctx.ip)
        await s.commit()
        return _state(user, ctx.org)


@router.post("/account/2fa/recovery-codes")
async def new_recovery_codes(body: CodeIn, ctx: Context = Depends(get_context)):
    async with platform_session() as s:
        user = await s.get(User, ctx.user.id)
        if not user.totp_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Turn on two-step sign-in first.")
        try:
            _check_code(user, body)
        except HTTPException:
            await s.commit()
            raise
        codes = twofa.new_recovery_codes(user)
        audit.record(s, action="auth.2fa_recovery_codes", organization_id=ctx.org.id if ctx.org else None, actor=user,
                     entity_type="user", entity_id=user.id, summary="Made new recovery codes (old ones stop working)",
                     ip=ctx.ip)
        await s.commit()
        return {**_state(user, ctx.org), "recovery_codes": codes}


# ---- devices -------------------------------------------------------------------------------------------
@router.get("/account/sessions")
async def list_sessions(ctx: Context = Depends(get_context)):
    async with platform_session() as s:
        return [_session_out(t, ctx.session_id) for t in await _active_sessions(s, ctx.user.id)]


@router.post("/account/sessions/sign-out-others")
async def sign_out_others(ctx: Context = Depends(get_context)):
    """Ends every other device's session at once: their refresh tokens are revoked and their access tokens stop
    working (token version bump). This device gets fresh tokens in the response."""
    from app.api.auth import _issue

    async with platform_session() as s:
        user = await s.get(User, ctx.user.id)
        current = await s.get(RefreshToken, ctx.session_id) if ctx.session_id else None
        q = update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        if current is not None:
            q = q.where(RefreshToken.id != current.id)
        n = (await s.execute(q.values(revoked_at=_hard_revoked_at(), revoked_reason="signed_out"))).rowcount or 0
        if current is not None and current.revoked_at is None:
            current.revoked_at, current.revoked_reason = _hard_revoked_at(), "signed_out"
        user.token_version += 1
        org = await s.get(Organization, ctx.org.id) if ctx.org else None
        tokens = await _issue(s, user, org, current.device_name if current else None, previous=current, ip=ctx.ip)
        audit.record(s, action="auth.signed_out_others", organization_id=org.id if org else None,
                     actor=user, summary=f"Signed out {n} other device(s)", ip=ctx.ip)
        await s.commit()
    return {"signed_out": n, "tokens": tokens.model_dump(mode="json")}


@router.delete("/account/sessions/{session_id}")
async def sign_out_device(session_id: UUID, ctx: Context = Depends(get_context)):
    async with platform_session() as s:
        t = await s.get(RefreshToken, session_id)
        if t is None or t.user_id != ctx.user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That device isn't signed in.")
        t.revoked_at, t.revoked_reason = _hard_revoked_at(), "signed_out"
        audit.record(s, action="auth.signed_out_device", organization_id=ctx.org.id if ctx.org else None,
                     actor=ctx.user, summary=f"Signed out {t.device_name or 'a device'}", ip=ctx.ip)
        await s.commit()
    return {"ok": True}


# ---- organization policy -------------------------------------------------------------------------------
class OrgSecurityIn(BaseModel):
    require_2fa: bool


@router.get("/organization/security")
async def org_security(ctx: Context = Depends(require("org.settings.view"))):
    from app.models import Membership

    members = (await ctx.session.scalars(select(Membership).where(Membership.is_active.is_(True)))).unique().all()
    on = sum(1 for m in members if m.user.totp_enabled)
    return {**twofa.security_settings(ctx.org), "members": len(members), "members_with_2fa": on,
            "can_change": await is_org_admin(ctx)}


@router.patch("/organization/security")
async def update_org_security(body: OrgSecurityIn, ctx: Context = Depends(require("org.settings.manage"))):
    if not await is_org_admin(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Administrators can change sign-in security.")
    org = await ctx.session.get(Organization, ctx.org_id)
    cur = twofa.security_settings(org)
    cur["require_2fa"] = body.require_2fa
    org.publishing = {**(org.publishing or {}), "security": cur}
    audit.record(ctx.session, action="org.security_updated", organization_id=org.id, actor=ctx.user,
                 entity_type="organization", entity_id=org.id, ip=ctx.ip,
                 summary="Two-step sign-in " + ("required for everyone" if body.require_2fa else "no longer required"))
    await ctx.session.commit()
    return await org_security(ctx)
