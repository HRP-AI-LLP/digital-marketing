"""Outgoing webhooks: tell other tools (Zapier, Make, n8n...) when something happens in the studio.

Other modules call `emit(org_id, event, data)` next to their notifications. It never raises: a broken
webhook set-up must never break publishing or any other work. Each matching webhook gets a
"webhook.deliver" job (see app/modules/webhooks.py) that POSTs

    {"id": <event id>, "event": "post.published", "created_at": "...",
     "organization": {"id": ..., "name": ...}, "data": {...}}

with headers X-HRP-Event, X-HRP-Delivery (one per attempt) and X-HRP-Signature: sha256=<HMAC-SHA256 of the
raw body with the webhook's secret>. Failed attempts are retried with backoff (5 attempts in all); after
20 failures in a row the webhook turns itself off and the admins are told.

Addresses must be https (http://localhost only when WEBHOOKS_ALLOW_LOCALHOST is on). Private, internal and
cloud-metadata addresses are refused, checked again at every delivery (the name is resolved each time).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json

log = logging.getLogger(__name__)

EVENTS: dict[str, str] = {
    "post.published": "A post was published",
    "post.failed": "A post couldn't be published",
    "post.needs_approval": "A post is waiting for approval",
    "post.approved": "A post was approved",
    "post.rejected": "A post was rejected",
    "inbox.new_urgent": "Urgent or negative comments arrived in the inbox",
    "listening.spike": "Listening found a sudden rise in mentions",
    "report.ready": "A marketing report is ready",
    "article.ready": "An SEO article is ready",
    "siteaudit.finished": "A site audit finished",
    "test.ping": "Test message (sent when you press Send test)",
}
SUBSCRIBABLE = [e for e in EVENTS if e != "test.ping"]

TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [60, 300, 1800, 7200]  # wait after attempt 1, 2, 3, 4
DISABLE_AFTER = 20
KEEP_DAYS = 30
USER_AGENT = "HRP-Digital-Marketing-Studio-Webhooks/1.0"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeUrl(ValueError):
    """The address isn't allowed (not https, or it points inside a private network)."""


# ------------------------------------------------------------------ secrets and signatures
def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def encrypt_secret(secret: str) -> str | None:
    return encrypt_json({"secret": secret})


def decrypt_secret(enc: str | None) -> str:
    return str(decrypt_json(enc).get("secret") or "")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str) -> bool:
    """What a receiver does: recompute the HMAC of the raw body and compare in constant time."""
    return hmac.compare_digest(sign(secret, body), header or "")


# ------------------------------------------------------------------ address safety (SSRF)
def _is_local_name(host: str) -> bool:
    return host in LOCAL_HOSTS or host.endswith(".localhost")


def check_url_shape(url: str) -> tuple[str, str, int]:
    """Validate scheme/host without touching the network. Returns (scheme, host, port)."""
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeUrl("That doesn't look like a web address.")
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if scheme not in ("https", "http") or not host:
        raise UnsafeUrl("Use a full web address starting with https://")
    if parts.username or parts.password:
        raise UnsafeUrl("Don't put a user name or password in the address.")
    local = _is_local_name(host)
    if local and not get_settings().webhooks_allow_localhost:
        raise UnsafeUrl("Addresses on this computer (localhost) can't be used.")
    if scheme == "http" and not local:
        raise UnsafeUrl("The address must start with https:// (http:// is only allowed for localhost testing).")
    return scheme, host, port or (443 if scheme == "https" else 80)


async def resolve(host: str, port: int) -> list[str]:
    """IP addresses for `host` (tests replace this)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=0)
    return sorted({i[4][0] for i in infos})


def _ip_blocked(ip: str, allow_loopback: bool) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_loopback:
        return not allow_loopback
    # Anything that isn't a public internet address: 10/8, 172.16/12, 192.168/16, 169.254/16 (cloud metadata),
    # 100.64/10, fc00::/7, fe80::/10, multicast, reserved, unspecified...
    return not addr.is_global or addr.is_multicast


async def check_url(url: str) -> None:
    """Raise UnsafeUrl unless `url` is an allowed public https address (or allowed localhost)."""
    _, host, port = check_url_shape(url)
    local = _is_local_name(host)
    try:
        ips = [host] if _looks_like_ip(host) else await resolve(host, port)
    except (OSError, UnicodeError):
        raise UnsafeUrl(f"Couldn't find the server {host}. Check the address.")
    if not ips:
        raise UnsafeUrl(f"Couldn't find the server {host}. Check the address.")
    for ip in ips:
        if _ip_blocked(ip, allow_loopback=local and get_settings().webhooks_allow_localhost):
            raise UnsafeUrl("That address points inside a private network, which isn't allowed.")


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ HTTP
def client() -> httpx.AsyncClient:
    """Tests replace this with an httpx.MockTransport client."""
    return httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False,
                             headers={"User-Agent": USER_AGENT})


# ------------------------------------------------------------------ payloads
def jsonable(data) -> dict:
    """Make `data` JSON-safe (UUIDs, datetimes...)."""
    try:
        out = json.loads(json.dumps(data or {}, default=_default))
        return out if isinstance(out, dict) else {"value": out}
    except (TypeError, ValueError):
        return {}


def _default(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def app_link(path: str | None) -> str:
    return get_settings().sign_in_url + (path or "/")


def post_data(post, **extra) -> dict:
    """The fields of a post that automation tools usually need."""
    data = {
        "id": post.id, "status": post.status, "platform": post.platform, "post_type": post.post_type,
        "title": post.title, "text": post.text, "hashtags": list(post.hashtags or []), "link_url": post.link_url,
        "scheduled_at": post.scheduled_at, "published_at": post.published_at, "external_url": post.external_url,
        "social_account_id": post.social_account_id, "error": post.error, "app_url": app_link(f"/posts/{post.id}"),
    }
    data.update(extra)
    return jsonable(data)


def body_bytes(body: dict) -> bytes:
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


# ------------------------------------------------------------------ emit
async def _enqueue_for(session: AsyncSession, org_id: uuid.UUID, event: str, data: dict) -> int:
    from app.models import Webhook
    from app.services.jobs import enqueue

    hooks = (await session.scalars(select(Webhook).where(Webhook.organization_id == org_id,
                                                         Webhook.active.is_(True)))).all()
    hooks = [h for h in hooks if event in (h.events or [])]
    if not hooks:
        return 0
    event_id = str(uuid.uuid4())
    created = datetime.now(UTC).isoformat()
    clean = jsonable(data)
    for h in hooks:
        await enqueue(session, "webhook.deliver", org_id, {
            "webhook_id": str(h.id), "event_id": event_id, "event": event, "created_at": created, "data": clean,
            "attempt": 1,
        }, priority=4, max_attempts=1)
    return len(hooks)


async def emit(org_id: uuid.UUID | None, event: str, data: dict | Callable[[], dict] | None = None, *,
               session: AsyncSession | None = None) -> int:
    """Queue `event` for every active webhook of the organization that subscribed to it. Never raises.

    Without `session` it uses (and commits) its own transaction — call it after your own commit.
    With `session` the deliveries join the caller's transaction (inside a savepoint), so they only go out
    if the caller commits. `data` may be a function returning the dict (errors building it are caught too).
    """
    if not org_id or event not in EVENTS:
        return 0
    try:
        if callable(data):
            data = data()
        if session is not None:
            async with session.begin_nested():
                return await _enqueue_for(session, org_id, event, data or {})
        from app.core.db import SessionLocal, set_tenant

        async with SessionLocal() as s:
            set_tenant(s, org_id)
            n = await _enqueue_for(s, org_id, event, data or {})
            if n:
                await s.commit()
            return n
    except Exception:  # noqa: BLE001 — webhooks must never break the caller
        log.exception("webhook emit %s failed", event)
        return 0


# ------------------------------------------------------------------ delivery
def retry_delay(attempt: int) -> int:
    """Seconds to wait after failed attempt number `attempt` (1-based)."""
    return BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS)) - 1]


def _retryable(status: int | None) -> bool:
    return status is None or status in (408, 409, 425, 429) or status >= 500


async def deliver(session: AsyncSession, payload: dict, *, force: bool = False) -> dict:
    """One delivery attempt. Records a WebhookDelivery, updates the webhook, schedules a retry or turns the
    webhook off. Returns a summary. The caller commits."""
    from app.models import Organization, Webhook, WebhookDelivery
    from app.services.jobs import enqueue

    try:
        hook = await session.get(Webhook, uuid.UUID(str(payload.get("webhook_id"))))
    except ValueError:
        hook = None
    if hook is None:
        return {"skipped": "The webhook was removed."}
    if not hook.active and not force:
        return {"skipped": "The webhook is turned off."}
    attempt = int(payload.get("attempt") or 1)
    org = await session.get(Organization, hook.organization_id)
    event = str(payload.get("event") or "test.ping")
    body = {
        "id": str(payload.get("event_id") or uuid.uuid4()), "event": event,
        "created_at": payload.get("created_at") or datetime.now(UTC).isoformat(),
        "organization": {"id": str(hook.organization_id), "name": org.name if org else None},
        "data": payload.get("data") or {},
    }
    raw = body_bytes(body)
    delivery = WebhookDelivery(id=uuid.uuid4(), organization_id=hook.organization_id, webhook_id=hook.id, event=event,
                               event_id=uuid.UUID(body["id"]), payload=body, attempt=attempt,
                               redelivery=bool(payload.get("redelivery")), created_at=datetime.now(UTC))
    status_code: int | None = None
    error: str | None = None
    retry = True
    started = time.monotonic()
    try:
        await check_url(hook.url)
        headers = {
            "Content-Type": "application/json", "User-Agent": USER_AGENT, "X-HRP-Event": event,
            "X-HRP-Delivery": str(delivery.id), "X-HRP-Signature": sign(decrypt_secret(hook.secret_enc), raw),
        }
        async with client() as c:
            r = await c.post(hook.url, content=raw, headers=headers, timeout=TIMEOUT_SECONDS)
        status_code = r.status_code
        delivery.response_body = (r.text or "")[:2000]
        if not 200 <= r.status_code < 300:
            error = f"The server answered {r.status_code}."
            if 300 <= r.status_code < 400:
                error += " Redirects aren't followed; use the final address."
            retry = _retryable(r.status_code)
    except UnsafeUrl as e:
        error, retry = str(e), False
    except httpx.TimeoutException:
        error = f"No answer within {TIMEOUT_SECONDS:g} seconds."
    except httpx.HTTPError as e:
        from app.services.jobs import WaitForNetwork, offline_now

        if await offline_now(e):
            raise WaitForNetwork() from e
        error = f"Couldn't connect: {type(e).__name__}."
    delivery.duration_ms = int((time.monotonic() - started) * 1000)
    delivery.status_code = status_code
    delivery.ok = error is None
    delivery.error = error
    session.add(delivery)

    hook.last_delivery_at = datetime.now(UTC)
    hook.last_status_code = status_code
    result = {"delivery_id": str(delivery.id), "ok": delivery.ok, "status_code": status_code, "attempt": attempt}
    if delivery.ok:
        hook.last_status, hook.failure_count = "ok", 0
        return result
    hook.last_status = "failed"
    hook.failure_count = int(hook.failure_count or 0) + 1
    result["error"] = error
    if hook.active and hook.failure_count >= DISABLE_AFTER:
        hook.active = False
        hook.disabled_reason = (f"Turned off after {DISABLE_AFTER} failed deliveries in a row. Last problem: {error} "
                                "Fix the receiving side, then turn it back on.")
        result["disabled"] = True
        return result
    if retry and attempt < MAX_ATTEMPTS and event != "test.ping" and hook.active:
        delay = retry_delay(attempt)
        await enqueue(session, "webhook.deliver", hook.organization_id, {**payload, "attempt": attempt + 1},
                      run_after=datetime.now(UTC) + timedelta(seconds=delay), priority=5, max_attempts=1)
        result["retry_in"] = delay
    return result


async def notify_disabled(org_id: uuid.UUID, hook_name: str, reason: str | None) -> None:
    try:
        from app.services.notify import notify_org

        await notify_org(org_id, "webhook_disabled", f"Webhook “{hook_name[:80]}” was turned off", reason,
                         link="/settings/connections", permission="connections.manage")
    except Exception:  # noqa: BLE001
        log.exception("webhook disabled notification failed")


def success_rate(ok: int, total: int) -> float | None:
    return round(100.0 * ok / total, 1) if total else None
