"""Inbox orchestration: collect from connected accounts, store, classify, notify; reply and like actions."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post, SocialAccount, User
from app.models.inbox import InboxItem, InboxSettings
from app.services.inbox import bluesky, mastodon, meta, sentiment, threads, youtube
from app.services.inbox.base import (
    CAPABILITIES, LISTEN_REPLY_PLATFORMS, SYNC_DAYS, SYNC_PLATFORMS, CollectContext, InboxPermissionError, PostRef,
    clip, parse_time,
)
from app.services.inbox.store import account_key, store
from app.services.social.common import PublishError

log = logging.getLogger("inbox")

COLLECTORS = {
    "facebook_page": meta.collect_facebook, "instagram": meta.collect_instagram, "threads": threads.collect,
    "youtube": youtube.collect, "bluesky": bluesky.collect, "mastodon": mastodon.collect,
}
REPLIERS = {
    "facebook_page": meta.reply_facebook, "instagram": meta.reply_instagram, "threads": threads.reply,
    "youtube": youtube.reply, "bluesky": bluesky.reply, "mastodon": mastodon.reply,
}
LIKERS = {"facebook_page": meta.like_facebook, "bluesky": bluesky.like, "mastodon": mastodon.like}

DIGEST_EVERY = timedelta(hours=3)


class InboxActionError(Exception):
    """A reply/like/suggest request that can't be done. status: HTTP status for the API."""

    def __init__(self, message: str, status: int = 400, *, permission: str | None = None, reconnect: bool = False):
        super().__init__(message)
        self.message, self.status, self.permission, self.reconnect = message, status, permission, reconnect


def _now() -> datetime:
    return datetime.now(UTC)


async def get_settings(session: AsyncSession, org_id: uuid.UUID) -> InboxSettings:
    s = await session.scalar(select(InboxSettings))
    if s is None:
        s = InboxSettings(organization_id=org_id, keywords=[], excluded=[], languages=[], sources={}, state={})
        session.add(s)
        await session.flush()
    return s


async def _our_posts(session: AsyncSession, account: SocialAccount, since: datetime) -> dict[str, PostRef]:
    rows = (await session.scalars(select(Post).where(
        Post.social_account_id == account.id, Post.external_id.is_not(None), Post.status == "published",
        or_(Post.published_at.is_(None), Post.published_at >= since)).order_by(Post.published_at.desc()).limit(25))).all()
    return {p.external_id: PostRef(p.external_id, p.external_url, p.text) for p in rows}


def _account_error(e: Exception) -> dict:
    if isinstance(e, InboxPermissionError):
        return {"ok": False, "message": e.message, "missing_permission": e.permission, "reconnect": True}
    if isinstance(e, PublishError):
        return {"ok": False, "message": e.message, "missing_permission": None, "reconnect": e.reconnect,
                "retryable": e.retryable}
    return {"ok": False, "message": f"Couldn't reach the platform ({type(e).__name__}). It will try again.",
            "missing_permission": None, "reconnect": False, "retryable": True}


async def sync_accounts(session: AsyncSession, org_id: uuid.UUID, account_id: uuid.UUID | None = None) -> dict:
    """Collect comments, replies and mentions from every connected account that supports it.
    Commits after each account. Returns {"created": [...], "notify": [...], "accounts": {...}}."""
    settings = await get_settings(session, org_id)
    q = select(SocialAccount).where(SocialAccount.platform.in_(SYNC_PLATFORMS), SocialAccount.status != "removed")
    if account_id:
        q = q.where(SocialAccount.id == account_id)
    accounts = list((await session.scalars(q.order_by(SocialAccount.created_at))).all())
    since = _now() - timedelta(days=SYNC_DAYS)
    created_all: list[InboxItem] = []
    notify: list[InboxItem] = []
    for acc in accounts:
        state = dict(settings.state or {})
        accs = dict(state.get("accounts") or {})
        st = dict(accs.get(str(acc.id)) or {})
        st["last_attempt_at"] = _now().isoformat()
        if acc.status != "connected":
            st.update(ok=False, reconnect=True, missing_permission=None,
                      message=acc.status_message or "This account needs reconnecting in Settings → Social accounts.")
        else:
            ctx = CollectContext(since=since, posts=await _our_posts(session, acc, since), state=st,
                                 first_run=not st.get("last_synced_at"))
            try:
                items = await COLLECTORS[acc.platform](session, acc, ctx)
                created = await store(session, org_id, items, account=acc)
                created_all.extend(created)
                if not ctx.first_run:  # the first sync back-fills 30 days quietly
                    notify.extend(c for c in created if not c.is_own)
                st.update(ok=True, message=None, missing_permission=None, reconnect=False, retryable=False,
                          last_synced_at=_now().isoformat(), notes=ctx.notes[:5], last_found=len(created))
            except (PublishError, httpx.HTTPError) as e:
                log.info("inbox sync of %s (%s) failed: %s", acc.id, acc.platform, e)
                st.update(_account_error(e))
        accs[str(acc.id)] = st
        state["accounts"] = accs
        settings.state = state
        await session.commit()
    if created_all:
        await sentiment.classify(session, [c for c in created_all if not c.is_own])
        await session.commit()
    return {"created": created_all, "notify": notify, "accounts": (settings.state or {}).get("accounts", {})}


# ------------------------------------------------------------------ notifications
def _line(i: InboxItem) -> str:
    who = i.author_name or i.author_handle or "Someone"
    where = CAPABILITIES[i.platform].label if i.platform in CAPABILITIES else i.platform.capitalize()
    return f"• {who} on {where}: {clip(i.text or i.title or '', 140)}"


async def notify_new(session: AsyncSession, org_id: uuid.UUID, items: list[InboxItem], *, force_digest: bool = False) -> dict:
    """Urgent items notify at once (one message per run); everything else is batched into a digest at most every
    3 hours. Call after committing the items."""
    from app.services import notify as notifier

    settings = await get_settings(session, org_id)
    state = dict(settings.state or {})
    sent = {"urgent": 0, "digest": 0}
    urgent = [i for i in items if not i.is_own and (i.priority or i.sentiment == "negative")]
    if urgent:
        title = ("An urgent comment needs a reply" if len(urgent) == 1
                 else f"{len(urgent)} urgent or negative comments need a reply")
        link = f"/inbox?item={urgent[0].id}" if len(urgent) == 1 else "/inbox?sentiment=negative"
        try:
            await notifier.notify_org(org_id, "inbox_urgent", title, "\n".join(_line(i) for i in urgent[:5]),
                                      link=link, permission="inbox.view")
            sent["urgent"] = len(urgent)
        except Exception:  # noqa: BLE001
            log.exception("inbox urgent notification failed")
    pending = dict(state.get("pending_digest") or {})
    for i in items:
        if i.is_own:
            continue
        key = "mentions" if i.kind == "mention" else "comments"
        pending[key] = int(pending.get(key) or 0) + 1
    last = parse_time(state.get("last_digest_at"))
    total = int(pending.get("comments") or 0) + int(pending.get("mentions") or 0)
    if total and (force_digest or last is None or _now() - last >= DIGEST_EVERY):
        parts = []
        if pending.get("comments"):
            n = int(pending["comments"])
            parts.append(f"{n} new comment{'s' if n != 1 else ''}")
        if pending.get("mentions"):
            n = int(pending["mentions"])
            parts.append(f"{n} new mention{'s' if n != 1 else ''}")
        body = "\n".join(_line(i) for i in [i for i in items if not i.is_own][:5]) or None
        try:
            await notifier.notify_org(org_id, "inbox_new", " and ".join(parts) + " in the inbox", body, link="/inbox",
                                      permission="inbox.view")
            sent["digest"] = total
            pending = {}
            state["last_digest_at"] = _now().isoformat()
        except Exception:  # noqa: BLE001
            log.exception("inbox digest notification failed")
    state["pending_digest"] = pending
    settings.state = state
    await session.commit()
    return sent


# ------------------------------------------------------------------ actions
async def thread_of(session: AsyncSession, item: InboxItem) -> list[InboxItem]:
    if not item.thread_key or not item.social_account_id:
        return [item]
    rows = (await session.scalars(select(InboxItem).where(
        InboxItem.social_account_id == item.social_account_id, InboxItem.thread_key == item.thread_key)
        .order_by(InboxItem.published_at.asc().nulls_first(), InboxItem.created_at.asc()).limit(100))).all()
    return list(rows) or [item]


def can_reply(item: InboxItem) -> tuple[bool, str | None]:
    if item.is_own:
        return False, "This is your own reply."
    if item.source == "listening" and item.platform not in LISTEN_REPLY_PLATFORMS:
        return False, "Reply on the site itself: open the link."
    cap = CAPABILITIES.get(item.platform)
    if cap is None or not cap.reply:
        return False, "This platform doesn't let apps reply. Open it to answer there."
    if not item.social_account_id:
        return False, "Connect an account on this platform to reply from here."
    return True, None


def can_like(item: InboxItem) -> bool:
    return bool(item.social_account_id) and not item.is_own and item.platform in LIKERS and \
        (item.source == "account" or item.platform in LISTEN_REPLY_PLATFORMS)


async def _account_for(session: AsyncSession, item: InboxItem) -> SocialAccount:
    acc = await session.get(SocialAccount, item.social_account_id) if item.social_account_id else None
    if acc is None or acc.status == "removed":
        raise InboxActionError("The account this came from was removed. Reconnect it to reply.", 409, reconnect=True)
    if acc.status != "connected":
        raise InboxActionError(f"{acc.name} needs reconnecting in Settings → Social accounts before you can reply.",
                               409, reconnect=True)
    return acc


async def _remember_permission(session: AsyncSession, org_id: uuid.UUID, acc: SocialAccount, e: InboxPermissionError) -> None:
    settings = await get_settings(session, org_id)
    state = dict(settings.state or {})
    accs = dict(state.get("accounts") or {})
    st = dict(accs.get(str(acc.id)) or {})
    st.update(reply_missing_permission=e.permission, reply_message=e.message)
    accs[str(acc.id)] = st
    state["accounts"] = accs
    settings.state = state


def _account_state(settings: InboxSettings, acc: SocialAccount) -> dict:
    return dict(((settings.state or {}).get("accounts") or {}).get(str(acc.id)) or {})


async def send_reply(session: AsyncSession, org_id: uuid.UUID, item: InboxItem, text: str, user: User) -> InboxItem:
    """Send a reply through the platform. Records the outcome on the item (and our reply as its own item).
    The caller commits, also after an InboxActionError (the error is recorded on the item)."""
    ok, why = can_reply(item)
    if not ok:
        raise InboxActionError(why or "Replies aren't possible here.")
    text = (text or "").strip()
    if not text:
        raise InboxActionError("Write a reply first.")
    from app.services.inbox.suggest import reply_limit

    limit = reply_limit(item.platform)
    if len(text) > limit:
        raise InboxActionError(f"Replies on this platform are limited to {limit} characters; this one has {len(text)}.")
    acc = await _account_for(session, item)
    settings = await get_settings(session, org_id)
    try:
        ext_id, url = await REPLIERS[item.platform](session, acc, item, text, _account_state(settings, acc))
    except InboxPermissionError as e:
        item.reply_error = e.message
        await _remember_permission(session, org_id, acc, e)
        raise InboxActionError(e.message, 409, permission=e.permission, reconnect=True)
    except PublishError as e:
        item.reply_error = e.message
        raise InboxActionError(e.message, 409 if e.reconnect else 502, reconnect=e.reconnect)
    except httpx.HTTPError as e:
        item.reply_error = f"Couldn't reach the platform ({type(e).__name__}). Check whether the reply went out first."
        raise InboxActionError(item.reply_error, 502)
    now = _now()
    item.status, item.reply_text, item.replied_at, item.replied_by = "replied", text, now, user.id
    item.reply_external_id, item.reply_error = ext_id, None
    st = _account_state(settings, acc)
    if st.get("reply_missing_permission"):
        await _clear_reply_permission(session, org_id, acc)
    own = None
    if ext_id:
        key = account_key(acc.id, ext_id)
        own = await session.scalar(select(InboxItem).where(InboxItem.dedupe_key == key))
        if own is None:
            own = InboxItem(
                organization_id=org_id, source="account", platform=item.platform, kind="reply", dedupe_key=key,
                social_account_id=acc.id, post_id=item.post_id, external_post_id=item.external_post_id,
                external_post_url=item.external_post_url, post_snippet=item.post_snippet, external_id=ext_id,
                parent_external_id=item.external_id, thread_key=item.thread_key or item.external_id, parent_id=item.id,
                url=url, author_name=acc.name, author_handle=acc.handle, author_avatar_url=acc.avatar_url,
                author_url=acc.profile_url, text=text, published_at=now, is_own=True, status="read",
                replied_by=user.id, ai_suggestions=[], raw={"sent_from_inbox": True},
            )
            session.add(own)
    await session.flush()
    return own or item


async def _clear_reply_permission(session: AsyncSession, org_id: uuid.UUID, acc: SocialAccount) -> None:
    settings = await get_settings(session, org_id)
    state = dict(settings.state or {})
    accs = dict(state.get("accounts") or {})
    st = dict(accs.get(str(acc.id)) or {})
    st.pop("reply_missing_permission", None)
    st.pop("reply_message", None)
    accs[str(acc.id)] = st
    state["accounts"] = accs
    settings.state = state


async def send_like(session: AsyncSession, org_id: uuid.UUID, item: InboxItem) -> None:
    if not can_like(item):
        raise InboxActionError("Liking isn't available for this platform through its API.")
    acc = await _account_for(session, item)
    settings = await get_settings(session, org_id)
    try:
        await LIKERS[item.platform](session, acc, item, _account_state(settings, acc))
    except InboxPermissionError as e:
        raise InboxActionError(e.message, 409, permission=e.permission, reconnect=True)
    except PublishError as e:
        raise InboxActionError(e.message, 409 if e.reconnect else 502, reconnect=e.reconnect)
    except httpx.HTTPError as e:
        raise InboxActionError(f"Couldn't reach the platform ({type(e).__name__}).", 502)
    item.liked_at = _now()
