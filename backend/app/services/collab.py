"""Team collaboration helpers: post version history, internal comments (@mentions) and saved inbox replies.

Version history: every change to a post's content (title, text, hashtags, link, first comment, media) made through
the API or by the AI stores a snapshot in post_versions. Call `record_change(session, post, before, reason, user)`
after changing a post, with `before = snapshot(post)` taken before the change; the first call also stores the
original content as version 1 so nothing is lost.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Membership, Organization, Post, PostComment, PostVersion, SavedReply, User
from app.models.base import utcnow

log = logging.getLogger(__name__)

SNAPSHOT_FIELDS = ("title", "text", "hashtags", "link_url", "first_comment", "media_ids")
FIELD_LABELS = {"title": "title", "text": "text", "hashtags": "hashtags", "link_url": "link",
                "first_comment": "first comment", "media_ids": "media"}
RESTORE_BLOCKED = ("published", "publishing", "planned", "generating")


# ====================================================================== versions
def snapshot(post: Post) -> dict[str, Any]:
    return {
        "title": post.title or None,
        "text": post.text or "",
        "hashtags": [str(h) for h in (post.hashtags or [])],
        "link_url": post.link_url or None,
        "first_comment": post.first_comment or None,
        "media_ids": [str(m) for m in (post.media_ids or [])],
    }


def _has_content(snap: dict | None) -> bool:
    return bool(snap) and any(snap.get(k) for k in SNAPSHOT_FIELDS)


def changed_fields(a: dict | None, b: dict | None) -> list[str]:
    a, b = a or {}, b or {}
    return [k for k in SNAPSHOT_FIELDS if (a.get(k) or None) != (b.get(k) or None)]


async def latest_version(session: AsyncSession, post_id: uuid.UUID) -> PostVersion | None:
    return await session.scalar(select(PostVersion).where(PostVersion.post_id == post_id)
                                .order_by(PostVersion.version.desc()).limit(1))


async def _add(session: AsyncSession, post: Post, number: int, fields: dict, reason: str | None,
               user_id: uuid.UUID | None) -> PostVersion:
    v = PostVersion(organization_id=post.organization_id, post_id=post.id, version=number, fields=fields,
                    changed_by=user_id, reason=(reason or "")[:300] or None, created_at=utcnow())
    session.add(v)
    await session.flush()
    return v


async def record_change(session: AsyncSession, post: Post, before: dict | None, reason: str, user=None) -> PostVersion | None:
    """Store the post's current content as a new version when it differs from the latest one (caller commits).

    `before`: snapshot taken before the change. When the post has no versions yet and `before` had content,
    it is stored first as the original version."""
    await session.flush()
    now = snapshot(post)
    uid = getattr(user, "id", None)
    last = await latest_version(session, post.id)
    number = last.version if last else 0
    if last is None and _has_content(before) and changed_fields(before, now):
        await _add(session, post, 1, before, "Original version", None)
        number = 1
        last_fields = before
    else:
        last_fields = last.fields if last else None
    if last_fields is not None and not changed_fields(last_fields, now):
        return None
    if last_fields is None and not _has_content(now):
        return None
    return await _add(session, post, number + 1, now, reason, uid)


def edit_reason(fields: list[str]) -> str:
    names = [FIELD_LABELS.get(f, f) for f in fields]
    return ("Edited " + ", ".join(names)) if names else "Edited"


# ====================================================================== comments
MENTION_RE = re.compile(r"@([\w.\-' ]{1,60})")


async def members(session: AsyncSession, org_id: uuid.UUID) -> list[User]:
    """Active members of the organization (RLS limits memberships to the current tenant)."""
    q = (select(User).join(Membership, Membership.user_id == User.id)
         .where(Membership.organization_id == org_id, Membership.is_active.is_(True), User.is_active.is_(True)))
    return list((await session.scalars(q)).unique().all())


async def comment_counts(session: AsyncSession, post_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    if not post_ids:
        return {}
    rows = (await session.execute(
        select(PostComment.post_id, func.count(), func.count().filter(PostComment.resolved.is_(False)))
        .where(PostComment.post_id.in_(post_ids)).group_by(PostComment.post_id))).all()
    return {pid: {"total": int(total), "open": int(open_)} for pid, total, open_ in rows}


def snippet(text: str, n: int = 240) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


# ====================================================================== saved replies
VARIABLES = {
    "author_name": "The name of the person you're replying to",
    "company": "Your organization's name",
    "website": "Your website address (Settings → Organization)",
    "my_name": "Your name",
    "email": "Your organization's contact email (Settings → Organization)",
    "phone": "Your organization's phone number (Settings → Organization)",
}
VAR_RE = re.compile(r"\{(" + "|".join(VARIABLES) + r")\}")


def variable_values(org: Organization | None, user: User | None, author_name: str | None) -> dict[str, str]:
    first = (author_name or "").strip()
    return {
        "author_name": first or "there",
        "company": (org.name if org else "") or "",
        "website": ((org.website if org else "") or "").strip(),
        "my_name": ((user.full_name if user else "") or "").strip(),
        "email": ((org.email if org else "") or "").strip(),
        "phone": ((org.phone if org else "") or "").strip(),
    }


def fill(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """Replace {variables}. Returns the text and the variables that had no value (left out of the text)."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        v = values.get(m.group(1)) or ""
        if not v and m.group(1) not in missing:
            missing.append(m.group(1))
        return v

    out = VAR_RE.sub(sub, text or "")
    if missing:  # tidy the gaps left by empty values
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r" +([,.!?;:])", r"\1", out)
    return out.strip(), missing


def default_replies(org: Organization | None) -> list[dict]:
    """Starter replies. They only use facts from the organization profile (through variables), nothing invented."""
    has_email = bool(org and (org.email or "").strip())
    has_site = bool(org and (org.website or "").strip())
    support = ("Hi {author_name}, sorry to hear you're having trouble. Please email us at {email} with the details "
               "and our team will help you as soon as possible." if has_email else
               "Hi {author_name}, sorry to hear you're having trouble. Please send us a direct message with the details "
               "and our team will help you as soon as possible.")
    pricing = ("Hi {author_name}, thanks for your interest in {company}! For pricing, please get in touch through "
               "{website} and our team will share the details with you." if has_site else
               "Hi {author_name}, thanks for your interest in {company}! Please send us a direct message and our team "
               "will share the pricing details with you.")
    return [
        {"title": "Thank you", "tags": ["thanks"],
         "text": "Thank you so much, {author_name}! We really appreciate your support."},
        {"title": "We'll message you", "tags": ["follow-up"],
         "text": "Thanks for reaching out, {author_name}. We'll send you a direct message shortly."},
        {"title": "Support contact", "tags": ["support"], "text": support},
        {"title": "Pricing enquiry", "tags": ["sales", "pricing"], "text": pricing},
    ]


async def seed_saved_replies(session: AsyncSession, org: Organization, user: User | None) -> int:
    """Add the starter replies once per organization (not again after the team deletes them). Caller commits."""
    from app.services.inbox.sync import get_settings

    st = await get_settings(session, org.id)
    state = dict(st.state or {})
    if state.get("saved_replies_seeded"):
        return 0
    have = await session.scalar(select(func.count()).select_from(SavedReply))
    added = 0
    if not have:
        for d in default_replies(org):
            session.add(SavedReply(organization_id=org.id, title=d["title"], text=d["text"], tags=d["tags"], platforms=[],
                                   usage_count=0, created_by=getattr(user, "id", None)))
            added += 1
    state["saved_replies_seeded"] = True
    st.state = state
    await session.flush()
    return added
