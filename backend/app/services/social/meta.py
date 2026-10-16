"""Meta: Facebook Login → Facebook Pages and linked Instagram professional accounts.

Facebook: text/link (/{page}/feed), photo (/{page}/photos), multi-photo (unpublished photos +
attached_media), video (/{page}/videos). Instagram: container (/{ig}/media) → poll → /{ig}/media_publish,
media must be at public URLs (ensure_public_url).
"""

import json
from urllib.parse import urlencode

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens

GRAPH_VERSION = "v21.0"  # the one place to bump the Graph API version
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
GRAPH_VIDEO = f"https://graph-video.facebook.com/{GRAPH_VERSION}"  # documented host for video uploads
DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"

# Pages scopes come from the "Manage everything on your Page" use case. Instagram scopes need the separate
# "Manage messaging and content on Instagram" use case (API setup with Facebook Login); Facebook rejects the whole
# sign-in with "Invalid Scopes" if they're requested without it, so they're only asked for when chosen.
SCOPES = [
    "pages_show_list", "pages_read_engagement", "pages_manage_posts", "pages_read_user_content", "read_insights",
    "business_management",
    "pages_manage_engagement",  # inbox: reply to and like comments (add it to the Page use case, then reconnect)
]
IG_SCOPES = ["instagram_basic", "instagram_content_publish", "instagram_manage_insights",
             "instagram_manage_comments"]  # inbox: read and reply to comments, @mentions
PAGE_FIELDS = "id,name,access_token,link,username,picture{url}"
IG_FIELD = ",instagram_business_account{id,username,name,profile_picture_url}"


def scopes(options: dict | None) -> list[str]:
    return SCOPES + (IG_SCOPES if (options or {}).get("include_instagram") else [])

# Graph error codes: https://developers.facebook.com/docs/graph-api/guides/error-handling
TOKEN_CODES = {190, 102, 463, 467}
PERMISSION_CODES = {10, 200, 3}
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613, 80001, 80002, 80004}

CONTAINER_POLLS = 60
CONTAINER_POLL_SECONDS = 5


def check(r: httpx.Response, what: str) -> dict:
    try:
        data = r.json() if r.content else {}
    except ValueError:
        data = {}
    err = data.get("error") if isinstance(data, dict) else None
    if r.is_success and not err:
        return data if isinstance(data, dict) else {"data": data}
    if not isinstance(err, dict):
        common.raise_for(r, what)
        raise PublishError(f"{what}: {r.text[:300]}")
    code = err.get("code")
    msg = err.get("error_user_msg") or err.get("message") or r.text[:300]
    retryable = code in TRANSIENT_CODES or bool(err.get("is_transient")) or r.status_code == 429 or r.status_code >= 500
    reconnect = not retryable and (code in TOKEN_CODES or code in PERMISSION_CODES or r.status_code == 401)
    raise PublishError(f"{what}: {msg}", retryable=retryable, reconnect=reconnect)


# ------------------------------------------------------------------ OAuth
def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"client_id": creds["client_id"], "redirect_uri": redirect_uri, "state": state, "response_type": "code",
         "scope": ",".join(scopes(options))}
    return f"{DIALOG}?{urlencode(q)}"


async def _paged(c: httpx.AsyncClient, url: str, params: dict, what: str, max_pages: int = 5) -> list[dict]:
    out: list[dict] = []
    d = check(await c.get(url, params=params), what)
    for _ in range(max_pages):
        out.extend(d.get("data") or [])
        nxt = (d.get("paging") or {}).get("next")
        if not nxt:
            break
        d = check(await c.get(nxt), what)
    return out


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    async with net.client() as c:
        short = check(await c.get(f"{GRAPH}/oauth/access_token", params={
            "client_id": creds["client_id"], "client_secret": creds["client_secret"],
            "redirect_uri": redirect_uri, "code": code}), "Facebook sign-in")["access_token"]
        ll = check(await c.get(f"{GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": creds["client_id"],
            "client_secret": creds["client_secret"], "fb_exchange_token": short}), "Facebook long-lived token")
        user_token = ll["access_token"]
        user_expires = common.expires_from(ll.get("expires_in"))
        with_ig = bool((data.get("options") or {}).get("include_instagram"))
        pages = await _paged(c, f"{GRAPH}/me/accounts",
                             {"fields": PAGE_FIELDS + (IG_FIELD if with_ig else ""), "limit": 100,
                              "access_token": user_token}, "Facebook pages")
    if not pages:
        raise PublishError("No Facebook pages were shared. Connect again and tick the pages you manage.")
    accounts: list[SocialAccount] = []
    for p in pages:
        if not p.get("access_token"):
            continue
        tokens = {"access_token": p["access_token"], "user_token": user_token,
                  "user_token_expires_at": user_expires.isoformat() if user_expires else None}
        accounts.append(await common.upsert_account(
            session, org_id, user_id, "facebook_page", str(p["id"]), name=p.get("name") or "Facebook page",
            handle=p.get("username"), avatar_url=((p.get("picture") or {}).get("data") or {}).get("url"),
            profile_url=p.get("link") or f"https://www.facebook.com/{p['id']}", tokens=tokens, expires_at=None,
        ))
        ig = p.get("instagram_business_account")
        if ig and ig.get("id"):
            accounts.append(await common.upsert_account(
                session, org_id, user_id, "instagram", str(ig["id"]),
                name=ig.get("name") or ig.get("username") or "Instagram", handle=ig.get("username"),
                avatar_url=ig.get("profile_picture_url"),
                profile_url=f"https://www.instagram.com/{ig['username']}/" if ig.get("username") else None,
                tokens={**tokens, "page_id": str(p["id"])}, expires_at=None,
                settings={"page_id": str(p["id"])},
            ))
    return accounts


async def refresh(session, account: SocialAccount) -> None:
    """Page tokens made from a long-lived user token don't expire; check they still work."""
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    fields = "id,username,name,profile_picture_url" if account.platform == "instagram" else "id,name,link,picture{url}"
    async with net.client() as c:
        d = check(await c.get(f"{GRAPH}/{account.external_id}", params={"fields": fields, "access_token": tok}),
                  "Checking the account")
    if d.get("name"):
        account.name = d["name"][:200]
    pic = d.get("profile_picture_url") or ((d.get("picture") or {}).get("data") or {}).get("url")
    if pic:
        account.avatar_url = pic


# ------------------------------------------------------------------ publish
async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    if account.platform == "instagram":
        return await _publish_instagram(session, post, account, media, tok)
    return await _publish_facebook(post, account, media, tok)


def _file(asset: MediaAsset) -> tuple[str, bytes, str]:
    return common.filename(asset), common.read_bytes(asset), asset.mime or "application/octet-stream"


async def _publish_facebook(post: Post, account: SocialAccount, media: list[MediaAsset], tok: str):
    page = account.external_id
    text = common.compose_text(post)
    videos = [m for m in media if common.is_video(m)]
    images = [m for m in media if not common.is_video(m) and not common.is_pdf(m)]
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        if videos:
            data = {"description": text, "access_token": tok}
            if post.title:
                data["title"] = post.title[:255]
            d = check(await c.post(f"{GRAPH_VIDEO}/{page}/videos", data=data, files={"source": _file(videos[0])}),
                      "Facebook video upload")
            vid = str(d["id"])
            url = f"https://www.facebook.com/{page}/videos/{vid}"
            await _first_comment(c, vid, post, tok)
            return vid, url
        if len(images) == 1:
            d = check(await c.post(f"{GRAPH}/{page}/photos", data={"caption": text, "access_token": tok},
                                   files={"source": _file(images[0])}), "Facebook photo")
            pid = str(d.get("post_id") or d["id"])
        elif len(images) > 1:
            ids = []
            for im in images[:10]:
                d = check(await c.post(f"{GRAPH}/{page}/photos", data={"published": "false", "access_token": tok},
                                       files={"source": _file(im)}), "Facebook photo upload")
                ids.append(str(d["id"]))
            data = {"message": text, "access_token": tok}
            for i, fid in enumerate(ids):
                data[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
            pid = str(check(await c.post(f"{GRAPH}/{page}/feed", data=data), "Facebook post")["id"])
        else:
            data = {"message": text, "access_token": tok}
            if post.link_url:
                data["link"] = post.link_url
            pid = str(check(await c.post(f"{GRAPH}/{page}/feed", data=data), "Facebook post")["id"])
        await _first_comment(c, pid, post, tok)
    return pid, f"https://www.facebook.com/{pid}"


async def _first_comment(c: httpx.AsyncClient, object_id: str, post: Post, tok: str) -> None:
    if not post.first_comment:
        return
    try:
        await c.post(f"{GRAPH}/{object_id}/comments", data={"message": post.first_comment, "access_token": tok})
    except httpx.HTTPError:  # the post is out; a missing first comment isn't worth failing it
        common.log.warning("first comment failed for %s", object_id)


async def _wait_container(c: httpx.AsyncClient, container: str, tok: str, what: str = "Instagram") -> None:
    for _ in range(CONTAINER_POLLS):
        d = check(await c.get(f"{GRAPH}/{container}", params={"fields": "status_code,status", "access_token": tok}),
                  f"{what} media status")
        st = d.get("status_code")
        if st in ("FINISHED", "PUBLISHED"):
            return
        if st in ("ERROR", "EXPIRED"):
            raise PublishError(f"{what} couldn't process the media: {d.get('status') or st}")
        await common.sleep(CONTAINER_POLL_SECONDS)
    raise PublishError(f"{what} is still processing the media. It will be retried.", retryable=True)


async def _publish_instagram(session, post: Post, account: SocialAccount, media: list[MediaAsset], tok: str):
    ig = account.external_id
    caption = common.compose_text(post)
    if not media:
        raise PublishError("Instagram posts need an image or a video.")
    for m in media:
        if common.is_gif(m):
            raise PublishError("Instagram doesn't accept GIF files. Use the MP4 version of the animation.")
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        if len(media) > 1:
            children = []
            for m in media[:10]:
                url = await common.public_url(session, m, jpeg=True)
                data = {"is_carousel_item": "true", "access_token": tok}
                if common.is_video(m):
                    data.update(media_type="VIDEO", video_url=url)
                else:
                    data["image_url"] = url
                cid = str(check(await c.post(f"{GRAPH}/{ig}/media", data=data), "Instagram carousel item")["id"])
                if common.is_video(m):
                    await _wait_container(c, cid, tok)
                children.append(cid)
            data = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption, "access_token": tok}
        elif common.is_video(media[0]):
            url = await common.public_url(session, media[0], jpeg=True)
            data = {"media_type": "REELS", "video_url": url, "caption": caption, "share_to_feed": "true",
                    "access_token": tok}
        else:
            url = await common.public_url(session, media[0], jpeg=True)
            data = {"image_url": url, "caption": caption, "access_token": tok}
            if media[0].alt_text:
                data["alt_text"] = media[0].alt_text[:1000]
        container = str(check(await c.post(f"{GRAPH}/{ig}/media", data=data), "Instagram media")["id"])
        await _wait_container(c, container, tok)
        mid = str(check(await c.post(f"{GRAPH}/{ig}/media_publish", data={"creation_id": container, "access_token": tok}),
                        "Instagram publish")["id"])
        permalink = None
        try:
            permalink = check(await c.get(f"{GRAPH}/{mid}", params={"fields": "permalink", "access_token": tok}),
                              "Instagram permalink").get("permalink")
        except (PublishError, httpx.HTTPError):
            pass
        await _first_comment(c, mid, post, tok)
    return mid, permalink


# ------------------------------------------------------------------ metrics
# Meta renames insights metrics often (impressions → views in 2025). Keep the names here.
FB_POST_INSIGHTS = "post_impressions,post_impressions_unique,post_clicks"
IG_MEDIA_INSIGHTS = "reach,saved,shares,views"


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    tok = account_tokens(account).get("access_token")
    if not tok or not post.external_id:
        return None
    async with net.client() as c:
        if account.platform == "instagram":
            base = check(await c.get(f"{GRAPH}/{post.external_id}",
                                     params={"fields": "like_count,comments_count", "access_token": tok}), "Instagram")
            ins = {}
            try:
                d = check(await c.get(f"{GRAPH}/{post.external_id}/insights",
                                      params={"metric": IG_MEDIA_INSIGHTS, "access_token": tok}), "Instagram insights")
                ins = {m["name"]: (m.get("values") or [{}])[0].get("value", 0) for m in d.get("data", [])}
            except PublishError:
                pass
            return common.metrics({"media": base, "insights": ins}, likes=base.get("like_count"),
                                  comments=base.get("comments_count"), reach=ins.get("reach"), saves=ins.get("saved"),
                                  shares=ins.get("shares"), impressions=ins.get("views"), video_views=ins.get("views"))
        base = check(await c.get(f"{GRAPH}/{post.external_id}", params={
            "fields": "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0)", "access_token": tok}),
            "Facebook post")
        ins = {}
        try:
            d = check(await c.get(f"{GRAPH}/{post.external_id}/insights",
                                  params={"metric": FB_POST_INSIGHTS, "access_token": tok}), "Facebook insights")
            ins = {m["name"]: (m.get("values") or [{}])[0].get("value", 0) for m in d.get("data", [])}
        except PublishError:
            pass
        return common.metrics(
            {"post": base, "insights": ins},
            likes=((base.get("reactions") or {}).get("summary") or {}).get("total_count"),
            comments=((base.get("comments") or {}).get("summary") or {}).get("total_count"),
            shares=(base.get("shares") or {}).get("count"), impressions=ins.get("post_impressions"),
            reach=ins.get("post_impressions_unique"), clicks=ins.get("post_clicks"),
        )
