"""YouTube comments: commentThreads.list (allThreadsRelatedToChannelId, 1 quota unit) and comments.insert
for replies. Both need the youtube.force-ssl scope."""

import httpx

from app.models import SocialAccount
from app.models.inbox import InboxItem
from app.services.inbox.base import CollectContext, Incoming, InboxPermissionError, clip, parse_time
from app.services.social import common, google, net
from app.services.social.common import PublishError, account_tokens

API = google.YOUTUBE_API
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


def check(r: httpx.Response, what: str) -> dict:
    if r.status_code == 403:
        try:
            err = (r.json() or {}).get("error") or {}
        except ValueError:
            err = {}
        reasons = {e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)}
        reasons |= {d.get("reason") for d in err.get("details") or [] if isinstance(d, dict)}
        if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "forbidden"}:
            raise InboxPermissionError("youtube", "youtube.force-ssl", what)
        if "commentsDisabled" in reasons:
            raise PublishError(f"{what}: comments are turned off for this video.")
        if reasons & {"quotaExceeded", "rateLimitExceeded", "dailyLimitExceeded"}:
            raise PublishError(f"{what}: the YouTube API quota is used up for today. It will try again later.",
                               retryable=True)
    return common.ok_json(r, what)


async def _granted_scope(session) -> str | None:
    holder = await google.google_account(session)
    return account_tokens(holder).get("scope") if holder else None


def _item(c: dict, *, kind: str, parent: str | None, thread: str, video_id: str, titles: dict, channel: str) -> Incoming:
    sn = c.get("snippet") or {}
    author_ch = (sn.get("authorChannelId") or {}).get("value")
    return Incoming(
        external_id=str(c["id"]), kind=kind, text=sn.get("textOriginal") or sn.get("textDisplay"),
        published_at=parse_time(sn.get("publishedAt")), author_name=sn.get("authorDisplayName") or "YouTube user",
        author_handle=sn.get("authorDisplayName"), author_avatar_url=sn.get("authorProfileImageUrl"),
        author_url=sn.get("authorChannelUrl"), author_external_id=author_ch,
        url=f"https://www.youtube.com/watch?v={video_id}&lc={c['id']}", parent_external_id=parent, thread_key=thread,
        external_post_id=video_id, external_post_url=f"https://www.youtube.com/watch?v={video_id}",
        post_snippet=clip(titles.get(video_id), 300), is_own=bool(author_ch) and author_ch == channel,
        engagement=sn.get("likeCount"), raw=c,
    )


async def collect(session, account: SocialAccount, ctx: CollectContext) -> list[Incoming]:
    tok = await google.google_access_token(session)
    if not tok:
        raise PublishError("Google isn't connected. Connect Google in Settings → Social accounts.", reconnect=True)
    headers = {"Authorization": f"Bearer {tok}"}
    channel = account.external_id
    threads: list[dict] = []
    async with net.client() as c:
        try:
            d = check(await c.get(f"{API}/commentThreads", headers=headers, params={
                "part": "snippet,replies", "allThreadsRelatedToChannelId": channel, "maxResults": 50, "order": "time",
                "textFormat": "plainText"}), "YouTube comments")
            threads = d.get("items", [])
        except PublishError as e:
            if isinstance(e, InboxPermissionError) or e.retryable or e.reconnect:
                raise
            # Channel-wide listing refused: fall back to the videos we published.
            for vid in list(ctx.posts)[:10]:
                try:
                    d = check(await c.get(f"{API}/commentThreads", headers=headers, params={
                        "part": "snippet,replies", "videoId": vid, "maxResults": 50, "order": "time",
                        "textFormat": "plainText"}), "YouTube comments")
                    threads.extend(d.get("items", []))
                except PublishError as e2:
                    if isinstance(e2, InboxPermissionError) or e2.retryable:
                        raise
                    ctx.notes.append(e2.message)
        video_ids = sorted({(t.get("snippet") or {}).get("videoId") for t in threads} - {None})
        titles: dict[str, str] = {}
        if video_ids:
            try:
                vd = check(await c.get(f"{API}/videos", headers=headers, params={
                    "part": "snippet", "id": ",".join(video_ids[:50])}), "YouTube videos")
                titles = {v["id"]: (v.get("snippet") or {}).get("title") for v in vd.get("items", [])}
            except PublishError:
                pass
    out: list[Incoming] = []
    for t in threads:
        sn = t.get("snippet") or {}
        top = sn.get("topLevelComment") or {}
        if not top.get("id"):
            continue
        video_id = sn.get("videoId") or (top.get("snippet") or {}).get("videoId") or ""
        ts = parse_time((top.get("snippet") or {}).get("publishedAt"))
        replies = (t.get("replies") or {}).get("comments") or []
        recent_reply = any((parse_time((r.get("snippet") or {}).get("publishedAt")) or ctx.since) >= ctx.since
                           for r in replies)
        if ts and ts < ctx.since and not recent_reply:
            continue
        tid = str(top["id"])
        out.append(_item(top, kind="comment", parent=None, thread=tid, video_id=video_id, titles=titles, channel=channel))
        for rp in replies:
            out.append(_item(rp, kind="reply", parent=tid, thread=tid, video_id=video_id, titles=titles,
                             channel=channel))
    return out


async def reply(session, account: SocialAccount, item: InboxItem, text: str, state: dict) -> tuple[str, str | None]:
    scope = await _granted_scope(session)
    if scope and SCOPE not in scope.split():
        raise InboxPermissionError("youtube", "youtube.force-ssl", "Replying on YouTube")
    tok = await google.google_access_token(session)
    if not tok:
        raise PublishError("Google isn't connected. Connect Google in Settings → Social accounts.", reconnect=True)
    parent = item.thread_key or item.external_id  # YouTube replies always attach to the top-level comment
    async with net.client() as c:
        d = check(await c.post(f"{API}/comments", params={"part": "snippet"}, headers={"Authorization": f"Bearer {tok}"},
                               json={"snippet": {"parentId": parent, "textOriginal": text}}), "Replying on YouTube")
    rid = d.get("id")
    video = item.external_post_id
    return rid, (f"https://www.youtube.com/watch?v={video}&lc={rid}" if rid and video else item.url)
