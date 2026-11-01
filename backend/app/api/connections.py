"""Connections: outgoing webhooks and personal API tokens for automation tools (Zapier, Make, n8n).

Webhook secrets and API tokens are shown once, when created (or when a secret is replaced). Every change is
written to the activity log. API tokens themselves can't call the /connections/tokens endpoints.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from app.core.deps import Context, get_context, require
from app.core.permissions import PERMISSIONS
from app.models import ApiToken, User, Webhook, WebhookDelivery
from app.services import api_tokens, audit
from app.services import webhooks as wh
from app.services.jobs import enqueue

router = APIRouter(prefix="/connections", tags=["connections"])
PERM_INFO = {code: (group, desc) for code, group, desc in PERMISSIONS}


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


# ====================================================================== webhooks
class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2000)
    events: list[str] = Field(default_factory=list, max_length=20)
    active: bool = True


class WebhookPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=2000)
    events: list[str] | None = Field(default=None, max_length=20)
    active: bool | None = None


def _events(events: list[str]) -> list[str]:
    unknown = [e for e in events if e not in wh.SUBSCRIBABLE]
    if unknown:
        raise _bad(f"Unknown event: {unknown[0]}")
    out = [e for e in wh.SUBSCRIBABLE if e in set(events)]
    if not out:
        raise _bad("Choose at least one event.")
    return out


async def _check(url: str) -> str:
    url = url.strip()
    try:
        await wh.check_url(url)
    except wh.UnsafeUrl as e:
        raise _bad(str(e))
    return url


async def _stats(ctx: Context, ids: list[uuid.UUID]) -> dict:
    if not ids:
        return {}
    since = datetime.now(UTC) - timedelta(days=wh.KEEP_DAYS)
    rows = await ctx.session.execute(
        select(WebhookDelivery.webhook_id, func.count(), func.sum(case((WebhookDelivery.ok.is_(True), 1), else_=0)))
        .where(WebhookDelivery.webhook_id.in_(ids), WebhookDelivery.created_at >= since)
        .group_by(WebhookDelivery.webhook_id))
    return {wid: (int(total), int(ok or 0)) for wid, total, ok in rows.all()}


def _hook_out(h: Webhook, stats: tuple[int, int] | None = None) -> dict:
    total, ok = stats or (0, 0)
    return {
        "id": h.id, "name": h.name, "url": h.url, "events": h.events or [], "active": h.active,
        "last_status": h.last_status, "last_status_code": h.last_status_code, "last_delivery_at": h.last_delivery_at,
        "failure_count": h.failure_count, "disabled_reason": h.disabled_reason, "created_at": h.created_at,
        "deliveries_30d": total, "success_rate": wh.success_rate(ok, total),
    }


async def _hook(ctx: Context, hid: uuid.UUID) -> Webhook:
    h = await ctx.session.get(Webhook, hid)
    if h is None:
        raise _bad("That webhook doesn't exist.", status.HTTP_404_NOT_FOUND)
    return h


@router.get("/events")
async def list_events(ctx: Context = Depends(require("connections.view"))):
    return {"items": [{"event": e, "label": wh.EVENTS[e]} for e in wh.SUBSCRIBABLE]}


@router.get("/webhooks")
async def list_webhooks(ctx: Context = Depends(require("connections.view"))):
    hooks = (await ctx.session.scalars(select(Webhook).order_by(Webhook.created_at))).all()
    stats = await _stats(ctx, [h.id for h in hooks])
    return {"items": [_hook_out(h, stats.get(h.id)) for h in hooks]}


@router.post("/webhooks", status_code=201)
async def create_webhook(body: WebhookIn, ctx: Context = Depends(require("connections.manage"))):
    url = await _check(body.url)
    secret = wh.new_secret()
    h = Webhook(id=uuid.uuid4(), organization_id=ctx.org_id, name=body.name.strip(), url=url,
                events=_events(body.events), secret_enc=wh.encrypt_secret(secret), active=body.active,
                failure_count=0, created_by=ctx.user.id)
    ctx.session.add(h)
    audit.record(ctx.session, action="webhook.created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="webhook", entity_id=h.id, summary=f"Added the webhook “{h.name}”",
                 details={"url": url, "events": h.events}, ip=ctx.ip)
    await ctx.session.commit()
    return {**_hook_out(h), "secret": secret}


@router.patch("/webhooks/{hid}")
async def update_webhook(hid: uuid.UUID, body: WebhookPatch, ctx: Context = Depends(require("connections.manage"))):
    h = await _hook(ctx, hid)
    changes: dict = {}
    if body.name is not None and body.name.strip() != h.name:
        changes["name"] = h.name = body.name.strip()
    if body.url is not None and body.url.strip() != h.url:
        changes["url"] = h.url = await _check(body.url)
    if body.events is not None:
        ev = _events(body.events)
        if ev != h.events:
            changes["events"] = h.events = ev
    if body.active is not None and body.active != h.active:
        if body.active:
            await _check(h.url)
            h.failure_count, h.disabled_reason = 0, None
        changes["active"] = h.active = body.active
    if changes:
        audit.record(ctx.session, action="webhook.updated", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="webhook", entity_id=h.id,
                     summary=f"Changed the webhook “{h.name}” ({', '.join(changes)})", details=changes, ip=ctx.ip)
    await ctx.session.commit()
    stats = await _stats(ctx, [h.id])
    return _hook_out(h, stats.get(h.id))


@router.post("/webhooks/{hid}/rotate-secret")
async def rotate_secret(hid: uuid.UUID, ctx: Context = Depends(require("connections.manage"))):
    h = await _hook(ctx, hid)
    secret = wh.new_secret()
    h.secret_enc = wh.encrypt_secret(secret)
    audit.record(ctx.session, action="webhook.secret_rotated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="webhook", entity_id=h.id, summary=f"Replaced the signing secret of “{h.name}”", ip=ctx.ip)
    await ctx.session.commit()
    return {"secret": secret}


@router.delete("/webhooks/{hid}", status_code=204)
async def delete_webhook(hid: uuid.UUID, ctx: Context = Depends(require("connections.manage"))):
    h = await _hook(ctx, hid)
    audit.record(ctx.session, action="webhook.deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="webhook", entity_id=h.id, summary=f"Removed the webhook “{h.name}”",
                 details={"url": h.url}, ip=ctx.ip)
    await ctx.session.delete(h)
    await ctx.session.commit()


@router.post("/webhooks/{hid}/test")
async def test_webhook(hid: uuid.UUID, ctx: Context = Depends(require("connections.manage"))):
    h = await _hook(ctx, hid)
    await _check(h.url)
    job = await enqueue(ctx.session, "webhook.deliver", ctx.org_id, {
        "webhook_id": str(h.id), "event_id": str(uuid.uuid4()), "event": "test.ping",
        "created_at": datetime.now(UTC).isoformat(), "attempt": 1, "force": True,
        "data": {"message": "Test message from HRP Digital Marketing Studio. If you can see this, the webhook works.",
                 "webhook": {"id": str(h.id), "name": h.name}, "sent_by": ctx.user.email},
    }, priority=1, max_attempts=1, created_by=ctx.user.id)
    audit.record(ctx.session, action="webhook.tested", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="webhook", entity_id=h.id, summary=f"Sent a test message to “{h.name}”", ip=ctx.ip)
    await ctx.session.commit()
    return {"job_id": job.id}


def _delivery_out(d: WebhookDelivery, full: bool = False) -> dict:
    out = {"id": d.id, "event": d.event, "event_id": d.event_id, "status_code": d.status_code, "ok": d.ok,
           "attempt": d.attempt, "duration_ms": d.duration_ms, "error": d.error, "redelivery": d.redelivery,
           "created_at": d.created_at}
    if full:
        out["payload"] = d.payload
        out["response_body"] = d.response_body
    return out


@router.get("/webhooks/{hid}/deliveries")
async def list_deliveries(hid: uuid.UUID, limit: int = 50, ctx: Context = Depends(require("connections.view"))):
    await _hook(ctx, hid)
    rows = (await ctx.session.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hid)
                                      .order_by(WebhookDelivery.created_at.desc()).limit(max(1, min(limit, 200))))).all()
    return {"items": [_delivery_out(d, full=True) for d in rows]}


@router.post("/webhooks/{hid}/deliveries/{did}/redeliver")
async def redeliver(hid: uuid.UUID, did: uuid.UUID, ctx: Context = Depends(require("connections.manage"))):
    h = await _hook(ctx, hid)
    d = await ctx.session.get(WebhookDelivery, did)
    if d is None or d.webhook_id != h.id:
        raise _bad("That delivery doesn't exist any more.", status.HTTP_404_NOT_FOUND)
    await _check(h.url)
    p = d.payload or {}
    job = await enqueue(ctx.session, "webhook.deliver", ctx.org_id, {
        "webhook_id": str(h.id), "event_id": str(d.event_id), "event": d.event, "created_at": p.get("created_at"),
        "data": p.get("data") or {}, "attempt": 1, "force": True, "redelivery": True,
    }, priority=1, max_attempts=1, created_by=ctx.user.id)
    audit.record(ctx.session, action="webhook.redelivered", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="webhook", entity_id=h.id, summary=f"Sent “{d.event}” again to “{h.name}”", ip=ctx.ip)
    await ctx.session.commit()
    return {"job_id": job.id}


# ====================================================================== API tokens
class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=100)
    expires_in_days: Literal[7, 30, 90, 180, 365] | None = 90


def _grantable(ctx: Context) -> list[str]:
    return [c for c, _, _ in PERMISSIONS if c in ctx.permissions and c not in api_tokens.NOT_GRANTABLE]


def _token_out(t: ApiToken, names: dict | None = None) -> dict:
    now = datetime.now(UTC)
    state = ("revoked" if t.revoked_at else "expired" if t.expires_at and t.expires_at <= now else "active")
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "hint": f"hrp_{t.prefix}_…", "scopes": t.scopes or [],
            "user_id": t.user_id, "created_by": (names or {}).get(t.user_id), "created_at": t.created_at,
            "last_used_at": t.last_used_at, "expires_at": t.expires_at, "revoked_at": t.revoked_at, "state": state}


def _token_reader(ctx: Context = Depends(get_context)) -> Context:
    if ctx.org is None:
        raise _bad("Select an organization first.")
    if not ({"connections.view", "apitoken.create", "connections.manage"} & ctx.permissions):
        raise _bad("You need the 'apitoken.create' permission for this.", status.HTTP_403_FORBIDDEN)
    return ctx


@router.get("/scopes")
async def list_scopes(ctx: Context = Depends(require("apitoken.create"))):
    return {"items": [{"code": c, "group": PERM_INFO[c][0], "description": PERM_INFO[c][1]} for c in _grantable(ctx)]}


@router.get("/tokens")
async def list_tokens(ctx: Context = Depends(_token_reader)):
    q = select(ApiToken).order_by(ApiToken.created_at.desc())
    if "connections.manage" not in ctx.permissions:
        q = q.where(ApiToken.user_id == ctx.user.id)
    rows = (await ctx.session.scalars(q)).all()
    ids = {t.user_id for t in rows}
    names = {u.id: u.email for u in (await ctx.session.scalars(select(User).where(User.id.in_(ids)))).all()} if ids else {}
    return {"items": [_token_out(t, names) for t in rows]}


@router.post("/tokens", status_code=201)
async def create_token(body: TokenIn, ctx: Context = Depends(require("apitoken.create"))):
    allowed = set(_grantable(ctx))
    bad = [s for s in body.scopes if s not in allowed]
    if bad:
        raise _bad(f"You can't give a token the '{bad[0]}' permission (you don't have it, or tokens can't use it).")
    scopes = [c for c, _, _ in PERMISSIONS if c in set(body.scopes)]
    token, prefix, digest = api_tokens.generate()
    t = ApiToken(id=uuid.uuid4(), organization_id=ctx.org_id, name=body.name.strip(), token_hash=digest, prefix=prefix,
                 scopes=scopes, user_id=ctx.user.id,
                 expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None)
    ctx.session.add(t)
    audit.record(ctx.session, action="api_token.created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="api_token", entity_id=t.id, summary=f"Created the API token “{t.name}” (hrp_{prefix}_…)",
                 details={"scopes": scopes, "expires_at": t.expires_at.isoformat() if t.expires_at else None}, ip=ctx.ip)
    await ctx.session.commit()
    return {**_token_out(t, {ctx.user.id: ctx.user.email}), "token": token}


@router.delete("/tokens/{tid}", status_code=204)
async def revoke_token(tid: uuid.UUID, ctx: Context = Depends(_token_reader)):
    t = await ctx.session.get(ApiToken, tid)
    if t is None or (t.user_id != ctx.user.id and "connections.manage" not in ctx.permissions):
        raise _bad("That API token doesn't exist.", status.HTTP_404_NOT_FOUND)
    if t.revoked_at is None:
        t.revoked_at, t.revoked_by = datetime.now(UTC), ctx.user.id
        audit.record(ctx.session, action="api_token.revoked", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="api_token", entity_id=t.id, summary=f"Revoked the API token “{t.name}” (hrp_{t.prefix}_…)",
                     ip=ctx.ip)
        await ctx.session.commit()
