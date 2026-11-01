"""Security and resilience in the worker: the daily database backup and new-device sign-in emails."""

import asyncio
import logging
import uuid
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.models import Organization, User
from app.services import backups
from app.services.jobs import JobContext, handler, periodic

log = logging.getLogger(__name__)


@periodic(300, name="backups.daily", offline_ok=True)
async def daily_backup() -> None:
    """Every 5 minutes: is today's backup due (past the set time and not done yet)? Works offline too."""
    await backups.maybe_run_daily()


@handler("security.signin_notice")
async def signin_notice(ctx: JobContext) -> dict:
    """Email someone who just signed in from a device we haven't seen them use. Uses the organization's default
    sender (Settings → Organization → Email); nothing is sent without one."""
    from app.services.mailer import default_sender
    from app.services.notify import send_email

    user = await ctx.session.get(User, uuid.UUID(ctx.payload["user_id"]))
    org = await ctx.session.get(Organization, ctx.org_id) if ctx.org_id else None
    if user is None or org is None:
        return {"sent": False, "reason": "user or organization gone"}
    cfg = await default_sender(ctx.session, org)
    if cfg is None:
        return {"sent": False, "reason": "no email sender set up"}
    try:
        at = datetime.fromisoformat(ctx.payload.get("at") or "").astimezone(ZoneInfo(org.timezone or "Asia/Kolkata"))
        when = at.strftime("%d %b %Y, %H:%M ") + (org.timezone or "")
    except (ValueError, KeyError):
        when = "just now"
    device = ctx.payload.get("device") or "a new device"
    ip = ctx.payload.get("ip")
    first = (user.full_name or "").split(" ")[0] or "there"
    subject = "New sign-in to HRP Digital Marketing Studio"
    lines = [f"Hi {first},", "", f"Your account ({user.email}) was just used to sign in to {org.name} from {device}.",
             f"When: {when}"] + ([f"Network address: {ip}"] if ip else []) + [
        "", "If this was you, there's nothing to do.",
        "If it wasn't, change your password now (Account → Password), then open Account → Security and press "
        "“Sign out other devices”. Turning on two-step sign-in stops this from happening again."]
    html = (f"<p>Hi {escape(first)},</p><p>Your account (<b>{escape(user.email)}</b>) was just used to sign in to "
            f"{escape(org.name)} from <b>{escape(device)}</b>.</p>"
            f"<p style=\"color:#475569\">When: {escape(when)}" + (f"<br>Network address: {escape(ip)}" if ip else "")
            + "</p><p>If this was you, there's nothing to do.</p><p>If it wasn't, change your password now "
              "(Account → Password), then open Account → Security and press “Sign out other devices”. Turning on "
              "two-step sign-in stops this from happening again.</p>")
    try:
        await asyncio.wait_for(send_email(cfg, user.email, subject, "\n".join(lines), html=html,
                                          link=f"{get_settings().sign_in_url}/account/security"), timeout=60)
    except Exception as e:  # noqa: BLE001
        log.warning("sign-in notice to %s failed: %s", user.email, type(e).__name__)
        return {"sent": False, "reason": "mail server refused"}
    return {"sent": True}
