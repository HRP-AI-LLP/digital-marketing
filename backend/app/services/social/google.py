"""Google: one OAuth connection (stored on a hidden `google` holder account) used for
Business Profile posts, YouTube Shorts uploads, GA4 and Search Console.

Helpers for other modules:
  await google_access_token(session) -> str | None   (refreshes when needed)
  await google_account(session) -> SocialAccount | None
  await list_ga4_properties(session), await list_gsc_sites(session)
"""

from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens, ok_json

AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = [
    "openid", "email", "profile",
    "https://www.googleapis.com/auth/business.manage",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
ACCOUNTS_API = "https://mybusinessaccountmanagement.googleapis.com/v1"
BUSINESS_INFO_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
LOCAL_POSTS_API = "https://mybusiness.googleapis.com/v4"  # localPosts still live on the v4 API
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
ANALYTICS_ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
SEARCH_CONSOLE_API = "https://www.googleapis.com/webmasters/v3"

HOLDER = "google"


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ------------------------------------------------------------------ OAuth
def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"client_id": creds["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
         "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
         "state": state}
    if code_challenge:
        q.update(code_challenge=code_challenge, code_challenge_method="S256")
    return f"{AUTHORIZE}?{urlencode(q)}"


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    form = {"code": code, "client_id": creds["client_id"], "client_secret": creds["client_secret"],
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
    if data.get("code_verifier"):
        form["code_verifier"] = data["code_verifier"]
    async with net.client() as c:
        t = ok_json(await c.post(TOKEN, data=form), "Google sign-in")
        tok = t["access_token"]
        u = ok_json(await c.get(USERINFO, headers=_bearer(tok)), "Google profile")
        channels: list[dict] = []
        try:
            d = ok_json(await c.get(f"{YOUTUBE_API}/channels", params={"part": "snippet", "mine": "true"},
                                    headers=_bearer(tok)), "YouTube channel")
            channels = d.get("items", [])
        except (PublishError, httpx.HTTPError):
            pass
    existing = await google_account(session)
    old = account_tokens(existing) if existing else {}
    tokens = {"access_token": tok, "refresh_token": t.get("refresh_token") or old.get("refresh_token"),
              "scope": t.get("scope")}
    holder = await common.upsert_account(
        session, org_id, user_id, HOLDER, str(u.get("sub") or u.get("email")), name=u.get("email") or "Google",
        handle=u.get("email"), avatar_url=u.get("picture"), profile_url=None, tokens=tokens,
        expires_at=common.expires_from(t.get("expires_in")),
        status_message=None if tokens["refresh_token"] else
        "Google didn't return a refresh token. Remove the app's access in your Google account and connect again.",
    )
    accounts = [holder]
    for ch in channels:
        sn = ch.get("snippet") or {}
        custom = sn.get("customUrl")
        accounts.append(await common.upsert_account(
            session, org_id, user_id, "youtube", ch["id"], name=sn.get("title") or "YouTube channel", handle=custom,
            avatar_url=((sn.get("thumbnails") or {}).get("default") or {}).get("url"),
            profile_url=f"https://www.youtube.com/{custom}" if custom else f"https://www.youtube.com/channel/{ch['id']}",
            tokens=None, settings={"privacy": "public"},
        ))
    return accounts


async def google_account(session) -> SocialAccount | None:
    return await session.scalar(
        select(SocialAccount).where(SocialAccount.platform == HOLDER, SocialAccount.status != "removed")
        .order_by(SocialAccount.updated_at.desc())
    )


async def _refresh_holder(session, holder: SocialAccount, force: bool = False) -> str:
    tokens = account_tokens(holder)
    if not force and tokens.get("access_token") and not common.expiring(holder, timedelta(minutes=5)):
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise PublishError("Google access has expired and there's no refresh token. Connect Google again.",
                           reconnect=True)
    creds = await common.app_credentials(session, "google")
    if not creds:
        raise PublishError("The Google OAuth client is missing in Integrations.")
    async with net.client() as c:
        t = ok_json(await c.post(TOKEN, data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": creds["client_id"], "client_secret": creds["client_secret"]}), "Google token refresh")
    tokens["access_token"] = t["access_token"]
    if t.get("refresh_token"):
        tokens["refresh_token"] = t["refresh_token"]
    common.save_tokens(holder, tokens, common.expires_from(t.get("expires_in")))
    holder.status, holder.status_message = "connected", None
    return t["access_token"]


async def google_access_token(session) -> str | None:
    """A valid Google access token for this organization, or None when Google isn't connected."""
    holder = await google_account(session)
    if holder is None:
        return None
    return await _refresh_holder(session, holder)


async def _token(session) -> str:
    tok = await google_access_token(session)
    if not tok:
        raise PublishError("Google isn't connected. Connect Google in Settings → Social accounts.", reconnect=True)
    return tok


async def refresh(session, account: SocialAccount) -> None:
    holder = account if account.platform == HOLDER else await google_account(session)
    if holder is None:
        raise PublishError("Google isn't connected. Connect Google again.", reconnect=True)
    tok = await _refresh_holder(session, holder, force=common.expiring(holder, timedelta(minutes=30)))
    async with net.client() as c:
        r = await c.get(USERINFO, headers=_bearer(tok))
        if r.status_code == 401:
            tok = await _refresh_holder(session, holder, force=True)
            r = await c.get(USERINFO, headers=_bearer(tok))
        common.raise_for(r, "Checking Google access")
    if account is not holder:
        account.token_expires_at = None


# ------------------------------------------------------------------ Business Profile
async def list_locations(session) -> list[dict]:
    tok = await _token(session)
    out: list[dict] = []
    async with net.client() as c:
        accts = ok_json(await c.get(f"{ACCOUNTS_API}/accounts", headers=_bearer(tok)), "Business Profile accounts")
        for a in accts.get("accounts", []):
            token = None
            for _ in range(10):
                params = {"readMask": "name,title,storefrontAddress,metadata", "pageSize": 100}
                if token:
                    params["pageToken"] = token
                d = ok_json(await c.get(f"{BUSINESS_INFO_API}/{a['name']}/locations", params=params,
                                        headers=_bearer(tok)), "Business Profile locations")
                for loc in d.get("locations", []):
                    addr = loc.get("storefrontAddress") or {}
                    out.append({
                        "name": f"{a['name']}/{loc['name']}", "account": a["name"],
                        "account_name": a.get("accountName"), "location": loc["name"], "title": loc.get("title"),
                        "address": ", ".join([*(addr.get("addressLines") or []), addr.get("locality") or ""]).strip(", "),
                        "maps_url": (loc.get("metadata") or {}).get("mapsUri"),
                    })
                token = d.get("nextPageToken")
                if not token:
                    break
    return out


async def add_locations(session, org_id, user_id, names: list[str]) -> list[SocialAccount]:
    available = {loc["name"]: loc for loc in await list_locations(session)}
    out = []
    for n in names:
        loc = available.get(n)
        if loc is None:
            raise PublishError(f"Location {n} isn't available to the connected Google account.")
        out.append(await common.upsert_account(
            session, org_id, user_id, "gbp", n, name=loc.get("title") or n, handle=loc.get("address") or None,
            profile_url=loc.get("maps_url"), tokens=None,
            settings={"account": loc["account"], "location": loc["location"]},
        ))
    return out


async def _publish_gbp(session, post: Post, account: SocialAccount, media: list[MediaAsset]):
    tok = await _token(session)
    body: dict = {"languageCode": (account.settings or {}).get("language", "en"), "topicType": "STANDARD",
                  "summary": common.compose_text(post, hashtags=False)[:1500]}
    if post.link_url:
        body["callToAction"] = {"actionType": "LEARN_MORE", "url": post.link_url}
    images = [m for m in media if not common.is_video(m) and not common.is_pdf(m)]
    if images:
        body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": await common.public_url(session, images[0])}]
    async with net.client() as c:
        d = ok_json(await c.post(f"{LOCAL_POSTS_API}/{account.external_id}/localPosts", headers=_bearer(tok), json=body),
                    "Business Profile post")
    return d.get("name"), d.get("searchUrl")


# ------------------------------------------------------------------ YouTube Shorts
async def _publish_youtube(session, post: Post, account: SocialAccount, media: list[MediaAsset]):
    tok = await _token(session)
    videos = [m for m in media if common.is_video(m)]
    if not videos:
        raise PublishError("YouTube Shorts need a video.")
    v = videos[0]
    data = common.read_bytes(v)
    title = (post.title or post.topic or (post.text or "").split("\n")[0] or "Short").strip()
    if "#shorts" not in title.lower():
        title = title[: 100 - len(" #Shorts")].rstrip() + " #Shorts"
    title = title[:100]
    desc = common.compose_text(post, link=True)[:5000]
    tags = [str(h).lstrip("#") for h in (post.hashtags or [])][:15]
    meta = {"snippet": {"title": title, "description": desc, "tags": tags, "categoryId": "22"},
            "status": {"privacyStatus": (account.settings or {}).get("privacy") or "public",
                       "selfDeclaredMadeForKids": False}}
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        r = await c.post(YOUTUBE_UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"}, json=meta,
                         headers={**_bearer(tok), "X-Upload-Content-Length": str(len(data)),
                                  "X-Upload-Content-Type": v.mime or "video/mp4"})
        common.raise_for(r, "YouTube upload")
        location = r.headers.get("location")
        if not location:
            raise PublishError("YouTube didn't return an upload address.", retryable=True)
        d = ok_json(await c.put(location, content=data, headers={**_bearer(tok), "Content-Type": v.mime or "video/mp4"}),
                    "YouTube upload")
    vid = d.get("id")
    return vid, (f"https://www.youtube.com/shorts/{vid}" if vid else None)


# ------------------------------------------------------------------ dispatch
async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    if account.platform == "gbp":
        return await _publish_gbp(session, post, account, media)
    if account.platform == "youtube":
        return await _publish_youtube(session, post, account, media)
    raise PublishError("The Google connection itself can't be posted to. Pick a Business Profile location or YouTube channel.")


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    if account.platform != "youtube" or not post.external_id:
        return None  # Business Profile post insights are no longer offered by the API
    tok = await _token(session)
    async with net.client() as c:
        d = ok_json(await c.get(f"{YOUTUBE_API}/videos", params={"part": "statistics", "id": post.external_id},
                                headers=_bearer(tok)), "YouTube statistics")
    s = ((d.get("items") or [{}])[0]).get("statistics") or {}
    return common.metrics(d, video_views=s.get("viewCount"), impressions=s.get("viewCount"), likes=s.get("likeCount"),
                          comments=s.get("commentCount"))


# ------------------------------------------------------------------ analytics settings helpers
async def list_ga4_properties(session) -> list[dict]:
    tok = await _token(session)
    out: list[dict] = []
    token = None
    async with net.client() as c:
        for _ in range(10):
            params = {"pageSize": 200}
            if token:
                params["pageToken"] = token
            d = ok_json(await c.get(f"{ANALYTICS_ADMIN_API}/accountSummaries", params=params, headers=_bearer(tok)),
                        "Google Analytics properties")
            for a in d.get("accountSummaries", []):
                for p in a.get("propertySummaries", []):
                    out.append({"property": p.get("property"), "display_name": p.get("displayName"),
                                "account": a.get("account"), "account_name": a.get("displayName")})
            token = d.get("nextPageToken")
            if not token:
                break
    return out


async def list_gsc_sites(session) -> list[dict]:
    tok = await _token(session)
    async with net.client() as c:
        d = ok_json(await c.get(f"{SEARCH_CONSOLE_API}/sites", headers=_bearer(tok)), "Search Console sites")
    return [{"site_url": s.get("siteUrl"), "permission": s.get("permissionLevel")} for s in d.get("siteEntry", [])]

