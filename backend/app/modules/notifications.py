"""Worker side of notifications: Telegram polling (account linking and approve/reject buttons)."""

import logging
import uuid

from sqlalchemy import select

from app.core.crypto import decrypt_json
from app.core.db import SessionLocal, platform_session, set_tenant
from app.core.deps import permission_codes
from app.models import Membership, NotificationPref, ProviderConfig, User
from app.services.jobs import periodic
from app.services.notify import telegram_api

log = logging.getLogger(__name__)


async def _act_in_org(org_id, chat_id: str, data: str) -> str | None:
    """Run a button action for the user linked to chat_id in one organization.
    Returns None when this chat isn't linked there or the post isn't in this organization."""
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        pref = await s.scalar(select(NotificationPref).where(
            NotificationPref.channel == "telegram", NotificationPref.target == chat_id, NotificationPref.verified.is_(True)))
        if pref is None:
            return None
        user = await s.get(User, pref.user_id)
        action, _, ident = data.partition(":")
        needed = {"ap": "content.approve", "rj": "content.approve", "rg": "content.create"}.get(action)
        if needed is None:
            return "That button isn't supported."
        membership = await s.scalar(select(Membership).where(
            Membership.user_id == pref.user_id, Membership.organization_id == org_id))
        if user is None or not user.is_active or membership is None or not membership.is_active:
            return "Your account no longer has access to this organization."
        if needed not in await permission_codes(s, membership.id):
            return "You don't have permission to do that any more."
        from app.models import Post
        from app.services import content_actions as ca

        try:
            post_uuid = uuid.UUID(ident)
        except ValueError:
            return "That post link is invalid."
        if await s.scalar(select(Post.id).where(Post.id == post_uuid)) is None:
            return None  # the post belongs to another organization using the same bot
        try:
            if action == "ap":
                reply = await ca.approve_by_id(s, ident, user, via="Telegram")
            elif action == "rj":
                reply = await ca.reject_by_id(s, ident, user, "Rejected from Telegram", via="Telegram")
            else:
                reply = await ca.regenerate_by_id(s, ident, user, via="Telegram")
            await s.commit()
            return reply
        except Exception as e:  # noqa: BLE001
            return f"Couldn't do that: {e}"


async def _handle_update(cfgs: list[ProviderConfig], upd: dict) -> None:
    """cfgs: every organization's Telegram config that uses this bot token."""
    cfg = cfgs[0]
    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if text.startswith("/start"):
        code = text.split(maxsplit=1)[1].strip() if " " in text else ""
        linked = False
        for c in cfgs if code else []:
            async with SessionLocal() as s:
                set_tenant(s, c.organization_id)
                p = await s.scalar(select(NotificationPref).where(
                    NotificationPref.channel == "telegram", NotificationPref.link_code == code))
                if p:
                    p.target, p.verified, p.link_code = chat_id, True, None
                    await s.commit()
                    linked = True
                    break
        await telegram_api(cfg, "sendMessage", {
            "chat_id": chat_id,
            "text": "Linked. You'll get notifications here." if linked else
                    "Open Settings → Notifications in the app and press 'Link Telegram' to connect this chat."})
        return

    cb = upd.get("callback_query")
    if not cb:
        return
    data = cb.get("data") or ""
    chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
    reply = None
    for c in cfgs:
        reply = await _act_in_org(c.organization_id, chat_id, data)
        if reply is not None:
            break
    reply = reply or "This chat isn't linked to a user, or the post no longer exists."
    await telegram_api(cfg, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": reply[:190]})
    if cb.get("message"):
        try:
            await telegram_api(cfg, "editMessageReplyMarkup", {
                "chat_id": chat_id, "message_id": cb["message"]["message_id"], "reply_markup": {"inline_keyboard": []}})
            await telegram_api(cfg, "sendMessage", {"chat_id": chat_id, "text": reply})
        except Exception:  # noqa: BLE001
            pass


@periodic(5, name="telegram_poll")
async def telegram_poll() -> None:
    async with platform_session() as s:
        cfgs = (await s.scalars(select(ProviderConfig).where(
            ProviderConfig.kind == "telegram", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at))).all()
    by_token: dict[str, list[ProviderConfig]] = {}
    for c in cfgs:
        token = decrypt_json(c.secrets_enc).get("api_key")
        if token:
            by_token.setdefault(token, []).append(c)
    for group in by_token.values():
        cfg = group[0]
        offset = (cfg.settings or {}).get("update_offset", 0)
        try:
            updates = await telegram_api(cfg, "getUpdates", {"offset": offset, "timeout": 0,
                                                             "allowed_updates": ["message", "callback_query"]})
        except Exception as e:  # noqa: BLE001
            log.debug("telegram poll failed: %s", e)
            continue
        for upd in updates or []:
            offset = max(offset, upd["update_id"] + 1)
            try:
                await _handle_update(group, upd)
            except Exception:  # noqa: BLE001
                log.exception("telegram update failed")
        if updates:
            async with platform_session() as s:
                c = await s.get(ProviderConfig, cfg.id)
                c.settings = {**(c.settings or {}), "update_offset": offset}
                await s.commit()


# ---- connection tests shown on the Integrations page
from app.api.integrations import tester  # noqa: E402


@tester("telegram")
async def _test_telegram(cfg: ProviderConfig) -> str:
    me = await telegram_api(cfg, "getMe", {})
    return f"Connected to bot @{me.get('username')}. Each user links their chat from Settings → Notifications."


@tester("smtp")
async def _test_smtp(cfg: ProviderConfig) -> str:
    import aiosmtplib

    sec = decrypt_json(cfg.secrets_enc)
    port = int(sec.get("port") or 587)
    smtp = aiosmtplib.SMTP(hostname=sec.get("host"), port=port, use_tls=port == 465,
                           start_tls=port != 465 and str(sec.get("use_tls", "true")).lower() != "false", timeout=20)
    await smtp.connect()
    if sec.get("username"):
        await smtp.login(sec.get("username"), sec.get("password") or "")
    await smtp.quit()
    return f"Signed in to {sec.get('host')}:{port}."
