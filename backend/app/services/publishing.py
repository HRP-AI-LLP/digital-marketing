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
    is_manual,
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


async def _check_monthly_limit(session: AsyncSession, account: SocialAccount, label: str) -> None:
    """Accounts can have a monthly post limit (Settings → Social accounts), e.g. to cap X API spend."""
    from app.models import Organization
    from app.services.scheduler import monthly_limit, org_tz, posts_in_month, now_utc

    limit = monthly_limit(account)
    if not limit:
        return
    org = await session.get(Organization, account.organization_id)
    done = await posts_in_month(session, account, now_utc(), org_tz(org), published_only=True)
    if done >= limit:
        raise PublishError(
            f"{account.name} has reached its limit of {limit} {label} posts this month (set in Settings → Social accounts). "
            f"Raise the limit, wait for next month, or post this one by hand.", retryable=False)


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
    if not is_manual(account):
        await _check_monthly_limit(session, account, spec.label)
    mod = adapter_for_platform("manual" if is_manual(account) else account.platform)
    try:
        return await mod.publish(session, post, account, media)
    except PublishError:
        raise
    except httpx.HTTPError as e:
        raise _wrap_network(e, spec.label)


async def fetch_metrics(session: AsyncSession, post: Post, account: SocialAccount) -> dict | None:
    """Engagement numbers, or None when the platform doesn't offer them (or permission is missing)."""
    if not post.external_id or account.platform not in PLATFORMS or is_manual(account):
        return None
    try:
        return await adapter_for_platform(account.platform).fetch_metrics(session, post, account)
    except (PublishError, httpx.HTTPError, KeyError, ValueError) as e:
        log.info("metrics unavailable for post %s on %s: %s", post.id, account.platform, e)
        return None


def can_delete(account: SocialAccount | None) -> bool:
    """Whether posts on this account can be deleted from the platform through the app."""
    if account is None or is_manual(account) or account.platform not in PLATFORMS:
        return False
    return hasattr(adapter_for_platform(account.platform), "delete")


async def delete_remote(session: AsyncSession, post: Post, account: SocialAccount) -> None:
    """Delete a published post on the platform (X, Pinterest). Raises PublishError."""
    if not post.external_id:
        raise PublishError("This post has no link to a platform post, so there's nothing to delete there.")
    if not can_delete(account):
        label = PLATFORMS[account.platform].label if account and account.platform in PLATFORMS else "This platform"
        raise PublishError(f"{label} posts can't be deleted from the app. Delete it on the platform itself.")
    try:
        await adapter_for_platform(account.platform).delete(session, post, account)
    except PublishError:
        raise
    except httpx.HTTPError as e:
        raise PublishError(f"Couldn't reach {PLATFORMS[account.platform].label} ({type(e).__name__}). Try again.",
                           retryable=True)


async def refresh_account(session: AsyncSession, account: SocialAccount) -> None:
    """Refresh tokens when near expiry and check the account still works; updates status fields."""
    if account.status == "removed":
        return
    if is_manual(account):
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
        keep = account.platform in ("pinterest", "tiktok", "x") and msg and not msg.startswith("Last check failed") \
            and "expired" not in msg.lower()
        account.status_message = msg if keep else None
        account.settings = {**(account.settings or {}), "_last_checked_at": datetime.now(UTC).isoformat()}
