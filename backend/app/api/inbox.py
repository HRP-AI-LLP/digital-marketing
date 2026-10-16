"""Unified inbox: comments, replies and mentions from connected accounts, and social listening.

All queries go through ctx.session, which row-level security scopes to the caller's organization.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update

from app.core.deps import Context, require
from app.models import Membership, MembershipRole, Post, RolePermission, SocialAccount, User
from app.models.inbox import SENTIMENTS, InboxItem
from app.services import audit
from app.services.inbox import listening as listen_svc
from app.services.inbox.base import (
    CAPABILITIES, LISTEN_REPLY_PLATFORMS, LISTENING_SOURCES, OPEN_ONLY_PLATFORMS, SYNC_DAYS, SYNC_PLATFORMS,
    permission_help,
)
from app.services.inbox.suggest import reply_limit, suggest_replies
from app.services.inbox.sync import (
    InboxActionError, can_like, can_reply, get_settings, send_like, send_reply, thread_of,
)
from app.services.jobs import enqueue
from app.services.llm.router import AllProvidersFailed, NoProviderError

router = APIRouter(prefix="/inbox", tags=["inbox"])

SOURCE_LABELS = {"web": "Web", "news": "News"}


def _label(platform: str) -> str:
    if platform in CAPABILITIES:
        return CAPABILITIES[platform].label
    return SOURCE_LABELS.get(platform) or platform.replace("_", " ").capitalize()


def _account_out(a: SocialAccount | None) -> dict | None:
    if a is None:
        return None
    return {"id": a.id, "platform": a.platform, "platform_label": _label(a.platform), "name": a.name,
            "handle": a.handle, "avatar_url": a.avatar_url, "status": a.status, "profile_url": a.profile_url}


def _item_out(i: InboxItem, users: dict[uuid.UUID, User], accounts: dict[uuid.UUID, SocialAccount]) -> dict:
    u = users.get(i.assigned_to) if i.assigned_to else None
    ok, _ = can_reply(i)
    return {
        "id": i.id, "source": i.source, "platform": i.platform, "platform_label": _label(i.platform), "kind": i.kind,
        "account": _account_out(accounts.get(i.social_account_id)) if i.social_account_id else None,
        "post_id": i.post_id, "external_post_url": i.external_post_url, "post_snippet": i.post_snippet,
        "external_id": i.external_id, "parent_id": i.parent_id, "thread_key": i.thread_key, "url": i.url,
        "author": {"name": i.author_name, "handle": i.author_handle, "avatar_url": i.author_avatar_url, "url": i.author_url},
        "text": i.text, "title": i.title, "published_at": i.published_at, "created_at": i.created_at,
        "language": i.language, "is_own": i.is_own, "status": i.status,
        "assigned_to": {"id": u.id, "name": u.full_name} if u else None,
        "sentiment": i.sentiment, "sentiment_source": i.sentiment_source, "priority": i.priority,
        "keyword": i.keyword, "engagement": i.engagement, "ai_suggestions": i.ai_suggestions or [],
        "reply_text": i.reply_text, "replied_at": i.replied_at, "reply_error": i.reply_error, "liked_at": i.liked_at,
        "can_reply": ok, "can_like": can_like(i),
    }


async def _lookups(ctx: Context, items: list[InboxItem]) -> tuple[dict, dict]:
    uids = {i.assigned_to for i in items if i.assigned_to}
    aids = {i.social_account_id for i in items if i.social_account_id}
    users = {u.id: u for u in (await ctx.session.scalars(select(User).where(User.id.in_(uids)))).all()} if uids else {}
    accs = {a.id: a for a in (await ctx.session.scalars(
        select(SocialAccount).where(SocialAccount.id.in_(aids)))).all()} if aids else {}
    return users, accs


async def _get(ctx: Context, item_id: uuid.UUID) -> InboxItem:
    it = await ctx.session.get(InboxItem, item_id)
    if it is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That inbox item doesn't exist (it may have been removed).")
    return it


def _open_items():
    return select(func.count()).select_from(InboxItem).where(InboxItem.is_own.is_(False))


# ------------------------------------------------------------------ summary (nav badge)
@router.get("/summary")
async def summary(ctx: Context = Depends(require("inbox.view"))):
    new = InboxItem.status == "new"
    comments = await ctx.session.scalar(_open_items().where(new, InboxItem.kind != "mention"))
    mentions = await ctx.session.scalar(_open_items().where(new, InboxItem.kind == "mention"))
    urgent = await ctx.session.scalar(_open_items().where(
        InboxItem.status.in_(["new", "read"]), or_(InboxItem.priority.is_(True), InboxItem.sentiment == "negative")))
    mine = await ctx.session.scalar(_open_items().where(
        InboxItem.status.in_(["new", "read"]), InboxItem.assigned_to == ctx.user.id))
    return {"unread": (comments or 0) + (mentions or 0), "comments": comments or 0, "mentions": mentions or 0,
            "urgent": urgent or 0, "assigned_to_me": mine or 0}


# ------------------------------------------------------------------ list & detail
@router.get("/items")
async def list_items(
    tab: Literal["comments", "mentions", "all"] = "comments", platform: str | None = None,
    account_id: uuid.UUID | None = None, status_: str | None = Query(None, alias="status"),
    sentiment: str | None = None, assigned: str | None = None, priority: bool | None = None,
    source: Literal["account", "listening"] | None = None, keyword: str | None = None, q: str | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ctx: Context = Depends(require("inbox.view")),
):
    conds = [InboxItem.is_own.is_(False)]
    if tab == "comments":
        conds.append(InboxItem.kind != "mention")
    elif tab == "mentions":
        conds.append(InboxItem.kind == "mention")
    if platform:
        conds.append(InboxItem.platform == platform)
    if account_id:
        conds.append(InboxItem.social_account_id == account_id)
    if status_ in ("new", "read", "replied", "archived"):
        conds.append(InboxItem.status == status_)
    elif status_ == "open":
        conds.append(InboxItem.status.in_(["new", "read"]))
    elif status_ != "all":
        conds.append(InboxItem.status != "archived")
    if sentiment == "urgent":
        conds.append(or_(InboxItem.priority.is_(True), InboxItem.sentiment == "negative"))
    elif sentiment in SENTIMENTS:
        conds.append(InboxItem.sentiment == sentiment)
    if assigned == "me":
        conds.append(InboxItem.assigned_to == ctx.user.id)
    elif assigned == "none":
        conds.append(InboxItem.assigned_to.is_(None))
    elif assigned:
        try:
            conds.append(InboxItem.assigned_to == uuid.UUID(assigned))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "assigned must be me, none or a user id.")
    if priority is not None:
        conds.append(InboxItem.priority.is_(priority))
    if source:
        conds.append(InboxItem.source == source)
    if keyword:
        conds.append(InboxItem.keyword == keyword)
    if q and q.strip():
        like = f"%{q.strip()[:100]}%"
        conds.append(or_(InboxItem.text.ilike(like), InboxItem.title.ilike(like), InboxItem.author_name.ilike(like),
                         InboxItem.author_handle.ilike(like)))
    total = await ctx.session.scalar(select(func.count()).select_from(InboxItem).where(*conds))
    rows = list((await ctx.session.scalars(select(InboxItem).where(*conds).order_by(
        func.coalesce(InboxItem.published_at, InboxItem.created_at).desc(), InboxItem.id).limit(limit).offset(offset))).all())
    users, accs = await _lookups(ctx, rows)
    return {"items": [_item_out(i, users, accs) for i in rows], "total": total or 0}


async def _detail(ctx: Context, it: InboxItem) -> dict:
    thread = await thread_of(ctx.session, it)
    users, accs = await _lookups(ctx, [*thread, it])
    post = await ctx.session.get(Post, it.post_id) if it.post_id else None
    ok, why = can_reply(it)
    settings = await get_settings(ctx.session, ctx.org_id)
    st = ((settings.state or {}).get("accounts") or {}).get(str(it.social_account_id)) or {}
    perm_issue = None
    if st.get("reply_missing_permission"):
        perm_issue = {"permission": st["reply_missing_permission"], "message": st.get("reply_message")}
    elif st.get("missing_permission"):
        perm_issue = {"permission": st["missing_permission"], "message": st.get("message")}
    return {
        "item": _item_out(it, users, accs),
        "thread": [_item_out(t, users, accs) for t in thread],
        "post": {"id": post.id, "text": post.text, "title": post.title, "external_url": post.external_url,
                 "published_at": post.published_at, "platform": post.platform} if post else None,
        "can_reply": ok, "reply_blocked_reason": why, "reply_limit": reply_limit(it.platform) if ok else None,
        "can_like": can_like(it), "permission_issue": perm_issue,
    }


@router.get("/items/{item_id}")
async def get_item(item_id: uuid.UUID, ctx: Context = Depends(require("inbox.view"))):
    return await _detail(ctx, await _get(ctx, item_id))


class ItemPatch(BaseModel):
    status: Literal["new", "read", "replied", "archived"] | None = None
    assigned_to: uuid.UUID | None = None
    priority: bool | None = None
    sentiment: Literal["positive", "neutral", "negative", "question"] | None = None


async def _check_member(ctx: Context, user_id: uuid.UUID) -> None:
    m = await ctx.session.scalar(select(Membership).where(
        Membership.user_id == user_id, Membership.organization_id == ctx.org_id, Membership.is_active.is_(True)))
    if m is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That person isn't a member of this organization.")


@router.patch("/items/{item_id}")
async def update_item(item_id: uuid.UUID, body: ItemPatch, ctx: Context = Depends(require("inbox.reply"))):
    it = await _get(ctx, item_id)
    fields = body.model_fields_set
    if "status" in fields and body.status:
        it.status = body.status
    if "assigned_to" in fields:
        if body.assigned_to:
            await _check_member(ctx, body.assigned_to)
        it.assigned_to = body.assigned_to
    if "priority" in fields and body.priority is not None:
        it.priority = body.priority
    if "sentiment" in fields and body.sentiment:
        it.sentiment, it.sentiment_source = body.sentiment, "manual"
    await ctx.session.commit()
    return await _detail(ctx, it)


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)
    action: Literal["read", "unread", "archive", "unarchive"]


@router.post("/items/bulk")
async def bulk(body: BulkIn, ctx: Context = Depends(require("inbox.reply"))):
    base = update(InboxItem).where(InboxItem.id.in_(body.ids), InboxItem.is_own.is_(False))
    if body.action == "read":
        q = base.where(InboxItem.status == "new").values(status="read")
    elif body.action == "unread":
        q = base.where(InboxItem.status.in_(["read", "archived"])).values(status="new")
    elif body.action == "archive":
        q = base.values(status="archived")
    else:
        q = base.where(InboxItem.status == "archived").values(status="read")
    r = await ctx.session.execute(q.execution_options(synchronize_session=False))
    await ctx.session.commit()
    return {"updated": r.rowcount or 0}


# ------------------------------------------------------------------ reply, suggest, like
class ReplyIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)


@router.post("/items/{item_id}/reply")
async def reply(item_id: uuid.UUID, body: ReplyIn, ctx: Context = Depends(require("inbox.reply"))):
    it = await _get(ctx, item_id)
    try:
        await send_reply(ctx.session, ctx.org_id, it, body.text, ctx.user)
    except InboxActionError as e:
        await ctx.session.commit()  # keep the recorded error on the item
        detail = e.message
        raise HTTPException(e.status, detail)
    audit.record(ctx.session, action="inbox.reply", organization_id=ctx.org_id, actor=ctx.user, entity_type="inbox_item",
                 entity_id=it.id, summary=f"Replied on {_label(it.platform)} to {it.author_name or 'a comment'}",
                 details={"platform": it.platform, "reply_external_id": it.reply_external_id})
    await ctx.session.commit()
    return await _detail(ctx, it)


@router.post("/items/{item_id}/suggest")
async def suggest(item_id: uuid.UUID, ctx: Context = Depends(require("inbox.reply"))):
    it = await _get(ctx, item_id)
    thread = await thread_of(ctx.session, it)
    post = await ctx.session.get(Post, it.post_id) if it.post_id else None
    try:
        options = await suggest_replies(ctx.session, it, thread, post.text if post else None)
    except NoProviderError as e:
        await ctx.session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except AllProvidersFailed as e:
        await ctx.session.commit()  # keep usage logs
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:500])
    except ValueError:
        await ctx.session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI model didn't return usable suggestions. Try again.")
    if not options:
        await ctx.session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI model didn't return usable suggestions. Try again.")
    it.ai_suggestions = options
    await ctx.session.commit()
    return {"suggestions": options}


@router.post("/items/{item_id}/like")
async def like(item_id: uuid.UUID, ctx: Context = Depends(require("inbox.reply"))):
    it = await _get(ctx, item_id)
    try:
        await send_like(ctx.session, ctx.org_id, it)
    except InboxActionError as e:
        await ctx.session.commit()
        raise HTTPException(e.status, e.message)
    await ctx.session.commit()
    return {"liked_at": it.liked_at}


# ------------------------------------------------------------------ refresh
class RefreshIn(BaseModel):
    what: Literal["comments", "mentions", "all"] = "all"


@router.post("/refresh")
async def refresh(body: RefreshIn | None = None, ctx: Context = Depends(require("inbox.reply"))):
    what = (body or RefreshIn()).what
    jobs = {}
    if what in ("comments", "all"):
        j = await enqueue(ctx.session, "inbox.sync", ctx.org_id, {}, dedupe_key=f"inbox.sync:{ctx.org_id}",
                          max_attempts=1, priority=3, created_by=ctx.user.id)
        jobs["comments"] = j.id
    if what in ("mentions", "all"):
        settings = await get_settings(ctx.session, ctx.org_id)
        if settings.keywords:
            j = await enqueue(ctx.session, "inbox.listen", ctx.org_id, {"force": True},
                              dedupe_key=f"inbox.listen:{ctx.org_id}", max_attempts=1, priority=3, created_by=ctx.user.id)
            jobs["mentions"] = j.id
    await ctx.session.commit()
    return {"jobs": jobs}


# ------------------------------------------------------------------ status: what's connected and what's missing
@router.get("/status")
async def inbox_status(ctx: Context = Depends(require("inbox.view"))):
    settings = await get_settings(ctx.session, ctx.org_id)
    await ctx.session.commit()
    state = settings.state or {}
    accs_state = state.get("accounts") or {}
    rows = (await ctx.session.scalars(select(SocialAccount).where(
        SocialAccount.status != "removed", SocialAccount.platform.in_(list(CAPABILITIES))).order_by(
        SocialAccount.platform, SocialAccount.name))).all()
    accounts = []
    for a in rows:
        cap = CAPABILITIES[a.platform]
        st = accs_state.get(str(a.id)) or {}
        missing = st.get("missing_permission") or st.get("reply_missing_permission")
        accounts.append({
            **_account_out(a), "sync": cap.sync, "reply": cap.reply, "like": cap.like, "note": cap.note,
            "last_synced_at": st.get("last_synced_at"), "ok": st.get("ok") if st else None,
            "message": st.get("message") or st.get("reply_message"), "missing_permission": missing,
            "permission_help": permission_help(a.platform, missing) if missing else None,
            "reconnect": bool(st.get("reconnect") or st.get("reply_missing_permission")),
            "read_permissions": list(cap.read_permissions), "reply_permissions": list(cap.reply_permissions),
            "notes": st.get("notes") or [],
        })
    # Platforms without comment APIs: link to each recent post instead.
    since = datetime.now(UTC) - timedelta(days=SYNC_DAYS)
    open_rows = (await ctx.session.execute(select(Post, SocialAccount).join(
        SocialAccount, SocialAccount.id == Post.social_account_id).where(
        SocialAccount.platform.in_(OPEN_ONLY_PLATFORMS), Post.status == "published", Post.external_url.is_not(None),
        or_(Post.published_at.is_(None), Post.published_at >= since)).order_by(Post.published_at.desc()).limit(30))).all()
    open_posts = [{"post_id": p.id, "platform": a.platform, "platform_label": _label(a.platform), "account": a.name,
                   "text": (p.text or p.title or "")[:200], "url": p.external_url, "published_at": p.published_at}
                  for p, a in open_rows]
    src_state = state.get("sources") or {}
    connected = {a.platform for a in rows if a.status == "connected"}
    sources = []
    enabled = {**listen_svc.DEFAULT_SOURCES, **(settings.sources or {})}
    for sid, spec in LISTENING_SOURCES.items():
        s = src_state.get(sid) or {}
        sources.append({"id": sid, "label": spec["label"], "enabled": bool(enabled.get(sid)),
                        "available": spec["needs"] is None or spec["needs"] in connected,
                        "needs": spec["needs"], "interval_minutes": spec["interval_minutes"],
                        "can_reply": sid in LISTEN_REPLY_PLATFORMS, "last_run_at": s.get("last_run_at"),
                        "ok": s.get("ok"), "message": s.get("message"), "found": s.get("found")})
    return {"accounts": accounts, "open_posts": open_posts, "sources": sources, "sync_platforms": SYNC_PLATFORMS,
            "keywords": settings.keywords or [], "last_digest_at": state.get("last_digest_at")}


# ------------------------------------------------------------------ listening settings
def _clean_list(v: list[str], max_items: int, max_len: int) -> list[str]:
    out: list[str] = []
    for x in v or []:
        s = " ".join(str(x).split())[:max_len]
        if s and s.lower() not in {o.lower() for o in out}:
            out.append(s)
    return out[:max_items]


class ListeningIn(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=50)
    excluded: list[str] = Field(default_factory=list, max_length=100)
    languages: list[str] = Field(default_factory=list, max_length=10)
    sources: dict[str, bool] = Field(default_factory=dict)
    listening_enabled: bool = True

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        for x in v:
            if not str(x).strip().replace("-", "").isalpha() or len(str(x).strip()) > 8:
                raise ValueError("Use language codes such as en or hi.")
        return [str(x).strip().lower() for x in v]


def _listening_out(s) -> dict:
    return {"keywords": s.keywords or [], "excluded": s.excluded or [], "languages": s.languages or [],
            "sources": {**listen_svc.DEFAULT_SOURCES, **(s.sources or {})}, "listening_enabled": s.listening_enabled,
            "max_keywords": listen_svc.MAX_KEYWORDS,
            "source_info": {k: {"label": v["label"], "interval_minutes": v["interval_minutes"], "needs": v["needs"]}
                            for k, v in LISTENING_SOURCES.items()}}


@router.get("/listening")
async def get_listening(ctx: Context = Depends(require("inbox.view"))):
    s = await get_settings(ctx.session, ctx.org_id)
    await ctx.session.commit()
    return _listening_out(s)


@router.put("/listening")
async def put_listening(body: ListeningIn, ctx: Context = Depends(require("inbox.manage"))):
    s = await get_settings(ctx.session, ctx.org_id)
    before = list(s.keywords or [])
    s.keywords = _clean_list(body.keywords, listen_svc.MAX_KEYWORDS * 2, 100)
    s.excluded = _clean_list(body.excluded, 100, 100)
    s.languages = _clean_list(body.languages, 10, 8)
    s.sources = {k: bool(v) for k, v in body.sources.items() if k in LISTENING_SOURCES}
    s.listening_enabled = body.listening_enabled
    if s.keywords != before:  # new keywords: search everything again soon
        state = dict(s.state or {})
        state["sources"] = {}
        s.state = state
    audit.record(ctx.session, action="inbox.listening_updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="inbox_settings", entity_id=s.id, summary="Changed listening keywords",
                 details={"keywords": s.keywords, "excluded": s.excluded})
    await ctx.session.commit()
    return _listening_out(s)


# ------------------------------------------------------------------ people to assign
@router.get("/assignees")
async def assignees(ctx: Context = Depends(require("inbox.view"))):
    q = (select(User).join(Membership, Membership.user_id == User.id)
         .join(MembershipRole, MembershipRole.membership_id == Membership.id)
         .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
         .where(Membership.organization_id == ctx.org_id, Membership.is_active.is_(True), User.is_active.is_(True),
                RolePermission.permission_code == "inbox.reply").distinct())
    users = (await ctx.session.scalars(q)).all()
    return sorted([{"id": u.id, "name": u.full_name, "email": u.email} for u in users], key=lambda u: u["name"].lower())
