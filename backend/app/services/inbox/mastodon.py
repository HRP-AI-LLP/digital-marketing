"""Mastodon: GET /api/v1/notifications?types[]=mention (read:notifications); replies with POST /api/v1/statuses
and in_reply_to_id (write:statuses); likes with POST /api/v1/statuses/{id}/favourite (write:favourites)."""

import hashlib

import httpx

from app.models import SocialAccount
from app.models.inbox import InboxItem
from app.services.inbox.base import CollectContext, Incoming, InboxPermissionError, html_to_text, parse_time
from app.services.social import common, net
from app.services.social import mastodon as md


def check(r: httpx.Response, what: str, permission: str) -> dict:
    if r.status_code == 403:
        raise InboxPermissionError("mastodon", permission, what)
    return common.ok_json(r, what)


def status_to_incoming(st: dict, *, kind: str, own_id: str | None) -> Incoming:
    acc = st.get("account") or {}
    acct = acc.get("acct")
    eng = sum(int(st.get(k) or 0) for k in ("favourites_count", "reblogs_count", "replies_count"))
    return Incoming(
        external_id=str(st["id"]), kind=kind, text=html_to_text(st.get("content")),
        published_at=parse_time(st.get("created_at")), author_name=acc.get("display_name") or acct,
        author_handle=f"@{acct}" if acct else None, author_avatar_url=acc.get("avatar"), author_url=acc.get("url"),
        author_external_id=str(acc.get("id")) if acc.get("id") else None, url=st.get("url") or st.get("uri"),
        thread_key=str(st["id"]), is_own=bool(own_id) and str(acc.get("id")) == own_id, language=st.get("language"),
        engagement=eng or None, raw={"status": {k: st.get(k) for k in ("id", "uri", "url", "in_reply_to_id",
                                                                      "visibility", "language")}, "acct": acct},
    )


async def collect(session, account: SocialAccount, ctx: CollectContext) -> list[Incoming]:
    inst, tok = md._ctx(account)
    own_id = (account.external_id or "").rsplit(":", 1)[-1]
    async with net.client() as c:
        d = check(await c.get(f"{inst}/api/v1/notifications", params=[("types[]", "mention"), ("limit", "40")],
                              headers=md._auth(tok)), "Mastodon notifications", "read:notifications")
    rows = d if isinstance(d, list) else d.get("data", [])
    out: list[Incoming] = []
    for n in rows:
        st = n.get("status")
        if not st or not st.get("id"):
            continue
        it = status_to_incoming(st, kind="mention", own_id=own_id)
        if it.published_at and it.published_at < ctx.since:
            continue
        parent = st.get("in_reply_to_id")
        if parent:
            parent = str(parent)
            if parent in ctx.posts:
                it.kind, it.external_post_id = "comment", parent
            else:
                it.kind, it.parent_external_id, it.thread_key = "reply", parent, parent
        out.append(it)
    return out


async def reply(session, account: SocialAccount, item: InboxItem, text: str, state: dict) -> tuple[str, str | None]:
    inst, tok = md._ctx(account)
    acct = (item.raw or {}).get("acct") or (item.author_handle or "").lstrip("@")
    body_text = text if not acct or f"@{acct}".lower() in text.lower() else f"@{acct} {text}"
    # Answer with the same audience as the post being answered (never wider).
    visibility = ((item.raw or {}).get("status") or {}).get("visibility") or "public"
    if visibility not in ("public", "unlisted", "private", "direct"):
        visibility = "public"
    key = hashlib.sha256(f"{item.id}:{body_text}".encode()).hexdigest()[:32]
    async with net.client() as c:
        d = check(await c.post(f"{inst}/api/v1/statuses", json={
            "status": body_text, "in_reply_to_id": item.external_id, "visibility": visibility},
            headers={**md._auth(tok), "Idempotency-Key": key}),
            "Replying on Mastodon", "write:statuses")
    return str(d["id"]), d.get("url")


async def like(session, account: SocialAccount, item: InboxItem, state: dict) -> None:
    inst, tok = md._ctx(account)
    async with net.client() as c:
        check(await c.post(f"{inst}/api/v1/statuses/{item.external_id}/favourite", headers=md._auth(tok)),
              "Liking on Mastodon", "write:favourites")


async def hashtag_timeline(account: SocialAccount, tag: str, limit: int = 20) -> list[dict]:
    inst, tok = md._ctx(account)
    async with net.client() as c:
        d = common.ok_json(await c.get(f"{inst}/api/v1/timelines/tag/{tag}", params={"limit": limit},
                                       headers=md._auth(tok)), "Mastodon hashtag")
    return d if isinstance(d, list) else []


async def search_statuses(account: SocialAccount, query: str, limit: int = 20) -> list[dict]:
    """Full-text status search. Many servers only search posts you've interacted with; empty results are normal."""
    inst, tok = md._ctx(account)
    async with net.client() as c:
        d = common.ok_json(await c.get(f"{inst}/api/v2/search", params={
            "q": query, "type": "statuses", "limit": limit, "resolve": "false"}, headers=md._auth(tok)),
            "Mastodon search")
    return d.get("statuses", []) if isinstance(d, dict) else []
