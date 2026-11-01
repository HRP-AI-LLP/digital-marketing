"""Sending files and reports by email from anywhere in the studio.

Senders are the organization's Email (SMTP) integrations: each one is a mailbox (info@hrpai.in, reports@...).
Settings → Organization → Email picks the default sender and the default recipients for reports; every send can
override both.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import Organization, ProviderConfig

EMAIL_RX = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_RECIPIENTS = 25
SEND_TIMEOUT_S = 60


class MailError(ValueError):
    """Shown to the user as is."""


def explain(e: Exception) -> str:
    """Turn SMTP errors into something actionable."""
    text = str(e) or type(e).__name__
    if "EOF" in text or type(e).__name__ == "SMTPServerDisconnected":
        return ("The mail server hung up while signing in. With GoDaddy this means the mailbox password is wrong: "
                "re-type it in Settings → Integrations → Email (SMTP) → Edit.")
    if "535" in text or "authentication" in text.lower():
        return f"The mail server refused the username or password ({text[:200]})."
    return f"The mail server refused the email: {text[:300]}"


def email_settings(org: Organization | None) -> dict:
    cfg = dict(((org.publishing if org is not None else None) or {}).get("email") or {})
    return {"default_sender_id": cfg.get("default_sender_id"),
            "report_recipients": [e for e in (cfg.get("report_recipients") or []) if isinstance(e, str)],
            "report_cc": [e for e in (cfg.get("report_cc") or []) if isinstance(e, str)]}


def parse_addresses(value: str | list[str] | None) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,;\s]+", value or "")
    out: list[str] = []
    for raw in items:
        e = str(raw).strip().strip("<>").lower()
        if not e:
            continue
        if not EMAIL_RX.match(e) or len(e) > 254:
            raise MailError(f"“{raw}” isn't a valid email address.")
        if e not in out:
            out.append(e)
    if len(out) > MAX_RECIPIENTS:
        raise MailError(f"Send to at most {MAX_RECIPIENTS} addresses at a time.")
    return out


def sender_out(c: ProviderConfig, default_id: str | None) -> dict:
    sec = decrypt_json(c.secrets_enc) if c.secrets_enc else {}
    return {"id": str(c.id), "name": c.name, "from_email": sec.get("from_email") or sec.get("username"),
            "from_name": sec.get("from_name") or None, "enabled": c.enabled, "default": str(c.id) == str(default_id),
            "last_test_ok": c.last_test_ok}


async def senders(session: AsyncSession) -> list[ProviderConfig]:
    return list((await session.scalars(select(ProviderConfig).where(ProviderConfig.kind == "smtp")
                                       .order_by(ProviderConfig.created_at))).all())


async def default_sender(session: AsyncSession, org: Organization | None = None) -> ProviderConfig | None:
    rows = [c for c in await senders(session) if c.enabled]
    if not rows:
        return None
    want = email_settings(org)["default_sender_id"] if org is not None else None
    return next((c for c in rows if str(c.id) == str(want)), rows[0])


async def pick_sender(session: AsyncSession, org: Organization, sender_id: str | None) -> ProviderConfig:
    if sender_id:
        try:
            sid = uuid.UUID(str(sender_id))
        except ValueError:
            raise MailError("Choose a sender.")
        c = await session.get(ProviderConfig, sid)
        if c is None or c.kind != "smtp":
            raise MailError("That sender doesn't exist.")
        if not c.enabled:
            raise MailError(f"The sender “{c.name}” is turned off in Settings → Integrations.")
        return c
    c = await default_sender(session, org)
    if c is None:
        raise MailError("Set up Email (SMTP) in Settings → Integrations first, for example your GoDaddy mailbox.")
    return c


async def send_file(session: AsyncSession, org: Organization, *, sender_id: str | None, to, cc, subject: str,
                    message: str, filename: str, data: bytes, mime: str, sent_by: str) -> str:
    from app.services.notify import send_email

    cfg = await pick_sender(session, org, sender_id)
    to_list, cc_list = parse_addresses(to), parse_addresses(cc)
    if not to_list:
        raise MailError("Add at least one recipient.")
    subject = (subject or filename).strip()[:200]
    note = (message or "").strip()[:5000]
    body = (note + "\n\n" if note else "") + f"Attached: {filename}\nSent by {sent_by} from HRP Digital Marketing Studio ({org.name})."
    html = ((f'<p style="white-space:pre-line;color:#334155">{escape(note)}</p>' if note else "")
            + f'<p>Attached: <b>{escape(filename)}</b></p>'
            + f'<p style="color:#64748b;font-size:12px">Sent by {escape(sent_by)} from HRP Digital Marketing Studio ({escape(org.name)}).</p>')
    try:
        await asyncio.wait_for(send_email(cfg, to_list, subject, body, html=html, cc=cc_list or None,
                                          attachments=[(filename, data, mime)]), timeout=SEND_TIMEOUT_S)
    except TimeoutError:
        raise MailError("The mail server didn't answer in time. Check the Email (SMTP) settings and try again.")
    except Exception as e:  # noqa: BLE001
        raise MailError(explain(e))
    return f"Sent to {', '.join(to_list + cc_list)} from {decrypt_json(cfg.secrets_enc).get('from_email') or cfg.name}."
