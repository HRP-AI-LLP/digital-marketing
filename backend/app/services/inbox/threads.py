"""Threads replies: GET /{media}/conversation (all nested replies, threads_read_replies); answer by publishing a
TEXT container with reply_to_id (threads_manage_replies)."""

import httpx

from app.models import SocialAccount
from app.models.inbox import InboxItem
from app.services.inbox.base import MAX_POSTS_PER_ACCOUNT, CollectContext, Incoming, InboxPermissionError, clip, parse_time
from app.services.inbox.meta import check
from app.services.social import net
from app.services.social import threads as th
from app.services.social.common import PublishError, account_tokens

REPLY_FIELDS = "id,text,username,permalink,timestamp,replied_to,root_post,is_reply,hide_status"
MAX_THREADS = 15


def _token(account: SocialAccount) -> str:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    return tok


async def collect(session, account: SocialAccount, ctx: CollectContext) -> list[Incoming]:
    tok = _token(account)
    own = (account.handle or "").lower().lstrip("@")
    out: list[Incoming] = []
    async with net.client() as c:
        d = check(await c.get(f"{th.API}/{account.external_id}/threads", params={
            "fields": "id,text,permalink,timestamp", "since": int(ctx.since.timestamp()),
            "limit": MAX_POSTS_PER_ACCOUNT, "access_token": tok}), "Threads posts", "threads", "threads_basic")
        posts = {str(p["id"]): p for p in d.get("data", []) if p.get("id")}
        for ext, ref in ctx.posts.items():
            posts.setdefault(ext, {"id": ext, "text": ref.text, "permalink": ref.url})
        for pid, p in list(posts.items())[:MAX_THREADS]:
            ts = parse_time(p.get("timestamp"))
            if ts and ts < ctx.since:
                continue
            try:
                cd = check(await c.get(f"{th.API}/{pid}/conversation", params={
                    "fields": REPLY_FIELDS, "reverse": "false", "access_token": tok}),
                    "Threads replies", "threads", "threads_read_replies")
            except InboxPermissionError:
                raise
            except PublishError as e:
                if e.retryable or e.reconnect:
                    raise
                ctx.notes.append(f"Skipped a thread: {e.message}")
                continue
            rows = [r for r in cd.get("data", []) if r.get("id") and r.get("hide_status") not in ("HIDDEN", "BLOCKED")]
            parent_of = {str(r["id"]): str((r.get("replied_to") or {}).get("id") or pid) for r in rows}

            def top_of(rid: str) -> str:
                seen = set()
                while parent_of.get(rid, pid) != pid and rid not in seen:
                    seen.add(rid)
                    rid = parent_of[rid]
                return rid

            for r in rows:
                rid = str(r["id"])
                parent = parent_of[rid]
                u = r.get("username")
                out.append(Incoming(
                    external_id=rid, kind="comment" if parent == pid else "reply", text=r.get("text"),
                    published_at=parse_time(r.get("timestamp")), author_name=u or "Threads user", author_handle=u,
                    author_url=f"https://www.threads.net/@{u}" if u else None, url=r.get("permalink"),
                    parent_external_id=None if parent == pid else parent, thread_key=top_of(rid),
                    external_post_id=pid, external_post_url=p.get("permalink"), post_snippet=clip(p.get("text"), 500),
                    is_own=bool(u) and u.lower() == own, raw=r,
                ))
    return out


async def reply(session, account: SocialAccount, item: InboxItem, text: str, state: dict) -> tuple[str, str | None]:
    tok = _token(account)
    me = account.external_id
    async with net.client() as c:
        container = str(check(await c.post(f"{th.API}/{me}/threads", data={
            "media_type": "TEXT", "text": text, "reply_to_id": item.external_id, "access_token": tok}),
            "Replying on Threads", "threads", "threads_manage_replies")["id"])
        await th._wait(c, container, tok)
        rid = str(check(await c.post(f"{th.API}/{me}/threads_publish", data={
            "creation_id": container, "access_token": tok}), "Replying on Threads", "threads",
            "threads_manage_replies")["id"])
        permalink = None
        try:
            permalink = check(await c.get(f"{th.API}/{rid}", params={"fields": "permalink", "access_token": tok}),
                              "Threads permalink", "threads", "threads_basic").get("permalink")
        except (PublishError, httpx.HTTPError):
            pass
    return rid, permalink
