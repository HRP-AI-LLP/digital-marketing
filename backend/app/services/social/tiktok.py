"""TikTok Content Posting API: Login Kit OAuth v2 with PKCE, creator info, direct video posts (FILE_UPLOAD in
chunks, or PULL_FROM_URL from our public media link), photo posts (PULL_FROM_URL), publish-status polling and
token refresh.

Apps that TikTok hasn't audited yet may only post with privacy SELF_ONLY ("Only me"). That is the default
until the user says the app passed the audit (account setting `audited`).
"""

import hashlib
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens

AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = ["user.info.basic", "video.publish", "video.upload"]

PRIVACY_LABELS = {"PUBLIC_TO_EVERYONE": "Everyone", "MUTUAL_FOLLOW_FRIENDS": "Friends",
                  "FOLLOWER_OF_CREATOR": "Followers", "SELF_ONLY": "Only me"}
UNAUDITED_NOTE = ("TikTok shows posts from apps it hasn't audited only to you (privacy: Only me). Posts go out privately "
                  "until TikTok approves your app; then switch on 'My TikTok app passed the audit' in this account's settings.")

SINGLE_CHUNK_MAX = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024
STATUS_POLLS = 40
STATUS_POLL_SECONDS = 5
CAPTION_MAX = 2200
PHOTO_TITLE_MAX = 90
PHOTO_DESCRIPTION_MAX = 4000
MAX_PHOTOS = 35

_RECONNECT = {"access_token_invalid", "scope_not_authorized", "scope_permission_missed", "invalid_grant",
              "token_expired", "invalid_token"}
_RETRY = {"rate_limit_exceeded", "internal_error", "server_error"}
_FRIENDLY = {
    "unaudited_client_can_only_post_to_private_accounts":
        "TikTok only lets apps it hasn't audited post privately. Set the account's privacy to 'Only me' (switch off "
        "'My TikTok app passed the audit' in Social accounts), or get your app audited by TikTok.",
    "privacy_level_option_mismatch":
        "TikTok doesn't offer the chosen privacy for this account. Pick another one in this account's settings.",
    "url_ownership_unverified":
        "TikTok can only fetch media from a website you verified. Verify your public media address (the domain of "
        "PUBLIC_BASE_URL or Cloudinary) under URL properties in the TikTok developer portal, or switch video upload "
        "back to 'Upload the file' in this account's settings.",
    "spam_risk_too_many_posts": "TikTok's daily posting limit for this account has been reached. Try again tomorrow.",
    "spam_risk_too_many_pending_share": "Too many TikTok posts are still waiting to be processed. Try again later.",
    "spam_risk_user_banned_from_posting": "TikTok has blocked this account from posting through apps for now.",
    "reached_active_user_cap": "Your TikTok app reached its daily user limit. Try again tomorrow.",
    "unaudited_client_can_only_post_to_private_account": "TikTok only lets unaudited apps post privately.",
}


# ------------------------------------------------------------------ errors
def _err(r: httpx.Response) -> tuple[str, str]:
    try:
        d = r.json()
    except ValueError:
        return "", (r.text or r.reason_phrase or f"HTTP {r.status_code}")[:300]
    if not isinstance(d, dict):
        return "", str(d)[:300]
    e = d.get("error")
    if isinstance(e, dict):
        return str(e.get("code") or ""), str(e.get("message") or e.get("code") or "")[:300]
    if isinstance(e, str):  # OAuth endpoints
        return e, str(d.get("error_description") or e)[:300]
    return "", ""


def check(r: httpx.Response, what: str) -> dict:
    code, msg = _err(r)
    if r.is_success and code in ("", "ok"):
        try:
            return r.json() if r.content else {}
        except ValueError:
            return {}
    low = code.lower()
    if low in _FRIENDLY:
        raise PublishError(f"{what}: {_FRIENDLY[low]}")
    if low in _RECONNECT or r.status_code == 401:
        raise PublishError(f"{what}: TikTok no longer accepts this connection ({msg or code}). Connect TikTok again.",
                           reconnect=True)
    retry = low in _RETRY or r.status_code == 429 or r.status_code >= 500
    raise PublishError(f"{what}: {msg or code or f'HTTP {r.status_code}'}", retryable=retry)


# ------------------------------------------------------------------ OAuth
def pkce_challenge(verifier: str) -> str:
    """TikTok expects the hex-encoded SHA-256 of the verifier (not base64url)."""
    return hashlib.sha256(verifier.encode()).hexdigest()


def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"client_key": creds["client_id"], "scope": ",".join(SCOPES), "response_type": "code",
         "redirect_uri": redirect_uri, "state": state}
    if code_challenge:
        q.update(code_challenge=code_challenge, code_challenge_method="S256")
    return f"{AUTHORIZE}?{urlencode(q)}"


def _tokens(t: dict, old: dict | None = None) -> dict:
    old = old or {}
    rexp = common.expires_from(t.get("refresh_expires_in"))
    return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token") or old.get("refresh_token"),
            "open_id": t.get("open_id") or old.get("open_id"), "scope": t.get("scope") or old.get("scope"),
            "refresh_expires_at": rexp.isoformat() if rexp else old.get("refresh_expires_at")}


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"}


async def creator_info(c: httpx.AsyncClient, tok: str) -> dict:
    return check(await c.post(f"{API}/post/publish/creator_info/query/", headers=_bearer(tok), json={}),
                 "TikTok creator info").get("data") or {}


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    async with net.client() as c:
        form = {"client_key": creds["client_id"], "client_secret": creds["client_secret"], "code": code,
                "grant_type": "authorization_code", "redirect_uri": redirect_uri}
        if data.get("code_verifier"):
            form["code_verifier"] = data["code_verifier"]
        t = check(await c.post(f"{API}/oauth/token/", data=form), "TikTok sign-in")
        if not t.get("access_token"):
            raise PublishError("TikTok didn't return an access token. Press Connect again.")
        granted = set((t.get("scope") or "").split(","))
        tok = t["access_token"]
        u = check(await c.get(f"{API}/user/info/", params={"fields": "open_id,union_id,avatar_url,display_name"},
                              headers=_bearer(tok)), "TikTok profile").get("data", {}).get("user") or {}
        info: dict = {}
        if "video.publish" in granted or not t.get("scope"):
            try:
                info = await creator_info(c, tok)
            except PublishError:
                info = {}
    if t.get("scope") and "video.publish" not in granted:
        session.info["connect_warning"] = ("TikTok didn't grant posting (video.publish). Add the Content Posting API "
                                           "with Direct Post to your TikTok app, then connect again.")
    open_id = str(u.get("open_id") or t.get("open_id") or "")
    if not open_id:
        raise PublishError("TikTok didn't return the account details. Try connecting again.")
    username = info.get("creator_username")
    acc = await common.upsert_account(
        session, org_id, user_id, "tiktok", open_id,
        name=info.get("creator_nickname") or u.get("display_name") or username or "TikTok",
        handle=f"@{username}" if username else None, avatar_url=info.get("creator_avatar_url") or u.get("avatar_url"),
        profile_url=f"https://www.tiktok.com/@{username}" if username else None,
        tokens=_tokens(t), expires_at=common.expires_from(t.get("expires_in")),
        settings={"audited": False, "privacy_level": "SELF_ONLY", "video_source": "upload",
                  "disable_comment": False, "disable_duet": False, "disable_stitch": False, "is_aigc": False,
                  "privacy_options": info.get("privacy_level_options") or [],
                  "max_video_seconds": info.get("max_video_post_duration_sec")},
        status_message=UNAUDITED_NOTE,
    )
    return [acc]


async def _refresh_tokens(session, account: SocialAccount, c: httpx.AsyncClient) -> str:
    tokens = account_tokens(account)
    if not tokens.get("refresh_token"):
        raise PublishError("TikTok access expired. Connect TikTok again.", reconnect=True)
    creds = await common.app_credentials(session, "tiktok")
    if not creds:
        raise PublishError("The TikTok app credentials are missing in Integrations.")
    t = check(await c.post(f"{API}/oauth/token/", data={
        "client_key": creds["client_id"], "client_secret": creds["client_secret"], "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"]}), "TikTok token refresh")
    new = _tokens(t, tokens)
    await common.persist_tokens(account, new, common.expires_from(t.get("expires_in")))
    return new["access_token"]


async def access_token(session, account: SocialAccount, c: httpx.AsyncClient, within: timedelta = timedelta(minutes=10)) -> str:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect TikTok again.", reconnect=True)
    if account.token_expires_at is None or common.expiring(account, within):
        tok = await _refresh_tokens(session, account, c)
    return tok


async def refresh(session, account: SocialAccount) -> None:
    async with net.client() as c:
        tok = await access_token(session, account, c, within=timedelta(hours=12))  # tokens last 24 hours
        info = await creator_info(c, tok)
    s = dict(account.settings or {})
    if info.get("privacy_level_options"):
        s["privacy_options"] = info["privacy_level_options"]
    if info.get("max_video_post_duration_sec"):
        s["max_video_seconds"] = info["max_video_post_duration_sec"]
    account.settings = s
    if not s.get("audited"):
        account.status_message = UNAUDITED_NOTE


# ------------------------------------------------------------------ publish
def _privacy(account: SocialAccount, info: dict) -> str:
    s = account.settings or {}
    options = info.get("privacy_level_options") or []
    if not s.get("audited"):
        return "SELF_ONLY"
    wanted = s.get("privacy_level")
    if not wanted:
        raise PublishError("Choose who can see TikTok posts in this account's settings (Social accounts).")
    if options and wanted not in options:
        names = ", ".join(PRIVACY_LABELS.get(o, o) for o in options)
        raise PublishError(f"TikTok doesn't offer '{PRIVACY_LABELS.get(wanted, wanted)}' for this account right now. "
                           f"Choose one of: {names}.")
    return wanted


def _video_bytes(asset: MediaAsset) -> tuple[bytes, str]:
    if common.is_video(asset):
        return common.read_bytes(asset), asset.mime or "video/mp4"
    from app.services.media import asset_file  # animations / GIFs have an MP4 twin

    path, mime = asset_file(asset, "mp4")
    if path is None:
        raise PublishError("TikTok needs a video. This animation has no MP4 version; render it as a video first.")
    return path.read_bytes(), mime or "video/mp4"


async def _upload_chunks(c: httpx.AsyncClient, url: str, data: bytes, mime: str) -> None:
    size = len(data)
    n = 1 if size <= SINGLE_CHUNK_MAX else size // CHUNK
    chunk = size if n == 1 else CHUNK
    for i in range(n):
        start = i * chunk
        end = size - 1 if i == n - 1 else start + chunk - 1
        r = await c.put(url, content=data[start:end + 1], headers={
            "Content-Type": mime, "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
        if r.status_code not in (200, 201, 206):
            check(r, "TikTok video upload")


async def _wait(c: httpx.AsyncClient, tok: str, publish_id: str) -> dict | None:
    for _ in range(STATUS_POLLS):
        d = check(await c.post(f"{API}/post/publish/status/fetch/", headers=_bearer(tok), json={"publish_id": publish_id}),
                  "TikTok publish status").get("data") or {}
        st = d.get("status")
        if st == "PUBLISH_COMPLETE":
            return d
        if st == "FAILED":
            reason = d.get("fail_reason") or "unknown reason"
            raise PublishError(f"TikTok couldn't publish the post: {_FRIENDLY.get(str(reason).lower(), reason)}")
        await common.sleep(STATUS_POLL_SECONDS)
    return None


def _caption(post: Post) -> str:
    return common.compose_text(post)[:CAPTION_MAX]


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    if not media:
        raise PublishError("TikTok posts need a video or photos.")
    s = account.settings or {}
    videos = [m for m in media if common.is_video(m) or (m.meta or {}).get("mp4_path")]
    photos = [m for m in media if not common.is_video(m) and not common.is_pdf(m) and not common.is_gif(m)]
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        tok = await access_token(session, account, c)
        info = await creator_info(c, tok)
        privacy = _privacy(account, info)
        interactions = {"disable_comment": bool(s.get("disable_comment") or info.get("comment_disabled"))}
        if videos:
            v = videos[0]
            limit = info.get("max_video_post_duration_sec")
            if limit and v.duration_s and v.duration_s > float(limit) + 0.5:
                raise PublishError(f"This video is {v.duration_s:.0f} seconds; TikTok allows up to {limit} seconds for this account.")
            post_info = {"title": _caption(post), "privacy_level": privacy, **interactions,
                         "disable_duet": bool(s.get("disable_duet") or info.get("duet_disabled")),
                         "disable_stitch": bool(s.get("disable_stitch") or info.get("stitch_disabled")),
                         "video_cover_timestamp_ms": 1000}
            if s.get("is_aigc"):
                post_info["is_aigc"] = True
            if s.get("video_source") == "url":
                src = {"source": "PULL_FROM_URL", "video_url": await common.public_url(session, v)}
                data = mime = None
            else:
                data, mime = _video_bytes(v)
                n = 1 if len(data) <= SINGLE_CHUNK_MAX else len(data) // CHUNK
                src = {"source": "FILE_UPLOAD", "video_size": len(data),
                       "chunk_size": len(data) if n == 1 else CHUNK, "total_chunk_count": n}
            d = check(await c.post(f"{API}/post/publish/video/init/", headers=_bearer(tok),
                                   json={"post_info": post_info, "source_info": src}), "TikTok video post").get("data") or {}
            publish_id = str(d.get("publish_id") or "")
            if data is not None:
                if not d.get("upload_url"):
                    raise PublishError("TikTok didn't return an upload address. Try again.", retryable=True)
                await _upload_chunks(c, d["upload_url"], data, mime)
            kind = "video"
        elif photos:
            urls = [await common.public_url(session, m, jpeg=True) for m in photos[:MAX_PHOTOS]]
            post_info = {"title": (post.title or post.topic or "")[:PHOTO_TITLE_MAX],
                         "description": common.compose_text(post)[:PHOTO_DESCRIPTION_MAX], "privacy_level": privacy,
                         **interactions, "auto_add_music": True}
            d = check(await c.post(f"{API}/post/publish/content/init/", headers=_bearer(tok), json={
                "post_info": post_info, "post_mode": "DIRECT_POST", "media_type": "PHOTO",
                "source_info": {"source": "PULL_FROM_URL", "photo_cover_index": 0, "photo_images": urls}}),
                "TikTok photo post").get("data") or {}
            publish_id = str(d.get("publish_id") or "")
            kind = "photo"
        else:
            raise PublishError("TikTok takes videos or JPG/PNG/WebP photos. GIFs need their MP4 version and PDFs aren't supported.")
        if not publish_id:
            raise PublishError("TikTok didn't confirm the post. Check TikTok before trying again.")
        post.meta = {**(post.meta or {}), "tiktok_publish_id": publish_id, "tiktok_privacy": privacy}
        done = await _wait(c, tok, publish_id)
    if done is None:
        post.meta = {**post.meta, "publish_note": "TikTok is still processing this post; it appears on TikTok when ready."}
        return publish_id, None
    ids = done.get("publicaly_available_post_id") or done.get("publicly_available_post_id") or []
    if not ids:  # private (Only me) posts have no public id
        return publish_id, account.profile_url
    pid = str(ids[0])
    handle = (account.handle or "").lstrip("@")
    return pid, f"https://www.tiktok.com/@{handle}/{kind}/{pid}" if handle else None


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    return None  # needs the video.list scope, which this connection doesn't ask for
