"""Notifications: in-app, email (SMTP) and Telegram (with approve/reject buttons).

Other modules call `notify_org(...)` or `notify_users(...)`. Delivery to email/Telegram follows
each user's preferences (Settings → Notifications). Telegram button presses are handled by the
worker's polling loop (see app/modules/notifications.py).
"""

import logging
import uuid
from datetime import datetime
from email.message import EmailMessage
from html import escape
from zoneinfo import ZoneInfo

import aiosmtplib
import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import decrypt_json
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import (
    Membership, MembershipRole, Notification, NotificationPref, Organization, ProviderConfig, RolePermission, User,
)

log = logging.getLogger(__name__)

EVENTS: dict[str, str] = {
    "post_review": "Posts waiting for approval",
    "post_published": "Post published",
    "post_failed": "Post failed to publish",
    "manual_publish": "Post ready to publish by hand",
    "account_issue": "Social account needs reconnecting",
    "intel_ready": "Company intelligence finished",
    "competitor_change": "Competitor changes",
    "seo_ready": "SEO article ready",
    "daily_digest": "Daily summary",
    "job_failed": "Background task failed",
    "ai_issue": "AI model needs attention (credits, key or removed model)",
}
DEFAULT_EXTERNAL = ["post_review", "post_failed", "account_issue", "manual_publish", "job_failed", "ai_issue"]
URGENT = {"post_failed", "account_issue", "job_failed"}


async def _users_with_permission(session, permission: str | None) -> list[uuid.UUID]:
    q = select(Membership.user_id).where(Membership.is_active.is_(True))
    if permission:
        q = (
            q.join(MembershipRole, MembershipRole.membership_id == Membership.id)
            .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
            .where(RolePermission.permission_code == permission)
        )
    return list(set((await session.scalars(q)).all()))


async def notify_org(
    org_id: uuid.UUID, event: str, title: str, body: str | None = None, *, link: str | None = None,
    permission: str | None = None, actions: list[dict] | None = None,
) -> None:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        users = await _users_with_permission(s, permission)
    await notify_users(org_id, users, event, title, body, link=link, actions=actions)


async def notify_users(
    org_id: uuid.UUID, user_ids: list[uuid.UUID], event: str, title: str, body: str | None = None, *,
    link: str | None = None, actions: list[dict] | None = None,
) -> None:
    """actions: [{"label": "Approve", "data": "ap:<post_id>"}] → Telegram inline buttons."""
    if not user_ids:
        return
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        prefs = (await s.scalars(select(NotificationPref).where(NotificationPref.user_id.in_(user_ids)))).all()
        by_user: dict[uuid.UUID, dict[str, NotificationPref]] = {}
        for p in prefs:
            by_user.setdefault(p.user_id, {})[p.channel] = p
        org = await s.get(Organization, org_id)
        smtp = await s.scalar(select(ProviderConfig).where(ProviderConfig.kind == "smtp", ProviderConfig.enabled.is_(True)))
        tg = await s.scalar(select(ProviderConfig).where(ProviderConfig.kind == "telegram", ProviderConfig.enabled.is_(True)))
        users = {u.id: u for u in (await s.scalars(select(User).where(User.id.in_(user_ids)))).all()}

        external: list[tuple[str, NotificationPref | None, User]] = []
        for uid in user_ids:
            up = by_user.get(uid, {})
            inapp = up.get("inapp")
            if inapp is None or (inapp.enabled and (not inapp.events or event in inapp.events)):
                s.add(Notification(organization_id=org_id, user_id=uid, event=event, title=title, body=body, link=link))
            for ch in ("email", "telegram"):
                p = up.get(ch)
                if p and p.enabled and event in (p.events or DEFAULT_EXTERNAL) and p.target and (ch == "email" or p.verified):
                    if event not in URGENT and _quiet(p, org.timezone if org else "Asia/Kolkata"):
                        continue
                    external.append((ch, p, users.get(uid)))
        await s.commit()

    for ch, p, user in external:
        try:
            if ch == "email" and smtp:
                await send_email(smtp, p.target, title, body or "", link)
            elif ch == "telegram" and tg:
                await send_telegram(tg, p.target, title, body or "", link, actions)
        except Exception:  # noqa: BLE001
            log.exception("notification via %s failed", ch)


def _quiet(p: NotificationPref, tz: str) -> bool:
    if not (p.quiet_start and p.quiet_end):
        return False
    now = datetime.now(ZoneInfo(tz)).strftime("%H:%M")
    a, b = p.quiet_start, p.quiet_end
    return (a <= now < b) if a < b else (now >= a or now < b)


def _abs_link(link: str | None) -> str | None:
    if not link:
        return None
    return link if link.startswith("http") else get_settings().web_base_url + link


async def send_email(cfg: ProviderConfig, to: str, subject: str, body: str, link: str | None = None) -> None:
    sec = decrypt_json(cfg.secrets_enc)
    msg = EmailMessage()
    msg["From"] = sec.get("from_email") or sec.get("username")
    msg["To"] = to
    msg["Subject"] = subject
    url = _abs_link(link)
    msg.set_content(body + (f"\n\nOpen: {url}" if url else ""))
    msg.add_alternative(
        f"""<div style="font-family:Inter,Arial,sans-serif;color:#0f172a;max-width:560px">
<div style="background:#062f59;color:#fff;padding:14px 18px;border-radius:10px 10px 0 0;font-weight:600">
HRP AI Digital Marketing Studio</div>
<div style="border:1px solid #e2e8f0;border-top:0;padding:18px;border-radius:0 0 10px 10px">
<h2 style="margin:0 0 8px;font-size:18px">{escape(subject)}</h2>
<p style="white-space:pre-line;color:#334155">{escape(body)}</p>
{f'<p><a href="{escape(url)}" style="background:linear-gradient(90deg,#1d4ed8,#0891b2);color:#fff;padding:9px 16px;border-radius:8px;text-decoration:none">Open in the app</a></p>' if url else ''}
</div></div>""",
        subtype="html",
    )
    port = int(sec.get("port") or 587)
    use_tls = str(sec.get("use_tls", "true")).lower() != "false"
    await aiosmtplib.send(
        msg, hostname=sec.get("host"), port=port, username=sec.get("username"), password=sec.get("password"),
        start_tls=use_tls and port != 465, use_tls=port == 465, timeout=30,
    )


async def telegram_api(cfg: ProviderConfig, method: str, payload: dict) -> dict:
    token = decrypt_json(cfg.secrets_enc).get("api_key")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        r = await http.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data.get("result")


async def send_telegram(cfg: ProviderConfig, chat_id: str, title: str, body: str, link: str | None = None,
                        actions: list[dict] | None = None) -> None:
    text = f"<b>{escape(title)}</b>\n{escape(body)}"
    url = _abs_link(link)
    buttons = [[{"text": a["label"], "callback_data": a["data"][:64]} for a in (actions or [])]]
    if url and url.startswith("https://"):
        buttons.append([{"text": "Open in the app", "url": url}])
    payload = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
    if any(buttons):
        payload["reply_markup"] = {"inline_keyboard": [row for row in buttons if row]}
    await telegram_api(cfg, "sendMessage", payload)


async def platform_admin_ids() -> list[uuid.UUID]:
    async with platform_session() as s:
        return list((await s.scalars(select(User.id).where(User.is_platform_admin.is_(True)))).all())
