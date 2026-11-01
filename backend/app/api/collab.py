"""Team collaboration API.

- Saved replies for the inbox (/inbox/saved-replies): using them needs inbox.reply, editing needs inbox.manage.
- Internal team comments on posts with @mentions (/content/posts/{id}/comments). Comments are never published.
- Post version history (/content/posts/{id}/versions) with restore.

All queries go through ctx.session, which row-level security scopes to the caller's organization.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import MediaAsset, Post, PostComment, PostVersion, SavedReply, User
from app.models.base import utcnow
from app.models.inbox import InboxItem
from app.services import audit, collab
from app.services import content as content_svc
from app.services.notify import _users_with_permission, notify_users

log = logging.getLogger(__name__)

inbox_router = APIRouter(prefix="/inbox", tags=["inbox"])
content_router = APIRouter(prefix="/content", tags=["content"])

COMMENT_PERMS = ("content.create", "content.approve")


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


def _clean_list(v: list[str] | None, limit: int = 20, item_len: int = 40) -> list[str]:
    out, seen = [], set()
    for x in v or []:
        s = str(x).strip()[:item_len]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:limit]


# ====================================================================== saved replies
class SavedReplyIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=5000)
    tags: list[str] = []
    platforms: list[str] = []

    @field_validator("title", "text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This can't be empty.")
        return v


class SavedReplyPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    text: str | None = Field(None, min_length=1, max_length=5000)
    tags: list[str] | None = None
    platforms: list[str] | None = None


class UseIn(BaseModel):
    item_id: uuid.UUID | None = None


def reply_out(r: SavedReply) -> dict:
    return {"id": r.id, "title": r.title, "text": r.text, "tags": r.tags or [], "platforms": r.platforms or [],
            "usage_count": r.usage_count or 0, "last_used_at": r.last_used_at, "created_at": r.created_at,
            "updated_at": r.updated_at}


async def _reply(ctx: Context, rid: uuid.UUID) -> SavedReply:
    r = await ctx.session.get(SavedReply, rid)
    if r is None:
        raise _bad("That saved reply doesn't exist (it may have been deleted).", 404)
    return r


@inbox_router.get("/saved-replies")
async def list_saved_replies(platform: str | None = None, q: str | None = Query(None, max_length=200),
                             ctx: Context = Depends(require("inbox.reply"))):
    """Saved replies, most used first. The first time anyone opens them, four starter replies are added."""
    if await collab.seed_saved_replies(ctx.session, ctx.org, ctx.user):
        audit.record(ctx.session, action="saved_reply.seeded", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="saved_reply", summary="Added the starter saved replies", ip=ctx.ip)
    await ctx.session.commit()
    rows = list((await ctx.session.scalars(
        select(SavedReply).order_by(SavedReply.usage_count.desc(), SavedReply.title))).all())
    if platform:
        rows = [r for r in rows if not r.platforms or platform in r.platforms]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r.title.lower() or needle in r.text.lower()
                or any(needle in t.lower() for t in (r.tags or []))]
    from app.services.inbox.base import CAPABILITIES

    return {"items": [reply_out(r) for r in rows], "variables": collab.VARIABLES,
            "platforms": [{"id": k, "label": c.label} for k, c in CAPABILITIES.items()],
            "can_manage": "inbox.manage" in ctx.permissions}


@inbox_router.post("/saved-replies", status_code=201)
async def add_saved_reply(body: SavedReplyIn, ctx: Context = Depends(require("inbox.manage"))):
    r = SavedReply(organization_id=ctx.org_id, title=body.title, text=body.text, tags=_clean_list(body.tags),
                   platforms=_clean_list(body.platforms), usage_count=0, created_by=ctx.user.id)
    ctx.session.add(r)
    await ctx.session.flush()
    audit.record(ctx.session, action="saved_reply.added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="saved_reply", entity_id=r.id, summary=f"Added saved reply “{r.title}”", ip=ctx.ip)
    await ctx.session.commit()
    return reply_out(r)


@inbox_router.patch("/saved-replies/{rid}")
async def update_saved_reply(rid: uuid.UUID, body: SavedReplyPatch, ctx: Context = Depends(require("inbox.manage"))):
    r = await _reply(ctx, rid)
    data = body.model_dump(exclude_unset=True)
    for k in ("title", "text"):
        if data.get(k) is not None:
            v = data[k].strip()
            if not v:
                raise _bad("The title and text can't be empty.")
            setattr(r, k, v)
    if data.get("tags") is not None:
        r.tags = _clean_list(data["tags"])
    if data.get("platforms") is not None:
        r.platforms = _clean_list(data["platforms"])
    audit.record(ctx.session, action="saved_reply.updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="saved_reply", entity_id=r.id, summary=f"Edited saved reply “{r.title}”", ip=ctx.ip)
    await ctx.session.commit()
    return reply_out(r)


@inbox_router.delete("/saved-replies/{rid}", status_code=204)
async def delete_saved_reply(rid: uuid.UUID, ctx: Context = Depends(require("inbox.manage"))):
    r = await _reply(ctx, rid)
    await ctx.session.delete(r)
    audit.record(ctx.session, action="saved_reply.deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="saved_reply", entity_id=rid, summary=f"Deleted saved reply “{r.title}”", ip=ctx.ip)
    await ctx.session.commit()


@inbox_router.post("/saved-replies/{rid}/use")
async def use_saved_reply(rid: uuid.UUID, body: UseIn | None = None, ctx: Context = Depends(require("inbox.reply"))):
    """Fill in the variables for one inbox item and count the use. Returns the text to put in the reply box."""
    r = await _reply(ctx, rid)
    author = None
    if body and body.item_id:
        it = await ctx.session.get(InboxItem, body.item_id)
        if it is None:
            raise _bad("That inbox item doesn't exist (it may have been removed).", 404)
        author = it.author_name or (it.author_handle or "").lstrip("@") or None
    text, missing = collab.fill(r.text, collab.variable_values(ctx.org, ctx.user, author))
    r.usage_count = (r.usage_count or 0) + 1
    r.last_used_at = utcnow()
    await ctx.session.commit()
    return {"text": text, "missing": missing,
            "missing_labels": [collab.VARIABLES[m] for m in missing]}


# ====================================================================== shared post helpers
async def _post(ctx: Context, pid: uuid.UUID) -> Post:
    p = await ctx.session.get(Post, pid)
    if p is None:
        raise _bad("Post not found.", 404)
    return p


def _person(u: User | None) -> dict | None:
    return {"id": u.id, "name": u.full_name, "email": u.email} if u else None


async def _users(ctx: Context, ids) -> dict[uuid.UUID, User]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u for u in (await ctx.session.scalars(select(User).where(User.id.in_(ids)))).all()}


@content_router.get("/members")
async def team_members(ctx: Context = Depends(require("content.view"))):
    """People in the organization, for @mentions in team comments."""
    users = await collab.members(ctx.session, ctx.org_id)
    return sorted([_person(u) for u in users], key=lambda u: u["name"].lower())


# ====================================================================== comments
class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    mentions: list[uuid.UUID] = Field(default_factory=list, max_length=30)


class CommentPatch(BaseModel):
    body: str | None = Field(None, min_length=1, max_length=5000)
    mentions: list[uuid.UUID] | None = Field(None, max_length=30)


class ResolveIn(BaseModel):
    resolved: bool = True


def _can_comment(ctx: Context) -> None:
    if not any(p in ctx.permissions for p in COMMENT_PERMS):
        raise _bad("Only people who write or approve posts can add team comments.", 403)


def comment_out(c: PostComment, users: dict[uuid.UUID, User], me: uuid.UUID) -> dict:
    return {
        "id": c.id, "post_id": c.post_id, "body": c.body, "author": _person(users.get(c.user_id)),
        "mentions": [_person(users.get(collab_uuid(m))) for m in (c.mentions or []) if users.get(collab_uuid(m))],
        "resolved": c.resolved, "resolved_by": _person(users.get(c.resolved_by)), "resolved_at": c.resolved_at,
        "edited_at": c.edited_at, "created_at": c.created_at, "mine": c.user_id == me,
    }


def collab_uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        return None


async def _valid_mentions(ctx: Context, ids: list[uuid.UUID]) -> list[str]:
    """Keep only active members of this organization (never the writer themselves)."""
    if not ids:
        return []
    allowed = {u.id for u in await collab.members(ctx.session, ctx.org_id)}
    return [str(i) for i in dict.fromkeys(ids) if i in allowed]


async def _comment(ctx: Context, pid: uuid.UUID, cid: uuid.UUID) -> PostComment:
    c = await ctx.session.get(PostComment, cid)
    if c is None or c.post_id != pid:
        raise _bad("That comment doesn't exist (it may have been deleted).", 404)
    return c


async def _comments_payload(ctx: Context, pid: uuid.UUID) -> dict:
    rows = list((await ctx.session.scalars(select(PostComment).where(PostComment.post_id == pid)
                                           .order_by(PostComment.created_at))).all())
    ids = {c.user_id for c in rows} | {c.resolved_by for c in rows} | {collab_uuid(m) for c in rows for m in (c.mentions or [])}
    users = await _users(ctx, ids)
    return {"items": [comment_out(c, users, ctx.user.id) for c in rows],
            "open": sum(1 for c in rows if not c.resolved), "total": len(rows),
            "can_comment": any(p in ctx.permissions for p in COMMENT_PERMS)}


async def _send_comment_notices(ctx: Context, post: Post, c: PostComment, new_mentions: list[str], *, edited: bool) -> None:
    """@mentioned people get a "mention" notice (in-app + email by default); the post's writer and approvers get
    "post_comment" (in-app). Never the commenter themselves. Called after commit."""
    who = ctx.user.full_name or ctx.user.email
    label = content_svc.platform_of(post.platform).label
    link = f"/posts/{post.id}#comments"
    body = f"{label} post: {collab.snippet(post.title or post.text or post.topic or 'Untitled', 120)}\n\n“{collab.snippet(c.body, 400)}”"
    me = ctx.user.id
    mentioned = [u for u in (collab_uuid(m) for m in new_mentions) if u and u != me]
    try:
        if mentioned:
            await notify_users(ctx.org_id, mentioned, "mention", f"{who} mentioned you in a comment", body, link=link)
        if edited:
            return
        others = set(await _users_with_permission(ctx.session, "content.approve"))
        if post.created_by:
            others.add(post.created_by)
        others -= {me, *mentioned}
        if others:
            await notify_users(ctx.org_id, list(others), "post_comment", f"{who} commented on a {label} post", body, link=link)
    except Exception:  # noqa: BLE001
        log.exception("comment notification failed")


@content_router.get("/posts/{pid}/comments")
async def list_comments(pid: uuid.UUID, ctx: Context = Depends(require("content.view"))):
    await _post(ctx, pid)
    return await _comments_payload(ctx, pid)


@content_router.post("/posts/{pid}/comments", status_code=201)
async def add_comment(pid: uuid.UUID, body: CommentIn, ctx: Context = Depends(require("content.view"))):
    _can_comment(ctx)
    post = await _post(ctx, pid)
    text = body.body.strip()
    if not text:
        raise _bad("Write a comment first.")
    mentions = await _valid_mentions(ctx, body.mentions)
    c = PostComment(organization_id=ctx.org_id, post_id=post.id, user_id=ctx.user.id, body=text, mentions=mentions,
                    resolved=False)
    ctx.session.add(c)
    await ctx.session.flush()
    content_svc.add_activity(ctx.session, post, "commented", collab.snippet(text, 160), user=ctx.user)
    await ctx.session.commit()
    await _send_comment_notices(ctx, post, c, mentions, edited=False)
    return await _comments_payload(ctx, pid)


@content_router.patch("/posts/{pid}/comments/{cid}")
async def edit_comment(pid: uuid.UUID, cid: uuid.UUID, body: CommentPatch, ctx: Context = Depends(require("content.view"))):
    post = await _post(ctx, pid)
    c = await _comment(ctx, pid, cid)
    if c.user_id != ctx.user.id:
        raise _bad("You can only edit your own comments.", 403)
    new_mentions: list[str] = []
    if body.body is not None:
        text = body.body.strip()
        if not text:
            raise _bad("A comment can't be empty. Delete it instead.")
        if text != c.body:
            c.body, c.edited_at = text, utcnow()
    if body.mentions is not None:
        mentions = await _valid_mentions(ctx, body.mentions)
        new_mentions = [m for m in mentions if m not in (c.mentions or [])]
        c.mentions = mentions
    await ctx.session.commit()
    if new_mentions:
        await _send_comment_notices(ctx, post, c, new_mentions, edited=True)
    return await _comments_payload(ctx, pid)


@content_router.delete("/posts/{pid}/comments/{cid}")
async def delete_comment(pid: uuid.UUID, cid: uuid.UUID, ctx: Context = Depends(require("content.view"))):
    await _post(ctx, pid)
    c = await _comment(ctx, pid, cid)
    if c.user_id != ctx.user.id:
        raise _bad("You can only delete your own comments.", 403)
    await ctx.session.delete(c)
    await ctx.session.commit()
    return await _comments_payload(ctx, pid)


@content_router.post("/posts/{pid}/comments/{cid}/resolve")
async def resolve_comment(pid: uuid.UUID, cid: uuid.UUID, body: ResolveIn | None = None,
                          ctx: Context = Depends(require("content.view"))):
    _can_comment(ctx)
    await _post(ctx, pid)
    c = await _comment(ctx, pid, cid)
    resolved = body.resolved if body else True
    c.resolved = resolved
    c.resolved_by, c.resolved_at = (ctx.user.id, utcnow()) if resolved else (None, None)
    await ctx.session.commit()
    return await _comments_payload(ctx, pid)


# ====================================================================== versions
def version_out(v: PostVersion, users: dict[uuid.UUID, User], prev: PostVersion | None, current: int) -> dict:
    u = users.get(v.changed_by)
    return {"version": v.version, "fields": v.fields or {}, "reason": v.reason,
            "changed_by": _person(u), "changed_by_name": u.full_name if u else ("AI" if (v.reason or "").startswith("AI") else "System"),
            "created_at": v.created_at, "changed": collab.changed_fields(prev.fields if prev else None, v.fields),
            "current": v.version == current}


@content_router.get("/posts/{pid}/versions")
async def list_versions(pid: uuid.UUID, ctx: Context = Depends(require("content.view"))):
    post = await _post(ctx, pid)
    rows = list((await ctx.session.scalars(select(PostVersion).where(PostVersion.post_id == pid)
                                           .order_by(PostVersion.version))).all())
    users = await _users(ctx, {v.changed_by for v in rows})
    current = rows[-1].version if rows and not collab.changed_fields(rows[-1].fields, collab.snapshot(post)) else -1
    out = [version_out(v, users, rows[i - 1] if i else None, current) for i, v in enumerate(rows)]
    out.reverse()
    return {"items": out, "can_restore": "content.create" in ctx.permissions and post.status not in collab.RESTORE_BLOCKED,
            "current_fields": collab.snapshot(post)}


@content_router.post("/posts/{pid}/versions/{n}/restore")
async def restore_version(pid: uuid.UUID, n: int, ctx: Context = Depends(require("content.create"))):
    """Put an earlier version's content back. Not for published posts. Re-runs the quality checks like an edit."""
    post = await _post(ctx, pid)
    if post.status in ("published", "publishing"):
        raise _bad("Published posts can't be changed here. Edit it on the platform instead.", 409)
    if post.status in ("planned", "generating"):
        raise _bad("This post is still being written. Wait until it's ready, then restore a version.", 409)
    v = await ctx.session.scalar(select(PostVersion).where(PostVersion.post_id == pid, PostVersion.version == n))
    if v is None:
        raise _bad("That version doesn't exist.", 404)
    before = collab.snapshot(post)
    f = v.fields or {}
    post.title = f.get("title") or None
    post.text = f.get("text") or ""
    post.hashtags = list(f.get("hashtags") or [])
    post.link_url = f.get("link_url") or None
    post.first_comment = f.get("first_comment") or None
    wanted = [str(m) for m in (f.get("media_ids") or [])]
    dropped = 0
    if wanted:
        ids = [collab_uuid(m) for m in wanted]
        found = {str(i) for i in (await ctx.session.scalars(select(MediaAsset.id).where(MediaAsset.id.in_([i for i in ids if i])))).all()}
        dropped = len([m for m in wanted if m not in found])
        wanted = [m for m in wanted if m in found]
    post.media_ids = wanted
    content_svc.set_meta(post, keep_media=bool(wanted))
    changed = collab.changed_fields(before, collab.snapshot(post))
    if not changed:
        return {"message": "The post already matches this version.", "restored": False}
    needs_approval = post.status in ("scheduled", "approved") and "content.approve" not in ctx.permissions
    if needs_approval:
        post.status, post.approved_by, post.approved_at = "in_review", None, None
        post.approval_deadline = post.scheduled_at
        content_svc.add_warning(post, "Changed after approval, so it needs approving again.")
    await content_svc.recheck_post(ctx.session, post)
    await collab.record_change(ctx.session, post, before, f"Restored version {n}", ctx.user)
    content_svc.add_activity(ctx.session, post, "restored", f"Restored version {n}", user=ctx.user)
    audit.record(ctx.session, action="post.version_restored", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="post", entity_id=post.id, summary=f"Restored version {n} of a post",
                 details={"version": n, "fields": changed}, ip=ctx.ip)
    await ctx.session.commit()
    msg = f"Version {n} restored."
    if dropped:
        msg += f" {dropped} media file(s) from that version were deleted from the library, so they weren't put back."
    if needs_approval:
        msg += " It needs approving again."
    return {"message": msg, "restored": True}

