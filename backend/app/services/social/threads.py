"""Threads (Meta): its own OAuth at threads.net and the graph.threads.net container → publish flow.
Media must be at public URLs."""

from urllib.parse import urlencode

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens
from app.services.social.meta import check  # same error format as the Graph API

THREADS_VERSION = "v1.0"
API = f"https://graph.threads.net/{THREADS_VERSION}"
TOKEN_HOST = "https://graph.threads.net"
AUTHORIZE = "https://threads.net/oauth/authorize"
SCOPES = ["threads_basic", "threads_content_publish", "threads_manage_insights"]

POLLS = 60
POLL_SECONDS = 5


def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"client_id": creds["client_id"], "redirect_uri": redirect_uri, "scope": ",".join(SCOPES),
         "response_type": "code", "state": state}
    return f"{AUTHORIZE}?{urlencode(q)}"


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    async with net.client() as c:
        short = check(await c.post(f"{TOKEN_HOST}/oauth/access_token", data={
            "client_id": creds["client_id"], "client_secret": creds["client_secret"], "grant_type": "authorization_code",
            "redirect_uri": redirect_uri, "code": code}), "Threads sign-in")
        ll = check(await c.get(f"{TOKEN_HOST}/access_token", params={
            "grant_type": "th_exchange_token", "client_secret": creds["client_secret"],
            "access_token": short["access_token"]}), "Threads long-lived token")
        tok = ll["access_token"]
        me = check(await c.get(f"{API}/me", params={
            "fields": "id,username,name,threads_profile_picture_url", "access_token": tok}), "Threads profile")
    uid = str(me.get("id") or short.get("user_id"))
    username = me.get("username")
    acc = await common.upsert_account(
        session, org_id, user_id, "threads", uid, name=me.get("name") or username or "Threads", handle=username,
        avatar_url=me.get("threads_profile_picture_url"),
        profile_url=f"https://www.threads.net/@{username}" if username else None,
        tokens={"access_token": tok, "user_id": uid}, expires_at=common.expires_from(ll.get("expires_in")),
    )
    return [acc]


async def refresh(session, account: SocialAccount) -> None:
    tokens = account_tokens(account)
    tok = tokens.get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    async with net.client() as c:
        if common.expiring(account):
            # Long-lived Threads tokens (60 days) can be refreshed once they are at least 24 hours old.
            d = check(await c.get(f"{TOKEN_HOST}/refresh_access_token",
                                  params={"grant_type": "th_refresh_token", "access_token": tok}), "Threads token refresh")
            tok = d["access_token"]
            common.save_tokens(account, {**tokens, "access_token": tok}, common.expires_from(d.get("expires_in")))
        check(await c.get(f"{API}/me", params={"fields": "id,username", "access_token": tok}), "Checking the account")


async def _wait(c: httpx.AsyncClient, container: str, tok: str) -> None:
    for _ in range(POLLS):
        d = check(await c.get(f"{API}/{container}", params={"fields": "status,error_message", "access_token": tok}),
                  "Threads media status")
        st = d.get("status")
        if st in ("FINISHED", "PUBLISHED"):
            return
        if st in ("ERROR", "EXPIRED"):
            raise PublishError(f"Threads couldn't process the media: {d.get('error_message') or st}")
        await common.sleep(POLL_SECONDS)
    raise PublishError("Threads is still processing the media. It will be retried.", retryable=True)


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    me = account.external_id
    text = common.compose_text(post, link=not media)
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        async def item(m: MediaAsset, carousel: bool) -> str:
            url = await common.public_url(session, m)
            d = {"access_token": tok}
            if common.is_video(m):
                d.update(media_type="VIDEO", video_url=url)
            else:
                d.update(media_type="IMAGE", image_url=url)
            if carousel:
                d["is_carousel_item"] = "true"
            else:
                d["text"] = text
            return str(check(await c.post(f"{API}/{me}/threads", data=d), "Threads media")["id"])

        if len(media) > 1:
            children = [await item(m, True) for m in media[:20]]
            for ch in children:
                await _wait(c, ch, tok)
            container = str(check(await c.post(f"{API}/{me}/threads", data={
                "media_type": "CAROUSEL", "children": ",".join(children), "text": text, "access_token": tok}),
                "Threads carousel")["id"])
        elif media:
            container = await item(media[0], False)
        else:
            container = str(check(await c.post(f"{API}/{me}/threads", data={
                "media_type": "TEXT", "text": text, "access_token": tok}), "Threads post")["id"])
        await _wait(c, container, tok)
        tid = str(check(await c.post(f"{API}/{me}/threads_publish", data={"creation_id": container, "access_token": tok}),
                        "Threads publish")["id"])
        permalink = None
        try:
            permalink = check(await c.get(f"{API}/{tid}", params={"fields": "permalink", "access_token": tok}),
                              "Threads permalink").get("permalink")
        except (PublishError, httpx.HTTPError):
            pass
    return tid, permalink


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    tok = account_tokens(account).get("access_token")
    if not tok or not post.external_id:
        return None
    async with net.client() as c:
        d = check(await c.get(f"{API}/{post.external_id}/insights", params={
            "metric": "views,likes,replies,reposts,quotes,shares", "access_token": tok}), "Threads insights")
    vals = {m["name"]: ((m.get("values") or [{}])[0].get("value") or (m.get("total_value") or {}).get("value") or 0)
            for m in d.get("data", [])}
    return common.metrics(d, impressions=vals.get("views"), likes=vals.get("likes"), comments=vals.get("replies"),
                          shares=(vals.get("reposts") or 0) + (vals.get("quotes") or 0) + (vals.get("shares") or 0),
                          video_views=vals.get("views"))
