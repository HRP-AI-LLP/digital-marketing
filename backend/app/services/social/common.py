"""Shared pieces for the social platform adapters: errors, tokens, media loading, OAuth app
credentials, redirect URIs and account upserts."""

import asyncio
import base64
import hashlib
import importlib
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json
from app.models import MediaAsset, Post, ProviderConfig, SocialAccount

log = logging.getLogger("social")

API_PREFIX = "/api/v1"


class PublishError(Exception):
    """A platform call failed.

    retryable: worth trying again later (rate limit, server error, timeout).
    reconnect: the account's token is invalid or lacks permission; the user must reconnect it.
    """

    def __init__(self, message: str, retryable: bool = False, reconnect: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.reconnect = reconnect


# ------------------------------------------------------------------ tokens
def account_tokens(account: SocialAccount) -> dict:
    return decrypt_json(account.tokens_enc)


def save_tokens(account: SocialAccount, tokens: dict, expires_at: datetime | None) -> None:
    account.tokens_enc = encrypt_json(tokens)
    account.token_expires_at = expires_at


async def persist_tokens(account: SocialAccount, tokens: dict, expires_at: datetime | None) -> None:
    """Save rotated tokens (X, TikTok, Canva refresh tokens are single-use) in their own committed transaction,
    so a later rollback of the caller's session can't lose them. Also updates the in-memory account."""
    from sqlalchemy import text, update

    from app.core.db import SessionLocal, set_tenant

    save_tokens(account, tokens, expires_at)
    try:
        async with SessionLocal() as s:
            set_tenant(s, account.organization_id)
            await s.execute(text("SET LOCAL lock_timeout = '5s'"))
            await s.execute(update(SocialAccount).where(SocialAccount.id == account.id)
                            .values(tokens_enc=account.tokens_enc, token_expires_at=expires_at))
            await s.commit()
    except Exception as e:  # noqa: BLE001 - the caller's session still carries the new tokens
        log.warning("couldn't save rotated tokens for account %s separately: %s", account.id, type(e).__name__)


def expires_from(seconds) -> datetime | None:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return None
    return datetime.now(UTC) + timedelta(seconds=s) if s > 0 else None


def expiring(account: SocialAccount, within: timedelta = timedelta(days=7)) -> bool:
    exp = account.token_expires_at
    if exp is None:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return exp - datetime.now(UTC) < within


# ------------------------------------------------------------------ HTTP error mapping
def error_message(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return (r.text or r.reason_phrase or f"HTTP {r.status_code}")[:300]
    if isinstance(d, dict):
        e = d.get("error")
        if isinstance(e, dict):
            return str(e.get("error_user_msg") or e.get("message") or e)[:300]
        for k in ("error_description", "message", "detail"):
            if d.get(k):
                return str(d[k])[:300]
        if isinstance(e, str):
            return e[:300]
    return str(d)[:300]


_TOKEN_WORDS = ("invalid_token", "expired_token", "invalidtoken", "expiredtoken", "token has expired",
                "invalid access token", "revoked", "invalid_grant", "unauthorized_client")


def raise_for(r: httpx.Response, what: str) -> None:
    """Raise PublishError for a failed response: 401/403/invalid token -> reconnect,
    429/5xx -> retryable."""
    if r.is_success:
        return
    msg = error_message(r)
    code = r.status_code
    low = msg.lower().replace(" ", "")
    retryable = code == 429 or code >= 500
    reconnect = not retryable and (code in (401, 403) or any(w.replace(" ", "") in low for w in _TOKEN_WORDS))
    raise PublishError(f"{what}: {msg}", retryable=retryable, reconnect=reconnect)


def ok_json(r: httpx.Response, what: str) -> dict:
    raise_for(r, what)
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


async def sleep(seconds: float) -> None:
    """Polling delay (tests replace it with a no-op)."""
    await asyncio.sleep(seconds)


# ------------------------------------------------------------------ OAuth helpers
OAUTH_PROVIDERS: dict[str, dict] = {
    "meta": {"label": "Facebook and Instagram", "app": "meta_app", "module": "meta"},
    "threads": {"label": "Threads", "app": "meta_app", "module": "threads"},
    "linkedin": {"label": "LinkedIn", "app": "linkedin_app", "module": "linkedin"},
    "pinterest": {"label": "Pinterest", "app": "pinterest_app", "module": "pinterest"},
    "google": {"label": "Google", "app": "google_app", "module": "google"},
    "x": {"label": "X", "app": "x_app", "module": "x", "pkce": True},
    "tiktok": {"label": "TikTok", "app": "tiktok_app", "module": "tiktok", "pkce": True},
    "canva": {"label": "Canva", "app": "canva_app", "module": "canva", "pkce": True},
}
PKCE_PROVIDERS = {"google"} | {k for k, v in OAUTH_PROVIDERS.items() if v.get("pkce")}


PLATFORM_MODULE: dict[str, str] = {
    "facebook_page": "meta", "instagram": "meta", "threads": "threads",
    "linkedin_person": "linkedin", "linkedin_org": "linkedin", "pinterest": "pinterest",
    "gbp": "google", "youtube": "google", "google": "google",
    "bluesky": "bluesky", "mastodon": "mastodon", "x": "x", "tiktok": "tiktok", "manual": "manual",
}


def adapter(module: str):
    return importlib.import_module(f"app.services.social.{module}")


def is_manual(account: SocialAccount) -> bool:
    """Accounts published by hand: 'Other', and X accounts added without an API connection."""
    return account.platform == "manual" or account.status == "manual" or (account.external_id or "").startswith("manual:")


def adapter_for_platform(platform: str):
    m = PLATFORM_MODULE.get(platform)
    if m is None:
        raise PublishError(f"Unknown platform: {platform}")
    return adapter(m)


def base_url() -> str:
    s = get_settings()
    return (s.public_base_url or s.api_base_url).rstrip("/")


def redirect_uri(provider: str) -> str:
    """The exact URI to register in the developer app.

    Providers listed in OAUTH_HTTPS_PROVIDERS use the public HTTPS address (PUBLIC_BASE_URL) when one is set;
    the others keep http://localhost, which Google and LinkedIn accept, so their registered URIs don't change.
    """
    s = get_settings()
    wanted = {p.strip() for p in (s.oauth_https_providers or "").split(",") if p.strip()}
    public = (s.public_base_url or "").strip().rstrip("/").removesuffix("/api/v1")
    base = public if public and ("*" in wanted or provider in wanted) else s.api_base_url.rstrip("/")
    return f"{base}{API_PREFIX}/social/oauth/{provider}/callback"


async def app_config(session: AsyncSession, app_provider: str) -> ProviderConfig | None:
    return await session.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.provider == app_provider, ProviderConfig.enabled.is_(True))
        .order_by(ProviderConfig.created_at)
    )


async def app_credentials(session: AsyncSession, oauth_provider: str, variant: str | None = None) -> dict | None:
    """{client_id, client_secret, ...} for the developer app behind an OAuth provider, or None.
    variant="pages" selects LinkedIn's separate company-pages app (Community Management API must be
    the only product on its app), falling back to the main app when none is stored."""
    spec = OAUTH_PROVIDERS.get(oauth_provider)
    if spec is None:
        return None
    cfg = await app_config(session, spec["app"])
    if cfg is None:
        return None
    sec = decrypt_json(cfg.secrets_enc)
    if oauth_provider == "threads":
        # Meta shows a separate "Threads app ID / secret" for the Threads use case. Use them when
        # stored (threads_app_id / threads_app_secret), otherwise the main app's credentials.
        sec = {**sec, "client_id": sec.get("threads_app_id") or sec.get("client_id"),
               "client_secret": sec.get("threads_app_secret") or sec.get("client_secret")}
    if oauth_provider == "linkedin" and variant == "pages" and sec.get("pages_client_id") and sec.get("pages_client_secret"):
        sec = {**sec, "client_id": sec["pages_client_id"], "client_secret": sec["pages_client_secret"], "variant": "pages"}
    if not (sec.get("client_id") and sec.get("client_secret")):
        return None
    return sec


def new_state() -> str:
    return secrets.token_urlsafe(32)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# ------------------------------------------------------------------ accounts
async def upsert_account(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None, platform: str, external_id: str, *,
    name: str, handle: str | None = None, avatar_url: str | None = None, profile_url: str | None = None,
    tokens: dict | None = None, expires_at: datetime | None = None, settings: dict | None = None,
    status: str = "connected", status_message: str | None = None,
) -> SocialAccount:
    acc = await session.scalar(
        select(SocialAccount).where(SocialAccount.platform == platform, SocialAccount.external_id == external_id)
    )
    if acc is None:
        acc = SocialAccount(organization_id=org_id, platform=platform, external_id=external_id, settings={},
                            connected_by=user_id)
        session.add(acc)
    acc.name = (name or external_id)[:200]
    acc.handle = handle[:200] if handle else None
    acc.avatar_url = avatar_url
    acc.profile_url = profile_url
    acc.status = status
    acc.status_message = status_message
    acc.connected_by = user_id or acc.connected_by
    merged = {k: v for k, v in (acc.settings or {}).items() if k not in ("_removed_at", "_issue_notified")}
    for k, v in (settings or {}).items():
        merged.setdefault(k, v)  # keep the user's earlier choices (board, privacy, ...)
    acc.settings = merged
    if tokens is not None:
        save_tokens(acc, tokens, expires_at)
    await session.flush()
    return acc


# ------------------------------------------------------------------ media & text
async def load_media(session: AsyncSession, post: Post) -> list[MediaAsset]:
    ids: list[uuid.UUID] = []
    for i in post.media_ids or []:
        try:
            ids.append(uuid.UUID(str(i)))
        except ValueError:
            continue
    if not ids:
        return []
    rows = {m.id: m for m in (await session.scalars(select(MediaAsset).where(MediaAsset.id.in_(ids)))).all()}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise PublishError("A media file attached to this post no longer exists. Regenerate or reattach the media.")
    return [rows[i] for i in ids]


def read_bytes(asset: MediaAsset) -> bytes:
    from app.core.storage import abs_path

    try:
        return abs_path(asset.path).read_bytes()
    except (OSError, ValueError) as e:
        raise PublishError(f"Couldn't read the media file {asset.path}: {e}")


def filename(asset: MediaAsset) -> str:
    return asset.path.rsplit("/", 1)[-1] or f"{asset.id}"


async def public_url(session: AsyncSession, asset: MediaAsset, *, jpeg: bool = False) -> str:
    """A URL the platform can fetch (Instagram, Threads, GBP). jpeg=True serves a JPEG copy of PNG/WebP images."""
    try:
        media = importlib.import_module("app.services.media")
        variant = await media.ensure_jpeg(session, asset) if jpeg and not is_video(asset) else ""
        return await media.ensure_public_url(session, asset, variant or None)
    except PublishError:
        raise
    except Exception as e:  # noqa: BLE001
        raise PublishError(
            f"Couldn't get a public link for the media ({e}). Set up Cloudinary or your own public URL "
            "under Integrations → Search and hosting."
        )


def is_video(asset: MediaAsset) -> bool:
    return asset.kind == "video" or (asset.mime or "").startswith("video/")


def is_gif(asset: MediaAsset) -> bool:
    return (asset.mime or "") == "image/gif" or asset.kind == "gif" and not is_video(asset)


def is_pdf(asset: MediaAsset) -> bool:
    return asset.kind == "document" or (asset.mime or "") == "application/pdf"


_HASHTAG = re.compile(r"(?<![\w#])#(\w+)", re.UNICODE)


def compose_text(post: Post, *, hashtags: bool = True, link: bool = False) -> str:
    """Post text plus hashtags that aren't already in it (and optionally the link)."""
    text = (post.text or "").strip()
    if hashtags and post.hashtags:
        present = {m.lower() for m in _HASHTAG.findall(text)}
        extra = []
        for h in post.hashtags:
            tag = str(h).strip().lstrip("#").replace(" ", "")
            if tag and tag.lower() not in present:
                extra.append("#" + tag)
                present.add(tag.lower())
        if extra:
            text = (text + "\n\n" + " ".join(extra)).strip()
    if link and post.link_url and post.link_url not in text:
        text = (text + "\n\n" + post.link_url).strip()
    return text


def metrics(raw: dict | None = None, **values) -> dict:
    out = {k: 0 for k in ("impressions", "reach", "likes", "comments", "shares", "clicks", "saves", "video_views")}
    for k, v in values.items():
        if k in out and v is not None:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass
    out["raw"] = raw or {}
    return out
