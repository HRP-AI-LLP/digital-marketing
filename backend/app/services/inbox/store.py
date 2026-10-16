"""Store collected items: de-duplicate (unique per organization + dedupe key), link to our posts, thread replies
under their parents and mark comments answered when our own reply shows up."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post, SocialAccount
from app.models.inbox import InboxItem
from app.services.inbox.base import Incoming, clip, normalize_url

UPDATABLE = ("text", "engagement", "author_avatar_url", "author_name", "url")


def account_key(account_id: uuid.UUID, external_id: str) -> str:
    return f"{account_id}:{external_id}"[:600]


def listening_key(platform: str, it: Incoming) -> str:
    url = normalize_url(it.url)
    return (f"listen:url:{url}" if url else f"listen:{platform}:{it.external_id}")[:600]


async def _existing(session: AsyncSession, keys: list[str]) -> dict[str, InboxItem]:
    out: dict[str, InboxItem] = {}
    for i in range(0, len(keys), 500):
        rows = (await session.scalars(select(InboxItem).where(InboxItem.dedupe_key.in_(keys[i:i + 500])))).all()
        out.update({r.dedupe_key: r for r in rows})
    return out


async def store(
    session: AsyncSession, org_id: uuid.UUID, items: list[Incoming], *, account: SocialAccount | None,
    source: str = "account", platform: str | None = None,
) -> list[InboxItem]:
    """Insert new items and refresh existing ones. Returns the newly created rows."""
    if not items:
        return []
    batch: dict[str, Incoming] = {}
    for it in items:
        if not it.external_id and not it.url:
            continue
        p = it.platform or platform or (account.platform if account else "web")
        it.platform = p
        key = account_key(account.id, it.external_id) if source == "account" and account else listening_key(p, it)
        batch.setdefault(key, it)

    if source == "listening":
        # Skip our own posts and anything already in the inbox through a connected account.
        batch = {k: v for k, v in batch.items() if not v.is_own}
        ext = [v.external_id for v in batch.values() if v.external_id]
        if ext:
            seen = set((await session.scalars(select(InboxItem.external_id).where(
                InboxItem.source == "account", InboxItem.external_id.in_(ext)))).all())
            batch = {k: v for k, v in batch.items() if v.external_id not in seen}
    if not batch:
        return []

    existing = await _existing(session, list(batch))
    posts: dict[str, Post] = {}
    post_ids = {v.external_post_id for v in batch.values() if v.external_post_id}
    if account is not None and post_ids:
        rows = (await session.scalars(select(Post).where(
            Post.social_account_id == account.id, Post.external_id.in_(list(post_ids))))).all()
        posts = {p.external_id: p for p in rows}

    created: list[InboxItem] = []
    for key, it in batch.items():
        row = existing.get(key)
        if row is not None:
            for f in UPDATABLE:
                v = getattr(it, f)
                if v is not None and getattr(row, f) != v:
                    setattr(row, f, v)
            if it.post_snippet and not row.post_snippet:
                row.post_snippet = it.post_snippet
            continue
        post = posts.get(it.external_post_id or "")
        row = InboxItem(
            organization_id=org_id, source=source, platform=it.platform, kind=it.kind, dedupe_key=key,
            social_account_id=account.id if account else None, post_id=post.id if post else None,
            external_post_id=it.external_post_id, external_post_url=it.external_post_url or (post.external_url if post else None),
            post_snippet=it.post_snippet or (clip(post.text, 500) if post else None),
            external_id=it.external_id, parent_external_id=it.parent_external_id,
            thread_key=it.thread_key or it.external_id, url=it.url, author_name=clip(it.author_name, 300),
            author_handle=clip(it.author_handle, 300), author_avatar_url=it.author_avatar_url, author_url=it.author_url,
            author_external_id=it.author_external_id, text=it.text, title=it.title, published_at=it.published_at,
            language=(it.language or "")[:20] or None, is_own=it.is_own, status="read" if it.is_own else "new",
            keyword=clip(it.keyword, 200), engagement=it.engagement, ai_suggestions=[], raw=it.raw or {},
        )
        session.add(row)
        created.append(row)
    await session.flush()

    # Threading: hang replies under their parent and inherit its conversation.
    if account is not None:
        children = [r for r in created if r.parent_external_id]
        if children:
            keys = {account_key(account.id, r.parent_external_id) for r in children}
            parents = await _existing(session, list(keys))
            children.sort(key=lambda r: r.published_at or datetime.now(UTC))
            for r in children:
                parent = parents.get(account_key(account.id, r.parent_external_id))
                if parent is None or parent.id == r.id:
                    continue
                r.parent_id = parent.id
                r.thread_key = parent.thread_key or parent.external_id
                if not r.post_id and parent.post_id:
                    r.post_id, r.post_snippet = parent.post_id, r.post_snippet or parent.post_snippet
                if r.is_own and parent.status in ("new", "read") and not parent.is_own:
                    parent.status = "replied"
                    parent.replied_at = parent.replied_at or r.published_at
        await session.flush()
    return created
