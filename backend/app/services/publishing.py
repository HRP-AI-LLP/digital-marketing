"""Publishing contract used by the content engine (see docs/ARCHITECTURE.md).

  await publish(session, post, account) -> (external_id, external_url)    raises PublishError
  await fetch_metrics(session, post, account) -> dict | None
  await refresh_account(session, account) -> None
  account_tokens(account) -> dict, save_tokens(account, tokens, expires_at)

Platform adapters live in app/services/social/. Callers commit the session.
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post, SocialAccount
from app.services.platforms import PLATFORMS, POST_TYPES
from app.services.social.common import (  # re-exported: part of the contract
    PublishError,
    account_tokens,
    adapter_for_platform,
    load_media,
    save_tokens,
)

__all__ = ["PublishError", "account_tokens", "fetch_metrics", "publish", "refresh_account", "save_tokens"]

log = logging.getLogger("publishing")

MEDIA_TYPES = {"image", "carousel", "gif", "video", "animation", "document"}


def _wrap_network(e: Exception, label: str) -> PublishError:
    # Safe to retry only when the request certainly never reached the platform.
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return PublishError(f"Couldn't reach {label}. It will be retried.", retryable=True)
    # Read/write timeouts and dropped connections: the post may already be live, so never auto-retry.
    return PublishError(
        f"The connection to {label} broke while posting ({type(e).__name__}). The post may or may not be live: "
        f"check {label} first, then press Publish now only if it isn't there.", retryable=False)


async def publish(session: AsyncSession, post: Post, account: SocialAccount) -> tuple[str | None, str | None]:
    spec = PLATFORMS.get(account.platform)
    if spec is None:
        raise PublishError(f"Posting to {account.platform} isn't supported.")
    if account.status == "removed":
        raise PublishError(f"The account {account.name} was removed. Reconnect it or move the post to another account.",
                           reconnect=True)
    if account.status == "expired":
        raise PublishError(f"{spec.label} access for {account.name} has expired. Reconnect it in Settings → Social accounts.",
                           reconnect=True)
    if post.post_type not in spec.post_types:
        raise PublishError(f"{spec.label} doesn't support {POST_TYPES.get(post.post_type, post.post_type).lower()} posts.")
    media = await load_media(session, post)
    if post.post_type in MEDIA_TYPES and not media:
        raise PublishError(f"This {POST_TYPES.get(post.post_type, post.post_type).lower()} post has no media attached.")
    if spec.max_images and len(media) > max(spec.max_images, 1) and account.platform not in ("manual",):
        media = media[: max(spec.max_images, 1)]
    mod = adapter_for_platform(account.platform)
    try:
        return await mod.publish(session, post, account, media)
    except PublishError:
        raise
    except httpx.HTTPError as e:
        raise _wrap_network(e, spec.label)


async def fetch_metrics(session: AsyncSession, post: Post, account: SocialAccount) -> dict | None:
    """Engagement numbers, or None when the platform doesn't offer them (or permission is missing)."""
    if not post.external_id or account.platform not in PLATFORMS:
        return None
    try:
        return await adapter_for_platform(account.platform).fetch_metrics(session, post, account)
    except (PublishError, httpx.HTTPError, KeyError, ValueError) as e:
        log.info("metrics unavailable for post %s on %s: %s", post.id, account.platform, e)
        return None


async def refresh_account(session: AsyncSession, account: SocialAccount) -> None:
    """Refresh tokens when near expiry and check the account still works; updates status fields."""
    if account.status == "removed":
        return
    if account.platform in ("manual", "x"):
        account.status, account.status_message = "manual", None
        return
    try:
        await adapter_for_platform(account.platform).refresh(session, account)
    except PublishError as e:
        if e.reconnect:
            account.status, account.status_message = "expired", e.message[:1000]
        else:
            # Temporary problem (or config issue): keep the status, explain what happened.
            account.status_message = f"Last check failed: {e.message}"[:1000]
            if not e.retryable and account.status == "connected":
                account.status = "error"
    except httpx.HTTPError as e:
        account.status_message = f"Last check failed: couldn't reach the platform ({type(e).__name__})."
    else:
        account.status = "connected"
        msg = account.status_message or ""
        # Keep informational notes (Pinterest trial access); clear old failure messages.
        keep = account.platform == "pinterest" and msg and not msg.startswith("Last check failed") \
            and "expired" not in msg.lower()
        account.status_message = msg if keep else None
        account.settings = {**(account.settings or {}), "_last_checked_at": datetime.now(UTC).isoformat()}
