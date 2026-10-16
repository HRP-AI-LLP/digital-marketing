"""Bluesky: app.bsky.notification.listNotifications (reply, mention, quote); replies through
com.atproto.repo.createRecord with reply root/parent refs; likes as app.bsky.feed.like records."""

from datetime import UTC, datetime

import httpx

from app.models import SocialAccount
from app.models.inbox import InboxItem
from app.services.inbox.base import CollectContext, Incoming, clip, parse_time
from app.services.social import bluesky as bs
from app.services.social import net
from app.services.social.common import PublishError, ok_json

KEEP_REASONS = {"reply": "comment", "mention": "mention", "quote": "mention"}


def post_url(handle_or_did: str, uri: str) -> str:
    return f"https://bsky.app/profile/{handle_or_did}/post/{uri.rsplit('/', 1)[-1]}"


def _auth(jwt: str) -> dict:
    return {"Authorization": f"Bearer {jwt}"}


def post_to_incoming(p: dict, *, kind: str, own_did: str | None) -> Incoming:
    """A post view (searchPosts / getPosts) or a notification as an Incoming item."""
    author = p.get("author") or {}
    rec = p.get("record") or {}
    uri = p["uri"]
    langs = rec.get("langs") or []
    eng = sum(int(p.get(k) or 0) for k in ("likeCount", "repostCount", "replyCount", "quoteCount"))
    return Incoming(
        external_id=uri, kind=kind, text=rec.get("text"), published_at=parse_time(rec.get("createdAt") or p.get("indexedAt")),
        author_name=author.get("displayName") or author.get("handle"), author_handle=author.get("handle"),
        author_avatar_url=author.get("avatar"), author_external_id=author.get("did"),
        author_url=f"https://bsky.app/profile/{author.get('handle') or author.get('did')}",
        url=post_url(author.get("handle") or author.get("did") or "", uri), thread_key=uri,
        is_own=bool(own_did) and author.get("did") == own_did, language=langs[0] if langs else None,
        engagement=eng or None, raw={"uri": uri, "cid": p.get("cid"), "record": rec, "reason": p.get("reason")},
    )


async def collect(session, account: SocialAccount, ctx: CollectContext) -> list[Incoming]:
    async with net.client() as c:
        tokens = await bs._session(c, account)
        svc, jwt = bs._service(account), tokens["access_jwt"]
        did = tokens.get("did") or account.external_id
        d = ok_json(await c.get(bs._xrpc(svc, "app.bsky.notification.listNotifications"), params={"limit": 50},
                                headers=_auth(jwt)), "Bluesky notifications")
        notes = [n for n in d.get("notifications", []) if n.get("reason") in KEEP_REASONS and n.get("uri")]
        out: list[Incoming] = []
        context_uris: set[str] = set()
        for n in notes:
            it = post_to_incoming(n, kind=KEEP_REASONS[n["reason"]], own_did=did)
            if it.published_at and it.published_at < ctx.since:
                continue
            rec = n.get("record") or {}
            reply = rec.get("reply") or {}
            if n["reason"] == "reply" and reply:
                root, parent = (reply.get("root") or {}).get("uri"), (reply.get("parent") or {}).get("uri")
                it.external_post_id = root
                if parent and parent != root:
                    it.kind, it.parent_external_id, it.thread_key = "reply", parent, parent
                if root:
                    context_uris.add(root)
            elif n["reason"] == "quote":
                quoted = ((rec.get("embed") or {}).get("record") or {}).get("uri")
                if quoted:
                    it.external_post_id = quoted
                    context_uris.add(quoted)
            out.append(it)
        # Text and links of the posts being answered or quoted, for context.
        posts: dict[str, dict] = {}
        uris = sorted(context_uris)[:25]
        if uris:
            try:
                pd = ok_json(await c.get(bs._xrpc(svc, "app.bsky.feed.getPosts"), params=[("uris", u) for u in uris],
                                         headers=_auth(jwt)), "Bluesky posts")
                posts = {p["uri"]: p for p in pd.get("posts", [])}
            except (PublishError, httpx.HTTPError):
                pass
        for it in out:
            p = posts.get(it.external_post_id or "")
            if p:
                it.post_snippet = clip((p.get("record") or {}).get("text"), 500)
                a = p.get("author") or {}
                it.external_post_url = post_url(a.get("handle") or a.get("did") or "", p["uri"])
    return out


async def _cid(c: httpx.AsyncClient, svc: str, jwt: str, uri: str) -> str:
    d = ok_json(await c.get(bs._xrpc(svc, "app.bsky.feed.getPosts"), params={"uris": uri}, headers=_auth(jwt)),
                "Bluesky post")
    posts = d.get("posts") or []
    if not posts or not posts[0].get("cid"):
        raise PublishError("That Bluesky post no longer exists.")
    return posts[0]["cid"]


async def reply(session, account: SocialAccount, item: InboxItem, text: str, state: dict) -> tuple[str, str | None]:
    raw = item.raw or {}
    uri = item.external_id
    async with net.client() as c:
        tokens = await bs._session(c, account)
        svc, jwt = bs._service(account), tokens["access_jwt"]
        did = tokens.get("did") or account.external_id
        cid = raw.get("cid") or await _cid(c, svc, jwt, uri)
        parent = {"uri": uri, "cid": cid}
        root = ((raw.get("record") or {}).get("reply") or {}).get("root") or parent
        record: dict = {"$type": "app.bsky.feed.post", "text": text, "langs": ["en"],
                        "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "reply": {"root": {"uri": root["uri"], "cid": root["cid"]}, "parent": parent}}
        f = bs.facets(text)
        if f:
            record["facets"] = f
        d = ok_json(await c.post(bs._xrpc(svc, "com.atproto.repo.createRecord"), headers=_auth(jwt),
                                 json={"repo": did, "collection": "app.bsky.feed.post", "record": record}),
                    "Replying on Bluesky")
    new_uri = d.get("uri")
    return new_uri, (post_url(account.handle or did, new_uri) if new_uri else None)


async def like(session, account: SocialAccount, item: InboxItem, state: dict) -> None:
    async with net.client() as c:
        tokens = await bs._session(c, account)
        svc, jwt = bs._service(account), tokens["access_jwt"]
        did = tokens.get("did") or account.external_id
        cid = (item.raw or {}).get("cid") or await _cid(c, svc, jwt, item.external_id)
        ok_json(await c.post(bs._xrpc(svc, "com.atproto.repo.createRecord"), headers=_auth(jwt), json={
            "repo": did, "collection": "app.bsky.feed.like",
            "record": {"$type": "app.bsky.feed.like", "subject": {"uri": item.external_id, "cid": cid},
                       "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z")}}), "Liking on Bluesky")


async def search(account: SocialAccount, query: str, *, lang: str | None = None, limit: int = 25) -> list[dict]:
    """app.bsky.feed.searchPosts with our session (newest first)."""
    params = {"q": query, "limit": limit, "sort": "latest"}
    if lang:
        params["lang"] = lang
    async with net.client() as c:
        tokens = await bs._session(c, account)
        d = ok_json(await c.get(bs._xrpc(bs._service(account), "app.bsky.feed.searchPosts"), params=params,
                                headers=_auth(tokens["access_jwt"])), "Bluesky search")
    return d.get("posts", [])
