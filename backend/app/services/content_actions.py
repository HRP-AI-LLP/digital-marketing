"""Approve, reject and regenerate posts. Shared by the web API and Telegram buttons.

Each function takes a session scoped to the post's organization, records a PostActivity and an
audit entry, and returns a short message for the person. It raises ValueError (with a message
that says what to do) when the post is in a state that doesn't allow the action. The caller commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post
from app.services import audit
from app.services.content import (
    add_activity,
    as_uuid,
    now_utc,
    platform_of,
)
from app.services.jobs import enqueue

APPROVABLE = {"in_review", "draft", "missed", "rejected"}
REJECTABLE = {"in_review", "draft", "scheduled", "approved", "missed"}
REGENERATABLE = {"in_review", "draft", "rejected", "failed", "missed", "scheduled", "approved", "planned"}


async def _load(session: AsyncSession, post_id: str | uuid.UUID) -> Post:
    pid = as_uuid(post_id)
    post = await session.get(Post, pid) if pid else None
    if post is None:
        raise ValueError("That post doesn't exist any more.")
    return post


async def _hook(session: AsyncSession, event: str, post: Post, user, via: str, **extra) -> None:
    """Outgoing webhooks, in the caller's transaction (they go out only if it commits). Never raises."""
    from app.services import webhooks

    try:
        data = webhooks.post_data(post, by=getattr(user, "email", None), via=via, **extra)
    except Exception:  # noqa: BLE001
        return
    await webhooks.emit(post.organization_id, event, data, session=session)


def _actor(user, via: str) -> str:
    email = getattr(user, "email", None) or "someone"
    return email if via == "app" else f"{email} via {via}"


async def _good_time(session: AsyncSession, post: Post, now: datetime) -> datetime:
    """The earliest of the best few posting hours in the next 3 days for this account (never fails)."""
    try:
        from app.models import Organization, SocialAccount
        from app.services import best_time
        from app.services.content import org_tz

        account = await session.get(SocialAccount, post.social_account_id) if post.social_account_id else None
        zone = org_tz(await session.get(Organization, post.organization_id))
        if account is not None:
            slots = await best_time.next_slots(session, account, zone, now=now, count=3, days=3)
            if slots:
                return datetime.fromisoformat(slots[0]["at"])
    except Exception:  # noqa: BLE001 - picking a time must never block approving
        pass
    return now + timedelta(minutes=30)


async def approve_by_id(session: AsyncSession, post_id: str | uuid.UUID, user, via: str = "app",
                        scheduled_at: datetime | None = None) -> str:
    post = await _load(session, post_id)
    if post.status in ("scheduled", "approved"):
        return "Already approved."
    if post.status not in APPROVABLE:
        if post.status in ("planned", "generating"):
            raise ValueError("This post is still being written. Try again in a minute.")
        raise ValueError(f"A post that is {post.status.replace('_', ' ')} can't be approved.")
    if not (post.text or "").strip() and not post.media_ids:
        raise ValueError("This post has no content yet. Regenerate it or write the text first.")

    now = now_utc()
    if scheduled_at is not None:
        if scheduled_at.tzinfo is None:
            raise ValueError("Give the new time with a time zone.")
        if scheduled_at <= now:
            raise ValueError("Pick a time in the future.")
        post.scheduled_at = scheduled_at

    note = ""
    if post.scheduled_at is None:
        # No time chosen: use the next good time for this account instead of refusing.
        post.scheduled_at = await _good_time(session, post, now)
        note = f" No time was set, so it goes out at a good time for {platform_of(post.platform).label}."
    if post.scheduled_at <= now:
        # Late (the app was off, or it waited for approval): it goes out now, gently spaced per account.
        post.scheduled_at = now + timedelta(minutes=1)
        note = " Its time has passed, so it will go out in about a minute."
    post.meta = {k: v for k, v in (post.meta or {}).items() if k not in ("held_reason", "overdue_reminded", "catch_up_at")}

    post.status = "scheduled"
    post.error = None
    post.approval_deadline = None
    post.approved_by = getattr(user, "id", None)
    post.approved_at = now
    post.rejection_reason = None
    add_activity(session, post, "approved", f"Approved{'' if via == 'app' else ' via ' + via}", user=user,
                 actor=_actor(user, via))
    audit.record(session, action="post.approved", organization_id=post.organization_id, actor=user,
                 entity_type="post", entity_id=post.id,
                 summary=f"Approved a {platform_of(post.platform).label} post" + ("" if via == "app" else f" via {via}"))
    await session.flush()
    await _hook(session, "post.approved", post, user, via)
    return "Approved. It's scheduled." + note


async def reject_by_id(session: AsyncSession, post_id: str | uuid.UUID, user, reason: str | None,
                       via: str = "app") -> str:
    post = await _load(session, post_id)
    if post.status == "rejected":
        return "Already rejected."
    if post.status not in REJECTABLE:
        raise ValueError(f"A post that is {post.status.replace('_', ' ')} can't be rejected.")
    reason = (reason or "").strip()[:1000] or "No reason given"
    post.status = "rejected"
    post.rejection_reason = reason
    post.approval_deadline = None
    meta = dict(post.meta or {})
    avoid = list(meta.get("avoid") or [])
    if reason != "No reason given" and reason not in avoid:
        avoid.append(reason)
    meta["avoid"] = avoid[-10:]
    post.meta = meta
    add_activity(session, post, "rejected", reason, user=user, actor=_actor(user, via))
    audit.record(session, action="post.rejected", organization_id=post.organization_id, actor=user,
                 entity_type="post", entity_id=post.id,
                 summary=f"Rejected a {platform_of(post.platform).label} post: {reason[:120]}")
    await session.flush()
    await _hook(session, "post.rejected", post, user, via, reason=reason)
    return "Rejected. The reason will guide future posts."


async def regenerate_by_id(session: AsyncSession, post_id: str | uuid.UUID, user, via: str = "app",
                           note: str | None = None) -> str:
    post = await _load(session, post_id)
    if post.status in ("generating",):
        return "A new version is already being written."
    if post.status not in REGENERATABLE:
        raise ValueError(f"A post that is {post.status.replace('_', ' ')} can't be regenerated.")
    meta = dict(post.meta or {})
    meta["regenerate_note"] = (note or "").strip()[:500] or "The previous version wasn't good enough."
    if post.rejection_reason and post.rejection_reason not in (meta.get("avoid") or []):
        meta["avoid"] = list(meta.get("avoid") or []) + [post.rejection_reason]
    meta["previous_text"] = (post.text or "")[:3000]
    meta.pop("keep_media", None)
    post.meta = meta
    post.status = "planned"
    post.error = None
    post.approval_deadline = None
    post.approved_by = None
    post.approved_at = None
    if meta.get("held_from_mode"):
        post.mode = meta.pop("held_from_mode")  # a hold on the previous version shouldn't stick
        post.meta = meta
    add_activity(session, post, "regenerate_requested", note, user=user, actor=_actor(user, via))
    audit.record(session, action="post.regenerated", organization_id=post.organization_id, actor=user,
                 entity_type="post", entity_id=post.id, summary=f"Asked for a new version of a {platform_of(post.platform).label} post")
    await enqueue(session, "post.generate", post.organization_id, {"post_id": str(post.id)},
                  dedupe_key=f"gen:{post.id}", priority=3, created_by=getattr(user, "id", None))
    await session.flush()
    return "Writing a new version. You'll be told when it's ready."
