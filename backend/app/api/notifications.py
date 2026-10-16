import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.core.crypto import decrypt_json
from app.core.deps import Context, get_context
from app.models import Notification, NotificationPref, ProviderConfig
from app.services.notify import DEFAULT_EXTERNAL, EVENTS, send_email, send_telegram

router = APIRouter(prefix="/notifications", tags=["notifications"])
CHANNELS = ("inapp", "email", "telegram")


def _org(ctx: Context) -> uuid.UUID:
    return ctx.org_id


@router.get("")
async def list_notifications(unread: bool = False, limit: int = 30, ctx: Context = Depends(get_context)):
    q = select(Notification).where(Notification.user_id == ctx.user.id, Notification.organization_id == _org(ctx))
    if unread:
        q = q.where(Notification.read_at.is_(None))
    rows = (await ctx.session.scalars(q.order_by(Notification.created_at.desc()).limit(min(limit, 100)))).all()
    count = await ctx.session.scalar(
        select(func.count()).where(Notification.user_id == ctx.user.id, Notification.read_at.is_(None))
    )
    return {
        "unread": count or 0,
        "items": [{"id": n.id, "event": n.event, "title": n.title, "body": n.body, "link": n.link,
                   "read": n.read_at is not None, "created_at": n.created_at} for n in rows],
    }


class ReadIn(BaseModel):
    ids: list[uuid.UUID] | None = None


@router.post("/read", status_code=204)
async def mark_read(body: ReadIn, ctx: Context = Depends(get_context)):
    q = update(Notification).where(Notification.user_id == ctx.user.id, Notification.read_at.is_(None))
    if body.ids:
        q = q.where(Notification.id.in_(body.ids))
    await ctx.session.execute(q.values(read_at=datetime.now(UTC)))
    await ctx.session.commit()


async def _channel_status(ctx: Context) -> dict:
    smtp = await ctx.session.scalar(select(ProviderConfig).where(ProviderConfig.kind == "smtp", ProviderConfig.enabled.is_(True)))
    tg = await ctx.session.scalar(select(ProviderConfig).where(ProviderConfig.kind == "telegram", ProviderConfig.enabled.is_(True)))
    return {
        "email": smtp is not None,
        "telegram": tg is not None,
        "telegram_bot": decrypt_json(tg.secrets_enc).get("bot_username") if tg else None,
    }


@router.get("/prefs")
async def get_prefs(ctx: Context = Depends(get_context)):
    prefs = {p.channel: p for p in (await ctx.session.scalars(
        select(NotificationPref).where(NotificationPref.user_id == ctx.user.id))).all()}
    out = {}
    for ch in CHANNELS:
        p = prefs.get(ch)
        out[ch] = {
            "enabled": p.enabled if p else ch != "telegram",
            "events": (p.events if p and p.events else (list(EVENTS) if ch == "inapp" else DEFAULT_EXTERNAL)),
            "target": p.target if p else (ctx.user.email if ch == "email" else None),
            "verified": p.verified if p else ch == "inapp",
            "quiet_start": p.quiet_start if p else None,
            "quiet_end": p.quiet_end if p else None,
        }
    return {"events": EVENTS, "channels": out, "available": await _channel_status(ctx)}


class PrefIn(BaseModel):
    enabled: bool = True
    events: list[str] = []
    target: str | None = Field(None, max_length=254)
    quiet_start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    quiet_end: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


async def _pref(ctx: Context, channel: str) -> NotificationPref:
    p = await ctx.session.scalar(select(NotificationPref).where(
        NotificationPref.user_id == ctx.user.id, NotificationPref.channel == channel))
    if p is None:
        p = NotificationPref(organization_id=_org(ctx), user_id=ctx.user.id, channel=channel,
                             verified=channel == "inapp", target=ctx.user.email if channel == "email" else None)
        ctx.session.add(p)
    return p


@router.put("/prefs/{channel}")
async def set_pref(channel: str, body: PrefIn, ctx: Context = Depends(get_context)):
    if channel not in CHANNELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown channel.")
    unknown = set(body.events) - set(EVENTS)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown event: {sorted(unknown)[0]}")
    p = await _pref(ctx, channel)
    p.enabled, p.events, p.quiet_start, p.quiet_end = body.enabled, body.events, body.quiet_start, body.quiet_end
    if channel == "email" and body.target:
        p.target, p.verified = body.target.strip(), True
    await ctx.session.commit()
    return await get_prefs(ctx)


@router.post("/telegram/link")
async def telegram_link(ctx: Context = Depends(get_context)):
    st = await _channel_status(ctx)
    if not st["telegram"] or not st["telegram_bot"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Ask an administrator to add the Telegram bot (token and username) in Settings → Integrations.")
    p = await _pref(ctx, "telegram")
    p.link_code = secrets.token_hex(4)
    p.verified = False
    await ctx.session.commit()
    return {"url": f"https://t.me/{st['telegram_bot']}?start={p.link_code}", "code": p.link_code}


class TestIn(BaseModel):
    channel: str


@router.post("/test")
async def test_channel(body: TestIn, ctx: Context = Depends(get_context)):
    p = await ctx.session.scalar(select(NotificationPref).where(
        NotificationPref.user_id == ctx.user.id, NotificationPref.channel == body.channel))
    title, text = "Test notification", "If you can read this, notifications reach you here."
    try:
        if body.channel == "email":
            cfg = await ctx.session.scalar(select(ProviderConfig).where(ProviderConfig.kind == "smtp"))
            if not cfg:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email (SMTP) isn't set up in Integrations yet.")
            await send_email(cfg, (p.target if p and p.target else ctx.user.email), title, text, "/settings/notifications")
        elif body.channel == "telegram":
            cfg = await ctx.session.scalar(select(ProviderConfig).where(ProviderConfig.kind == "telegram"))
            if not cfg or not p or not p.verified:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Link your Telegram first.")
            await send_telegram(cfg, p.target, title, text)
        else:
            ctx.session.add(Notification(organization_id=_org(ctx), user_id=ctx.user.id, event="daily_digest",
                                         title=title, body=text, link="/settings/notifications"))
            await ctx.session.commit()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Sending failed: {e}")
    return {"ok": True}
