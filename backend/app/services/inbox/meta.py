"""Facebook Page comments and Instagram comments / @mentions through the Graph API.

Facebook: GET /{page}/published_posts (with stream comment counts) → GET /{post}/comments?filter=stream for posts
whose count changed; reply with POST /{comment}/comments (pages_manage_engagement); like with POST /{comment}/likes.
Instagram: GET /{ig}/media (comments_count) → GET /{media}/comments with replies; reply with POST /{comment}/replies;
@mentions and tags via GET /{ig}/tags, answered with POST /{ig}/mentions (instagram_manage_comments).
"""

import httpx

from app.models import SocialAccount
from app.models.inbox import InboxItem
from app.services.inbox.base import (
    MAX_POSTS_PER_ACCOUNT, CollectContext, Incoming, InboxPermissionError, clip, parse_time,
)
from app.services.social import meta as graph_api
from app.services.social import net
from app.services.social.common import PublishError, account_tokens

GRAPH = graph_api.GRAPH
FB_COMMENT_FIELDS = "id,from,message,created_time,parent{id},comment_count,like_count,permalink_url"
IG_COMMENT_FIELDS = ("id,text,timestamp,username,like_count,from{id,username},"
                     "replies{id,text,timestamp,username,like_count,from{id,username}}")


def check(r: httpx.Response, what: str, platform: str, permission: str) -> dict:
    """Graph error mapping with permission errors (codes 3, 10, 200-299) turned into InboxPermissionError."""
    try:
        data = r.json() if r.content else {}
    except ValueError:
        data = {}
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        code = err.get("code")
        if isinstance(code, int) and (code in (3, 10) or 200 <= code <= 299):
            raise InboxPermissionError(platform, permission, what)
    return graph_api.check(r, what)


def _token(account: SocialAccount) -> str:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    return tok


async def granted_permissions(c: httpx.AsyncClient, account: SocialAccount) -> list[str] | None:
    """Permissions granted to the user token behind the Page (None when unknown)."""
    user_token = account_tokens(account).get("user_token")
    if not user_token:
        return None
    try:
        r = await c.get(f"{GRAPH}/me/permissions", params={"access_token": user_token})
        d = graph_api.check(r, "Permissions")
    except (PublishError, httpx.HTTPError):
        return None
    return sorted(p["permission"] for p in d.get("data", []) if p.get("status") == "granted" and p.get("permission"))


def _require(state: dict, platform: str, permission: str, what: str) -> None:
    granted = state.get("granted")
    if isinstance(granted, list) and granted and permission not in granted:
        raise InboxPermissionError(platform, permission, what)


# ------------------------------------------------------------------ Facebook
async def collect_facebook(session, account: SocialAccount, ctx: CollectContext) -> list[Incoming]:
    tok = _token(account)
    page = account.external_id
    out: list[Incoming] = []
    counts: dict = dict(ctx.state.get("counts") or {})
    async with net.client() as c:
        granted = await granted_permissions(c, account)
        if granted is not None:
            ctx.state["granted"] = granted
        d = check(await c.get(f"{GRAPH}/{page}/published_posts", params={
            "fields": "id,message,permalink_url,created_time,comments.filter(stream).summary(true).limit(0)",
            "since": int(ctx.since.timestamp()), "limit": MAX_POSTS_PER_ACCOUNT, "access_token": tok}),
            "Facebook posts", "facebook_page", "pages_read_engagement")
        posts = {p["id"]: p for p in d.get("data", []) if p.get("id")}
        for ext, ref in ctx.posts.items():  # our own posts, in case the Page list is incomplete
            posts.setdefault(ext, {"id": ext, "message": ref.text, "permalink_url": ref.url})
        new_counts: dict = {}
        for pid, p in list(posts.items())[:MAX_POSTS_PER_ACCOUNT]:
            total = ((p.get("comments") or {}).get("summary") or {}).get("total_count")
            if total is not None:
                new_counts[pid] = total
                if total == 0 or counts.get(pid) == total:
                    continue
            try:
                cd = check(await c.get(f"{GRAPH}/{pid}/comments", params={
                    "fields": FB_COMMENT_FIELDS, "filter": "stream", "order": "reverse_chronological", "limit": 100,
                    "access_token": tok}), "Facebook comments", "facebook_page", "pages_read_user_content")
            except InboxPermissionError:
                raise
            except PublishError as e:
                if e.retryable or e.reconnect:
                    raise
                ctx.notes.append(f"Skipped a post: {e.message}")
                new_counts.pop(pid, None)
                continue
            snippet = clip(p.get("message"), 500)
            for cm in cd.get("data", []):
                frm = cm.get("from") or {}
                parent = (cm.get("parent") or {}).get("id")
                out.append(Incoming(
                    external_id=str(cm["id"]), kind="reply" if parent else "comment", text=cm.get("message"),
                    published_at=parse_time(cm.get("created_time")), author_name=frm.get("name") or "Facebook user",
                    author_external_id=frm.get("id"),
                    author_url=f"https://www.facebook.com/{frm['id']}" if frm.get("id") else None,
                    url=cm.get("permalink_url"), parent_external_id=parent, thread_key=parent or str(cm["id"]),
                    external_post_id=pid, external_post_url=p.get("permalink_url"), post_snippet=snippet,
                    is_own=bool(frm.get("id")) and str(frm.get("id")) == str(page), engagement=cm.get("like_count"),
                    raw=cm,
                ))
    ctx.state["counts"] = new_counts
    return out


async def reply_facebook(session, account: SocialAccount, item: InboxItem, text: str, state: dict) -> tuple[str, str | None]:
    _require(state, "facebook_page", "pages_manage_engagement", "Replying on Facebook")
    # Facebook threads are two levels deep: answer a reply under its top-level comment.
    target = item.parent_external_id if item.kind == "reply" and item.parent_external_id else item.external_id
    async with net.client() as c:
        d = check(await c.post(f"{GRAPH}/{target}/comments", data={"message": text, "access_token": _token(account)}),
                  "Replying on Facebook", "facebook_page", "pages_manage_engagement")
    return str(d["id"]), item.url


async def like_facebook(session, account: SocialAccount, item: InboxItem, state: dict) -> None:
    _require(state, "facebook_page", "pages_manage_engagement", "Liking on Facebook")
    async with net.client() as c:
        check(await c.post(f"{GRAPH}/{item.external_id}/likes", data={"access_token": _token(account)}),
              "Liking on Facebook", "facebook_page", "pages_manage_engagement")


# ------------------------------------------------------------------ Instagram
def _ig_item(cm: dict, *, kind: str, parent: str | None, thread: str, media: dict, own_handle: str) -> Incoming:
    frm = cm.get("from") or {}
    username = cm.get("username") or frm.get("username")
    return Incoming(
        external_id=str(cm["id"]), kind=kind, text=cm.get("text"), published_at=parse_time(cm.get("timestamp")),
        author_name=username or "Instagram user", author_handle=username, author_external_id=frm.get("id"),
        author_url=f"https://www.instagram.com/{username}/" if username else None,
        url=media.get("permalink"), parent_external_id=parent, thread_key=thread, external_post_id=str(media["id"]),
        external_post_url=media.get("permalink"), post_snippet=clip(media.get("caption"), 500),
        is_own=bool(username) and username.lower() == own_handle, engagement=cm.get("like_count"), raw=cm,
    )


async def collect_instagram(session, account: SocialAccount, ctx: CollectContext) -> list[Incoming]:
    tok = _token(account)
    ig = account.external_id
    own = (account.handle or "").lower().lstrip("@")
    out: list[Incoming] = []
    counts: dict = dict(ctx.state.get("counts") or {})
    new_counts: dict = {}
    async with net.client() as c:
        granted = await granted_permissions(c, account)
        if granted is not None:
            ctx.state["granted"] = granted
        d = check(await c.get(f"{GRAPH}/{ig}/media", params={
            "fields": "id,caption,permalink,timestamp,comments_count", "limit": MAX_POSTS_PER_ACCOUNT, "access_token": tok}),
            "Instagram posts", "instagram", "instagram_basic")
        media = [m for m in d.get("data", []) if m.get("id")]
        known = {str(m["id"]) for m in media}
        for ext, ref in ctx.posts.items():
            if ext not in known:
                media.append({"id": ext, "caption": ref.text, "permalink": ref.url})
        for m in media[:MAX_POSTS_PER_ACCOUNT]:
            ts = parse_time(m.get("timestamp"))
            if ts and ts < ctx.since:
                continue
            total = m.get("comments_count")
            if total is not None:
                new_counts[str(m["id"])] = total
                if total == 0 or counts.get(str(m["id"])) == total:
                    continue
            try:
                cd = check(await c.get(f"{GRAPH}/{m['id']}/comments", params={
                    "fields": IG_COMMENT_FIELDS, "limit": 50, "access_token": tok}),
                    "Instagram comments", "instagram", "instagram_manage_comments")
            except InboxPermissionError:
                raise
            except PublishError as e:
                if e.retryable or e.reconnect:
                    raise
                ctx.notes.append(f"Skipped a post: {e.message}")
                new_counts.pop(str(m["id"]), None)
                continue
            for cm in cd.get("data", []):
                top = str(cm["id"])
                out.append(_ig_item(cm, kind="comment", parent=None, thread=top, media=m, own_handle=own))
                for rp in ((cm.get("replies") or {}).get("data") or []):
                    out.append(_ig_item(rp, kind="reply", parent=top, thread=top, media=m, own_handle=own))
        # Posts by others that tag or @mention us. Not fatal when the permission is missing.
        try:
            td = check(await c.get(f"{GRAPH}/{ig}/tags", params={
                "fields": "id,caption,permalink,timestamp,username,comments_count,like_count", "limit": 25,
                "access_token": tok}), "Instagram mentions", "instagram", "instagram_manage_comments")
            for t in td.get("data", []):
                ts = parse_time(t.get("timestamp"))
                if ts and ts < ctx.since:
                    continue
                u = t.get("username")
                out.append(Incoming(
                    external_id=str(t["id"]), kind="mention", text=t.get("caption"), published_at=ts,
                    author_name=u or "Instagram user", author_handle=u,
                    author_url=f"https://www.instagram.com/{u}/" if u else None, url=t.get("permalink"),
                    thread_key=str(t["id"]), external_post_id=str(t["id"]), external_post_url=t.get("permalink"),
                    engagement=(t.get("like_count") or 0) + (t.get("comments_count") or 0), raw=t,
                ))
        except PublishError as e:
            if e.retryable:
                raise
            ctx.notes.append(f"Instagram mentions skipped: {e.message}")
    ctx.state["counts"] = new_counts
    return out


async def reply_instagram(session, account: SocialAccount, item: InboxItem, text: str, state: dict) -> tuple[str, str | None]:
    _require(state, "instagram", "instagram_manage_comments", "Replying on Instagram")
    tok = _token(account)
    async with net.client() as c:
        if item.kind == "mention":
            # Comment on a post that mentions us (Instagram mentions API).
            d = check(await c.post(f"{GRAPH}/{account.external_id}/mentions", data={
                "media_id": item.external_id, "message": text, "access_token": tok}),
                "Replying on Instagram", "instagram", "instagram_manage_comments")
        else:
            # Instagram replies are one level deep: answer under the top-level comment.
            target = item.thread_key or item.external_id
            d = check(await c.post(f"{GRAPH}/{target}/replies", data={"message": text, "access_token": tok}),
                      "Replying on Instagram", "instagram", "instagram_manage_comments")
    return str(d.get("id")) if d.get("id") else None, item.url
