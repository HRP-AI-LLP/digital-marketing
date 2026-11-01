"""Emails to people added to an organization: their sign-in details and temporary password.

The temporary password works once: at the first sign-in the studio asks the person to choose their own
(User.must_change_password). Emails go through the organization's SMTP integration (Settings → Integrations →
Email). Without one nothing is sent and the admin sees the password on screen instead.
"""

from __future__ import annotations

import asyncio
import logging
from html import escape

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Organization, User

log = logging.getLogger(__name__)
SEND_TIMEOUT_S = 30
NO_SMTP = "No email was sent: set up Email (SMTP) in Settings → Integrations to send sign-in details automatically."


def _content(kind: str, org: Organization, user: User, temp: str | None, inviter: str) -> tuple[str, str, str]:
    url = get_settings().sign_in_url
    name = (user.full_name or "").split(" ")[0] or "there"
    if kind == "reset":
        subject = f"Your new temporary password for {org.name}"
        intro = f"{inviter} reset your password for the {org.name} workspace in HRP Digital Marketing Studio."
    elif temp:
        subject = f"You've been added to {org.name} on HRP Digital Marketing Studio"
        intro = f"{inviter} added you to the {org.name} workspace in HRP Digital Marketing Studio."
    else:
        subject = f"You now have access to {org.name}"
        intro = (f"{inviter} added you to the {org.name} workspace in HRP Digital Marketing Studio. "
                 "Sign in with the email and password you already use.")
    lines = [f"Hi {name},", "", intro, "", f"Sign in at: {url}", f"Email: {user.email}"]
    if temp:
        lines += [f"Temporary password: {temp}", "",
                  "This password is for your first sign-in only: you'll be asked to choose your own password straight away."]
    lines += ["", "If you weren't expecting this email, you can ignore it."]
    body = "\n".join(lines)
    rows = (f'<tr><td style="color:#64748b;padding:4px 12px 4px 0">Sign in at</td><td><a href="{escape(url)}">{escape(url)}</a></td></tr>'
            f'<tr><td style="color:#64748b;padding:4px 12px 4px 0">Email</td><td>{escape(user.email)}</td></tr>')
    if temp:
        rows += ('<tr><td style="color:#64748b;padding:4px 12px 4px 0">Temporary password</td>'
                 f'<td><code style="font-size:15px;background:#f1f5f9;padding:3px 8px;border-radius:6px;letter-spacing:.5px">{escape(temp)}</code></td></tr>')
    html = (f"<p>Hi {escape(name)},</p><p>{escape(intro)}</p><table style=\"border-collapse:collapse;margin:8px 0 14px\">{rows}</table>"
            + ("<p>This password is for your first sign-in only: you'll be asked to choose your own password straight away.</p>" if temp else "")
            + '<p style="color:#64748b;font-size:12px">If you weren\'t expecting this email, you can ignore it.</p>')
    return subject, body, html


async def send_access_email(session: AsyncSession, org: Organization, user: User, temp: str | None, inviter: str,
                            kind: str = "added") -> tuple[bool, str]:
    """(sent, message for the admin). Never raises: a failed email must not undo adding the person."""
    from app.services.analytics_reports import smtp_config
    from app.services.notify import send_email

    cfg = await smtp_config(session)
    if cfg is None:
        return False, NO_SMTP
    subject, body, html = _content(kind, org, user, temp, inviter)
    try:
        await asyncio.wait_for(send_email(cfg, user.email, subject, body, html=html), timeout=SEND_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        log.warning("access email to %s failed: %s", user.email, type(e).__name__)
        return False, f"The email to {user.email} couldn't be sent ({type(e).__name__}). Share the password yourself."
    return True, f"Sign-in details were emailed to {user.email}."
