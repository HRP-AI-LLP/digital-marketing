"""Approve, reject and regenerate posts. Shared by the web API and Telegram buttons.

Each function takes a session scoped to the post's organization, records a PostActivity and an
audit entry, and returns a short message for the person. It raises ValueError (with a message
that says what to do) when the post is in a state that doesn't allow the action. The caller commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post, Schedule
from app.services import audit
from app.services.content import (
    DEFAULT_GRACE_HOURS,
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


def _actor(user, via: str) -> str:
    email = getattr(user, "email", None) or "someone"
    return email if via == "app" else f"{email} via {via}"


async def _grace(session: AsyncSession, post: Post) -> int:
    if post.schedule_id:
        s = await session.get(Schedule, post.schedule_id)
        if s and s.catch_up_grace_hours is not None:
            return s.catch_up_grace_hours
    return DEFAULT_GRACE_HOURS


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
        raise ValueError("Pick a date and time for this post first, then approve it.")
    if post.scheduled_at <= now:
        grace = await _grace(session, post)
        if now - post.scheduled_at > timedelta(hours=grace):
            raise ValueError(
                f"Its posting time passed more than {grace} h ago. Pick a new time, then approve it."
            )
        post.scheduled_at = now + timedelta(minutes=1)
        note = " It was late, so it will go out in about a minute."

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
