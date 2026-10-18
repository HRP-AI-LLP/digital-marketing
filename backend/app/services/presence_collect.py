"""Collect public and connected data for one social profile, using the best source available.

Order per platform (the first that works wins; every failure is kept as a plain-language note):
  connected account token  → rich data (bio, website, followers, recent posts with engagement and formats)
  official public API      → YouTube Data API, Bluesky AppView, Mastodon, GitHub REST, Instagram Business Discovery,
                             Facebook Page public fields, Threads profile lookup, X API v2 (with a bearer token)
  public page              → og:title / og:description / og:image and any follower count in the page meta

All HTTP goes through app.services.social.net.client (tests patch it). Nothing here raises to the caller:
`collect()` always returns a data dict with source = connected | public | limited.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import unescape
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import AccountMetric, Post, PostMetric, ProviderConfig, SocialAccount
from app.services.analytics_growth import BLUESKY_PUBLIC, _int
from app.services.presence_profiles import ProfileRef
from app.services.social.common import PublishError, account_tokens, ok_json

log = logging.getLogger(__name__)

SAMPLE = 25
X_API = "https://api.x.com/2"
GITHUB_API = "https://api.github.com"
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_TAGS = re.compile(r"<[^>]+>")
_LOGIN_WALL = re.compile(r"\b(log ?in|sign ?up|sign ?in)\b", re.I)
_COUNT = r"(\d[\d,.]*\s*[KkMmLl]?)"

ERRORS = (PublishError, httpx.HTTPError, KeyError, ValueError, TypeError)


def parse_count(s: str | None) -> int | None:
    """'1,234' → 1234, '1.2K' → 1200, '3M' → 3000000, '2L' (lakh) → 200000."""
    if not s:
        return None
    t = s.strip().replace(",", "").replace(" ", "")
    mult = 1
    if t[-1:].lower() in ("k", "m", "l"):
        mult = {"k": 1_000, "m": 1_000_000, "l": 100_000}[t[-1].lower()]
        t = t[:-1]
    try:
        return int(float(t) * mult)
    except ValueError:
        return None


def _ts(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):  # LinkedIn: epoch milliseconds
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, UTC)
    s = str(v).replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", s):
        s = s[:-2] + ":" + s[-2:]
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _post(at, eng: int | None, fmt: str) -> dict | None:
    t = _ts(at)
    return {"at": t.isoformat(), "eng": eng, "format": fmt} if t else None


def _text(html: str | None) -> str:
    return unescape(_TAGS.sub(" ", html or "")).strip()


def _links(*texts: str | None) -> list[str]:
    out: list[str] = []
    for t in texts:
        for u in _URL.findall(t or ""):
            u = u.rstrip(".,;:!?")
            if u not in out:
                out.append(u)
    return out


def _sum(*vals) -> int | None:
    xs = [_int(v) for v in vals]
    xs = [x for x in xs if x is not None]
    return sum(xs) if xs else None


def blank(p: ProfileRef) -> dict:
    return {"name": None, "handle": p.handle, "bio": None, "website": None, "links": [], "avatar_url": None,
            "has_cover": None, "has_contact": None, "followers": None, "following": None, "posts_total": None,
            "views": None, "posts": None, "id": None, "fields": [], "notes": [], "source": "limited",
            "source_label": "No data"}


@dataclass
class Collect:
    session: AsyncSession
    c: httpx.AsyncClient
    accounts: dict[uuid.UUID, SocialAccount] = field(default_factory=dict)
    keys: dict[str, str | None] = field(default_factory=dict)

    async def account(self, p: ProfileRef) -> SocialAccount | None:
        if p.account_id is None:
            return None
        if p.account_id not in self.accounts:
            self.accounts[p.account_id] = await self.session.get(SocialAccount, p.account_id)
        return self.accounts[p.account_id]

    async def any_connected(self, platform: str) -> SocialAccount | None:
        return await self.session.scalar(select(SocialAccount).where(SocialAccount.platform == platform,
                                                                     SocialAccount.status == "connected")
                                         .order_by(SocialAccount.created_at))

    async def secret(self, provider: str, name: str = "api_key") -> str | None:
        k = f"{provider}:{name}"
        if k not in self.keys:
            cfg = await self.session.scalar(select(ProviderConfig).where(ProviderConfig.provider == provider,
                                                                         ProviderConfig.enabled.is_(True)))
            self.keys[k] = ((decrypt_json(cfg.secrets_enc).get(name) or "").strip() or None) if cfg else None
        return self.keys[k]


def _tok(acc: SocialAccount) -> str:
    tok = account_tokens(acc).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.")
    return tok


# ------------------------------------------------------------------ Meta: Facebook, Instagram, Threads
FB_FIELDS = "name,username,about,description,website,link,fan_count,followers_count,picture{url},cover{source},emails,phone,category"
FB_PUBLIC_FIELDS = "name,username,about,website,link,fan_count,followers_count,picture{url},cover{source},category"
FB_FORMATS = {"photo": "image", "video_inline": "video", "video_autoplay": "video", "video": "video", "album": "carousel",
              "share": "link", "link": "link", "event": "event"}


def _fb_profile(d: dict, data: dict) -> None:
    data.update(name=d.get("name"), bio=" ".join(x for x in (d.get("about"), d.get("description")) if x) or None,
                website=d.get("website"), avatar_url=((d.get("picture") or {}).get("data") or {}).get("url"),
                has_cover=bool((d.get("cover") or {}).get("source")),
                followers=_int(d.get("followers_count") if d.get("followers_count") is not None else d.get("fan_count")),
                id=d.get("id"))
    if "emails" in d or "phone" in d:
        data["has_contact"] = bool(d.get("emails") or d.get("phone"))
    data["links"] = _links(d.get("website"), d.get("about"), d.get("description"))
    data["fields"] += [k for k in ("name", "about", "website", "followers_count", "picture", "cover") if d.get(k)]


async def fb_connected(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.meta import GRAPH, check

    acc = await x.account(p)
    tok = _tok(acc)
    d = check(await x.c.get(f"{GRAPH}/{acc.external_id}", params={"fields": FB_FIELDS, "access_token": tok}), "Facebook page")
    _fb_profile(d, data)
    data.update(source="connected", source_label="Connected Facebook page (Graph API)")
    try:
        r = check(await x.c.get(f"{GRAPH}/{acc.external_id}/posts", params={
            "fields": "created_time,attachments{media_type},reactions.summary(total_count).limit(0),"
                      "comments.summary(total_count).limit(0),shares", "limit": SAMPLE, "access_token": tok}), "Facebook posts")
        posts = []
        for it in r.get("data") or []:
            att = ((it.get("attachments") or {}).get("data") or [{}])[0]
            eng = _sum(((it.get("reactions") or {}).get("summary") or {}).get("total_count"),
                       ((it.get("comments") or {}).get("summary") or {}).get("total_count"), (it.get("shares") or {}).get("count"))
            posts.append(_post(it.get("created_time"), eng, FB_FORMATS.get(att.get("media_type") or "", "text")))
        data["posts"] = [q for q in posts if q]
        data["fields"].append("recent posts")
    except ERRORS as e:
        data["notes"].append(f"Recent Facebook posts couldn't be read ({_msg(e)}). It needs the pages_read_engagement permission.")


async def fb_public(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.meta import GRAPH, check

    page = await x.any_connected("facebook_page")
    if page is None:
        raise PublishError("Connect a Facebook page to read other pages' public details.")
    d = check(await x.c.get(f"{GRAPH}/{p.handle}", params={"fields": FB_PUBLIC_FIELDS, "access_token": _tok(page)}),
              "Facebook page lookup")
    _fb_profile(d, data)
    data.update(source="public", source_label="Facebook page public fields (Graph API)")


IG_FIELDS = ("username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count,"
             f"media.limit({SAMPLE}){{timestamp,like_count,comments_count,media_type,media_product_type}}")


def ig_format(m: dict) -> str:
    if (m.get("media_product_type") or "").upper() == "REELS":
        return "reel"
    return {"CAROUSEL_ALBUM": "carousel", "VIDEO": "video", "IMAGE": "image"}.get((m.get("media_type") or "").upper(), "image")


def _ig_profile(d: dict, data: dict) -> None:
    media = (d.get("media") or {}).get("data") or []
    data.update(name=d.get("name"), handle=d.get("username") or data["handle"], bio=d.get("biography"),
                website=d.get("website"), avatar_url=d.get("profile_picture_url"), followers=_int(d.get("followers_count")),
                following=_int(d.get("follows_count")), posts_total=_int(d.get("media_count")), id=d.get("id"),
                links=_links(d.get("website"), d.get("biography")))
    posts = []
    for m in media:
        eng = None if m.get("like_count") is None and m.get("comments_count") is None else _sum(m.get("like_count"), m.get("comments_count"))
        posts.append(_post(m.get("timestamp"), eng, ig_format(m)))
    data["posts"] = [q for q in posts if q]
    data["fields"] += [k for k in ("biography", "website", "profile_picture_url", "followers_count", "media_count") if d.get(k)]
    if media:
        data["fields"].append("recent posts")


async def ig_connected(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.meta import GRAPH, check

    acc = await x.account(p)
    d = check(await x.c.get(f"{GRAPH}/{acc.external_id}", params={"fields": IG_FIELDS, "access_token": _tok(acc)}),
              "Instagram profile")
    _ig_profile(d, data)
    data.update(source="connected", source_label="Connected Instagram account (Graph API)")


async def ig_discovery(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.meta import GRAPH, check

    ours = await x.any_connected("instagram")
    if ours is None:
        raise PublishError("Connect an Instagram professional account to read other Instagram profiles.")
    d = check(await x.c.get(f"{GRAPH}/{ours.external_id}", params={
        "fields": f"business_discovery.username({p.handle}){{{IG_FIELDS}}}", "access_token": _tok(ours)}),
        "Instagram business discovery")
    bd = d.get("business_discovery")
    if not bd:
        raise PublishError("Instagram didn't return this profile. Business discovery works only for business and creator accounts.")
    _ig_profile(bd, data)
    data.update(source="public", source_label="Instagram Business Discovery (public business profile)")


TH_FORMATS = {"TEXT_POST": "text", "IMAGE": "image", "VIDEO": "video", "CAROUSEL_ALBUM": "carousel", "AUDIO": "audio"}


async def threads_connected(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.meta import check
    from app.services.social.threads import API

    acc = await x.account(p)
    tok = _tok(acc)
    d = check(await x.c.get(f"{API}/me", params={"fields": "id,username,name,threads_profile_picture_url,threads_biography",
                                                 "access_token": tok}), "Threads profile")
    data.update(name=d.get("name"), handle=d.get("username") or data["handle"], bio=d.get("threads_biography"),
                avatar_url=d.get("threads_profile_picture_url"), id=d.get("id"), links=_links(d.get("threads_biography")),
                source="connected", source_label="Connected Threads account (Threads API)")
    data["fields"] += [k for k in ("threads_biography", "threads_profile_picture_url") if d.get(k)]
    try:
        r = check(await x.c.get(f"{API}/{acc.external_id}/threads_insights", params={
            "metric": "followers_count", "access_token": tok}), "Threads followers")
        for m in r.get("data") or []:
            if m.get("name") == "followers_count":
                data["followers"] = _int((m.get("total_value") or {}).get("value"))
                data["fields"].append("followers_count")
    except ERRORS as e:
        data["notes"].append(f"Threads follower count isn't available ({_msg(e)}).")
    try:
        r = check(await x.c.get(f"{API}/me/threads", params={"fields": "id,timestamp,media_type", "limit": SAMPLE,
                                                             "access_token": tok}), "Threads posts")
        data["posts"] = [q for q in (_post(t.get("timestamp"), None, TH_FORMATS.get(t.get("media_type") or "", "text"))
                                     for t in r.get("data") or [] if t.get("media_type") != "REPOST_FACADE") if q]
        data["fields"].append("recent posts")
    except ERRORS as e:
        data["notes"].append(f"Recent Threads posts couldn't be read ({_msg(e)}).")


async def threads_lookup(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.meta import check
    from app.services.social.threads import API

    ours = await x.any_connected("threads")
    if ours is None:
        raise PublishError("Connect a Threads account to look up other Threads profiles.")
    d = check(await x.c.get(f"{API}/profile_lookup", params={"username": p.handle, "access_token": _tok(ours)}),
              "Threads profile lookup")
    data.update(name=d.get("name"), handle=d.get("username") or data["handle"], bio=d.get("biography"),
                avatar_url=d.get("profile_picture_url"), followers=_int(d.get("follower_count")),
                links=_links(d.get("biography")), source="public", source_label="Threads profile lookup (Threads API)")
    data["fields"] += [k for k in ("biography", "profile_picture_url", "follower_count") if d.get(k) is not None]


# ------------------------------------------------------------------ YouTube
def _duration(iso: str | None) -> int | None:
    m = re.match(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


async def youtube(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.google import YOUTUBE_API, google_access_token

    key = await x.secret("youtube_data")
    headers, auth = {}, {}
    if key:
        auth = {"key": key}
        label = "YouTube Data API (API key)"
    else:
        tok = await google_access_token(x.session)
        if not tok:
            raise PublishError("Add a YouTube Data API key in Settings → Integrations (or connect Google) to read YouTube channels.")
        headers = {"Authorization": f"Bearer {tok}"}
        label = "YouTube Data API (your Google connection)"
    params = {"part": "snippet,statistics,contentDetails,brandingSettings", **auth}
    if p.handle.startswith("UC") and p.kind != "handle":
        params["id"] = p.handle
    elif p.handle.startswith("@"):
        params["forHandle"] = p.handle
    else:
        params["forUsername"] = p.handle
    d = ok_json(await x.c.get(f"{YOUTUBE_API}/channels", params=params, headers=headers), "YouTube channel")
    items = d.get("items") or []
    if not items and "forUsername" in params:  # /c/name links are often handles too
        params.pop("forUsername")
        params["forHandle"] = "@" + p.handle
        items = ok_json(await x.c.get(f"{YOUTUBE_API}/channels", params=params, headers=headers), "YouTube channel").get("items") or []
    if not items:
        raise PublishError(f"YouTube has no channel {p.handle}.")
    ch = items[0]
    sn, st = ch.get("snippet") or {}, ch.get("statistics") or {}
    br = ch.get("brandingSettings") or {}
    thumbs = sn.get("thumbnails") or {}
    data.update(name=sn.get("title"), handle=sn.get("customUrl") or data["handle"], bio=sn.get("description"),
                avatar_url=((thumbs.get("high") or thumbs.get("default") or {}).get("url")),
                has_cover=bool((br.get("image") or {}).get("bannerExternalUrl")),
                followers=None if st.get("hiddenSubscriberCount") else _int(st.get("subscriberCount")),
                posts_total=_int(st.get("videoCount")), views=_int(st.get("viewCount")), id=ch.get("id"),
                links=_links(sn.get("description")), source="connected" if p.account_id else "public", source_label=label)
    data["fields"] += [k for k in ("title", "description", "thumbnails") if sn.get(k)] + \
        [k for k in ("subscriberCount", "videoCount", "viewCount") if st.get(k) is not None]
    if data["has_cover"]:
        data["fields"].append("banner")
    if st.get("hiddenSubscriberCount"):
        data["notes"].append("This channel hides its subscriber count.")
    uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not uploads:
        data["posts"] = []
        return
    try:
        pl = ok_json(await x.c.get(f"{YOUTUBE_API}/playlistItems", params={
            "part": "contentDetails", "playlistId": uploads, "maxResults": SAMPLE, **auth}, headers=headers), "YouTube uploads")
        vids = [(i.get("contentDetails") or {}) for i in pl.get("items") or []]
        ids = [v.get("videoId") for v in vids if v.get("videoId")]
        published = {v.get("videoId"): v.get("videoPublishedAt") for v in vids}
        posts = []
        if ids:
            vs = ok_json(await x.c.get(f"{YOUTUBE_API}/videos", params={"part": "statistics,contentDetails", "id": ",".join(ids),
                                                                         **auth}, headers=headers), "YouTube videos")
            for v in vs.get("items") or []:
                s = v.get("statistics") or {}
                dur = _duration((v.get("contentDetails") or {}).get("duration"))
                posts.append(_post(published.get(v.get("id")), _sum(s.get("likeCount"), s.get("commentCount")),
                                   "short" if dur is not None and dur <= 180 else "video"))
        data["posts"] = [q for q in posts if q]
        data["fields"].append("recent uploads")
    except ERRORS as e:
        data["notes"].append(f"Recent uploads couldn't be read ({_msg(e)}).")


# ------------------------------------------------------------------ LinkedIn
def li_format(content: dict | None) -> str:
    c = content or {}
    if "multiImage" in c:
        return "carousel"
    if "article" in c:
        return "link"
    if "poll" in c:
        return "poll"
    mid = str((c.get("media") or {}).get("id") or "")
    if ":video:" in mid:
        return "video"
    if ":document:" in mid:
        return "document"
    if ":image:" in mid:
        return "image"
    return "text"


async def linkedin_connected(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.linkedin import REST, _org_id, author_urn, headers

    acc = await x.account(p)
    tok = _tok(acc)
    urn = author_urn(acc)
    data.update(name=acc.name, source="connected", source_label="Connected LinkedIn company page (LinkedIn API)")
    r = await x.c.get(f"{REST}/organizations/{_org_id(urn)}", headers=headers(tok, False))
    if r.is_success:
        d = r.json()
        desc = d.get("localizedDescription") or ((d.get("description") or {}).get("localized") or {}).get("en_US")
        site = d.get("localizedWebsite") or ((d.get("website") or {}).get("localized") or {}).get("en_US")
        data.update(name=d.get("localizedName") or acc.name, handle=d.get("vanityName") or data["handle"], bio=desc,
                    website=site, has_cover=bool(d.get("coverPhotoV2")), links=_links(site, desc), id=urn)
        data["avatar_url"] = acc.avatar_url
        if d.get("logoV2") and not data["avatar_url"]:
            data["avatar_url"] = "logo"  # LinkedIn returns an image URN, not a URL: it exists, we just can't show it
        data["fields"] += [k for k in ("localizedName", "localizedDescription", "localizedWebsite", "logoV2", "coverPhotoV2")
                           if d.get(k)]
    else:
        data["notes"].append("LinkedIn didn't share the page details (description, website, logo). Reconnect the company page "
                             "with the Community Management API to read them.")
    r = await x.c.get(f"{REST}/networkSizes/{quote(urn, safe='')}", params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"},
                      headers=headers(tok, False))
    if r.is_success:
        data["followers"] = _int(r.json().get("firstDegreeSize"))
        data["fields"].append("followers")
    r = await x.c.get(f"{REST}/posts", params={"author": urn, "q": "author", "count": SAMPLE, "sortBy": "LAST_MODIFIED"},
                      headers=headers(tok, False))
    if r.is_success:
        posts = [_post(e.get("publishedAt") or e.get("createdAt"), None, li_format(e.get("content")))
                 for e in r.json().get("elements") or []]
        data["posts"] = [q for q in posts if q]
        data["fields"].append("recent posts")
    else:
        data["notes"].append("LinkedIn didn't share the page's recent posts; only posts published from this app are counted.")


# ------------------------------------------------------------------ X
X_FORMATS = {"photo": "image", "video": "video", "animated_gif": "gif"}


async def x_api(x: Collect, p: ProfileRef, data: dict) -> None:
    tok = await x.secret("x_api")
    if not tok:
        raise PublishError("No X API bearer token is set up (optional, paid), so the public page is used instead.")
    h = {"Authorization": f"Bearer {tok}"}
    d = ok_json(await x.c.get(f"{X_API}/users/by/username/{p.handle}", params={
        "user.fields": "description,public_metrics,profile_image_url,url,entities,verified"}, headers=h), "X profile")
    u = d.get("data") or {}
    if not u:
        raise PublishError(f"X has no account @{p.handle}.")
    pm = u.get("public_metrics") or {}
    site = (((u.get("entities") or {}).get("url") or {}).get("urls") or [{}])[0].get("expanded_url") or u.get("url")
    desc_urls = [x_.get("expanded_url") for x_ in ((u.get("entities") or {}).get("description") or {}).get("urls") or []]
    data.update(name=u.get("name"), handle=u.get("username") or data["handle"], bio=u.get("description"), website=site,
                avatar_url=u.get("profile_image_url"), followers=_int(pm.get("followers_count")),
                following=_int(pm.get("following_count")), posts_total=_int(pm.get("tweet_count")), id=u.get("id"),
                links=[x_ for x_ in [site, *desc_urls] if x_], source="public", source_label="X API v2 (bearer token)")
    data["fields"] += [k for k in ("description", "profile_image_url", "url") if u.get(k)] + ["public_metrics"]
    try:
        t = ok_json(await x.c.get(f"{X_API}/users/{u['id']}/tweets", params={
            "max_results": SAMPLE, "exclude": "replies,retweets", "tweet.fields": "created_at,public_metrics,attachments,entities",
            "expansions": "attachments.media_keys", "media.fields": "type"}, headers=h), "X posts")
        media = {m.get("media_key"): m.get("type") for m in (t.get("includes") or {}).get("media") or []}
        posts = []
        for tw in t.get("data") or []:
            keys = (tw.get("attachments") or {}).get("media_keys") or []
            fmt = X_FORMATS.get(media.get(keys[0]) if keys else "", "link" if (tw.get("entities") or {}).get("urls") else "text")
            m = tw.get("public_metrics") or {}
            posts.append(_post(tw.get("created_at"), _sum(m.get("like_count"), m.get("reply_count"), m.get("retweet_count"),
                                                          m.get("quote_count")), fmt))
        data["posts"] = [q for q in posts if q]
        data["fields"].append("recent posts")
    except ERRORS as e:
        data["notes"].append(f"Recent X posts couldn't be read ({_msg(e)}).")


# ------------------------------------------------------------------ GitHub
async def github(x: Collect, p: ProfileRef, data: dict) -> None:
    tok = await x.secret("github_api")
    h = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})}
    u = ok_json(await x.c.get(f"{GITHUB_API}/users/{p.handle}", headers=h), "GitHub profile")
    desc, email = u.get("bio"), u.get("email")
    if u.get("type") == "Organization":
        try:
            o = ok_json(await x.c.get(f"{GITHUB_API}/orgs/{p.handle}", headers=h), "GitHub organization")
            desc, email = o.get("description") or desc, o.get("email") or email
        except ERRORS:
            pass
    data.update(name=u.get("name") or u.get("login"), handle=u.get("login") or data["handle"], bio=desc,
                website=u.get("blog") or None, avatar_url=u.get("avatar_url"), followers=_int(u.get("followers")),
                posts_total=_int(u.get("public_repos")), has_contact=bool(email), id=str(u.get("id") or "") or None,
                links=_links(u.get("blog") if (u.get("blog") or "").startswith("http") else
                             ("https://" + u["blog"] if u.get("blog") else None), desc),
                source="public", source_label="GitHub REST API" + (" (token)" if tok else ""))
    data["fields"] += [k for k in ("name", "bio", "blog", "avatar_url", "followers", "public_repos") if u.get(k)]
    try:
        repos = ok_json(await x.c.get(f"{GITHUB_API}/users/{p.handle}/repos", params={
            "sort": "pushed", "per_page": 20, "type": "owner"}, headers=h), "GitHub repositories")
        rows = repos if isinstance(repos, list) else repos.get("data") or []
        data["posts"] = [q for q in (_post(r.get("pushed_at"), _sum(r.get("stargazers_count"), r.get("forks_count")), "repo")
                                     for r in rows if isinstance(r, dict) and not r.get("fork")) if q]
        data["fields"].append("recently updated repositories")
    except ERRORS as e:
        data["notes"].append(f"Repositories couldn't be read ({_msg(e)}).")


# ------------------------------------------------------------------ Bluesky, Mastodon, Pinterest
def bsky_format(post: dict) -> str:
    e = post.get("embed") or {}
    t = str(e.get("$type") or "")
    if "images" in t:
        return "carousel" if len(e.get("images") or []) > 1 else "image"
    if "video" in t:
        return "video"
    if "recordWithMedia" in t:
        return "image"
    if "external" in t:
        return "link"
    return "text"


async def bluesky(x: Collect, p: ProfileRef, data: dict) -> None:
    prof = ok_json(await x.c.get(f"{BLUESKY_PUBLIC}/xrpc/app.bsky.actor.getProfile", params={"actor": p.handle}),
                   "Bluesky profile")
    data.update(name=prof.get("displayName"), handle=prof.get("handle") or data["handle"], bio=prof.get("description"),
                avatar_url=prof.get("avatar"), has_cover=bool(prof.get("banner")), followers=_int(prof.get("followersCount")),
                following=_int(prof.get("followsCount")), posts_total=_int(prof.get("postsCount")), id=prof.get("did"),
                links=_links(prof.get("description")),
                source="connected" if p.account_id else "public", source_label="Bluesky public API")
    data["fields"] += [k for k in ("displayName", "description", "avatar", "banner", "followersCount") if prof.get(k) is not None]
    try:
        feed = ok_json(await x.c.get(f"{BLUESKY_PUBLIC}/xrpc/app.bsky.feed.getAuthorFeed", params={
            "actor": p.handle, "limit": SAMPLE, "filter": "posts_no_replies"}), "Bluesky posts")
        posts = []
        for it in feed.get("feed") or []:
            if it.get("reason"):
                continue
            q = it.get("post") or {}
            posts.append(_post((q.get("record") or {}).get("createdAt") or q.get("indexedAt"),
                               _sum(q.get("likeCount"), q.get("replyCount"), q.get("repostCount"), q.get("quoteCount")),
                               bsky_format(q)))
        data["posts"] = [q for q in posts if q]
        data["fields"].append("recent posts")
    except ERRORS as e:
        data["notes"].append(f"Recent Bluesky posts couldn't be read ({_msg(e)}).")


async def mastodon(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.analytics_competitors import _MASTO_RE, _instance

    m = _MASTO_RE.match(p.handle)
    if not m:
        raise PublishError("Mastodon addresses look like @name@instance.social.")
    inst = await _instance(m.group(2))
    acc = ok_json(await x.c.get(f"{inst}/api/v1/accounts/lookup", params={"acct": m.group(1)}), "Mastodon profile")
    note = _text(acc.get("note"))
    field_links = _links(*[re.sub(r'href="([^"]+)"', r" \1 ", str(f.get("value") or "")) for f in acc.get("fields") or []])
    data.update(name=acc.get("display_name") or acc.get("username"), bio=note or None,
                avatar_url=None if str(acc.get("avatar") or "").endswith("missing.png") else acc.get("avatar"),
                has_cover=bool(acc.get("header")) and not str(acc.get("header")).endswith("missing.png"),
                followers=_int(acc.get("followers_count")), following=_int(acc.get("following_count")),
                posts_total=_int(acc.get("statuses_count")), id=acc.get("id"), links=field_links + _links(note),
                website=field_links[0] if field_links else None,
                source="connected" if p.account_id else "public", source_label="Mastodon public API")
    data["fields"] += [k for k in ("display_name", "note", "avatar", "header", "fields", "followers_count") if acc.get(k)]
    try:
        r = await x.c.get(f"{inst}/api/v1/accounts/{acc['id']}/statuses",
                          params={"limit": SAMPLE, "exclude_reblogs": "true", "exclude_replies": "true"})
        rows = r.json() if r.is_success else []
        posts = []
        for s in rows if isinstance(rows, list) else []:
            att = (s.get("media_attachments") or [{}])
            t = (att[0] or {}).get("type") if att else None
            fmt = {"image": "image", "video": "video", "gifv": "gif"}.get(t or "", "link" if s.get("card") else "text")
            if fmt == "image" and len(att) > 1:
                fmt = "carousel"
            posts.append(_post(s.get("created_at"), _sum(s.get("favourites_count"), s.get("replies_count"), s.get("reblogs_count")), fmt))
        data["posts"] = [q for q in posts if q]
        data["fields"].append("recent posts")
    except ERRORS as e:
        data["notes"].append(f"Recent Mastodon posts couldn't be read ({_msg(e)}).")


async def pinterest_connected(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services.social.pinterest import api_base

    acc = await x.account(p)
    h = {"Authorization": f"Bearer {_tok(acc)}"}
    d = ok_json(await x.c.get(f"{api_base(acc)}/user_account", headers=h), "Pinterest profile")
    data.update(name=d.get("business_name") or d.get("username"), handle=d.get("username") or data["handle"], bio=d.get("about"),
                website=d.get("website_url"), avatar_url=d.get("profile_image"), followers=_int(d.get("follower_count")),
                following=_int(d.get("following_count")), posts_total=_int(d.get("pin_count")), views=_int(d.get("monthly_views")),
                links=_links(d.get("website_url"), d.get("about")), source="connected",
                source_label="Connected Pinterest account (Pinterest API)")
    data["fields"] += [k for k in ("about", "website_url", "profile_image", "follower_count", "pin_count") if d.get(k) is not None]
    try:
        r = ok_json(await x.c.get(f"{api_base(acc)}/pins", params={"page_size": SAMPLE}, headers=h), "Pinterest pins")
        data["posts"] = [q for q in (_post(i.get("created_at"), None,
                                           "video" if ((i.get("media") or {}).get("media_type") or "").startswith("video") else
                                           "carousel" if (i.get("media") or {}).get("media_type") == "multiple_images" else "image")
                                     for i in r.get("items") or []) if q]
        data["fields"].append("recent pins")
    except ERRORS as e:
        data["notes"].append(f"Recent pins couldn't be read ({_msg(e)}).")


# ------------------------------------------------------------------ public page fallback (og tags)
_FOLLOWERS = re.compile(_COUNT + r"\s+(followers|subscribers|fans|likes|abonnés)\b", re.I)
_POSTS = re.compile(_COUNT + r"\s+(posts|videos|pins|tweets)\b", re.I)


async def og_page(x: Collect, p: ProfileRef, data: dict) -> None:
    from app.services import crawler

    if not p.url:
        raise PublishError("No public link for this profile.")
    f = await crawler.fetch(x.c, p.url)
    if f.error or f.status >= 400 or not f.body:
        raise PublishError(f"The public page couldn't be read ({f.error or f'HTTP {f.status}'}).")
    if not f.is_html:
        raise PublishError("The public page isn't a web page.")
    page = crawler.parse_html(f.text(), f.final_url)
    og = page.og or {}
    title = og.get("og:title") or page.title or ""
    desc = og.get("og:description") or page.description or ""
    if (not desc and _LOGIN_WALL.search(title)) or (_LOGIN_WALL.search(title) and len(title) < 40 and p.platform in ("instagram", "linkedin", "facebook", "x", "threads")):
        raise PublishError("The platform shows a sign-in page instead of the public profile.")
    if not title and not desc:
        raise PublishError("The public page has no title or description to read.")
    fm, pm = _FOLLOWERS.search(desc), _POSTS.search(desc)
    data.update(name=title.split(" | ")[0].split(" (@")[0].strip() or None, bio=desc or None,
                avatar_url=og.get("og:image"), followers=parse_count(fm.group(1)) if fm else None,
                posts_total=parse_count(pm.group(1)) if pm else None, links=_links(desc),
                source="limited", source_label="Public page (title and description only)")
    data["fields"] += [k for k, v in (("og:title", title), ("og:description", desc), ("og:image", og.get("og:image")),
                                      ("followers in page text", fm)) if v]


# ------------------------------------------------------------------ app posts (published through the studio)
async def app_posts(session: AsyncSession, account_id: uuid.UUID, days: int = 90) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=days)
    posts = (await session.scalars(select(Post).where(Post.social_account_id == account_id, Post.status == "published",
                                                      Post.published_at >= since).order_by(Post.published_at.desc())
                                   .limit(60))).all()
    if not posts:
        return []
    mets = {m.post_id: m for m in (await session.scalars(
        select(PostMetric).where(PostMetric.post_id.in_([p.id for p in posts]))
        .distinct(PostMetric.post_id).order_by(PostMetric.post_id, PostMetric.fetched_at.desc()))).all()}
    out = []
    for p in posts:
        m = mets.get(p.id)
        eng = int(m.likes + m.comments + m.shares + m.saves) if m else None
        out.append({"at": p.published_at.isoformat(), "eng": eng, "format": p.post_type or "text"})
    return out


async def follower_history(session: AsyncSession, account_id: uuid.UUID, days: int = 30) -> int | None:
    """Followers about `days` ago from the daily account snapshots (analytics)."""
    cutoff = datetime.now(UTC).date() - timedelta(days=days)
    row = await session.scalar(select(AccountMetric).where(AccountMetric.social_account_id == account_id,
                                                           AccountMetric.day <= cutoff, AccountMetric.followers.is_not(None))
                               .order_by(AccountMetric.day.desc()).limit(1))
    return row.followers if row else None


# ------------------------------------------------------------------ dispatch
CONNECTED = {"facebook_page": fb_connected, "instagram": ig_connected, "threads": threads_connected, "youtube": youtube,
             "linkedin_org": linkedin_connected, "pinterest": pinterest_connected, "bluesky": bluesky, "mastodon": mastodon}
PUBLIC = {"facebook": [fb_public], "instagram": [ig_discovery], "threads": [threads_lookup], "youtube": [youtube],
          "x": [x_api], "github": [github], "bluesky": [bluesky], "mastodon": [mastodon]}
NAMES = {fb_connected: "Facebook Graph API", fb_public: "Facebook page lookup", ig_connected: "Instagram Graph API",
         ig_discovery: "Instagram Business Discovery", threads_connected: "Threads API", threads_lookup: "Threads profile lookup",
         youtube: "YouTube Data API", linkedin_connected: "LinkedIn API", pinterest_connected: "Pinterest API",
         bluesky: "Bluesky API", mastodon: "Mastodon API", x_api: "X API", github: "GitHub API", og_page: "Public page"}

LIMITED_HINT = {
    "linkedin": "Limited data — connect your LinkedIn company page in Settings → Social accounts for full analysis.",
    "x": "Limited data — X's API is paid. Optionally add an X bearer token in Settings → Integrations for full analysis.",
    "instagram": "Limited data — connect an Instagram professional account to read Instagram profiles.",
    "facebook": "Limited data — connect your Facebook page in Settings → Social accounts for full analysis.",
    "threads": "Limited data — connect your Threads account in Settings → Social accounts for full analysis.",
    "youtube": "Limited data — add a YouTube Data API key in Settings → Integrations (or connect Google).",
}


def _msg(e: Exception) -> str:
    return (e.message if isinstance(e, PublishError) else f"{type(e).__name__}: {e}")[:300]


async def collect(x: Collect, p: ProfileRef) -> dict:
    data = blank(p)
    steps = []
    acc = await x.account(p)
    if acc is not None and acc.status == "connected" and acc.platform in CONNECTED:
        steps.append(CONNECTED[acc.platform])
    steps += [s for s in PUBLIC.get(p.platform, []) if s not in steps]
    steps.append(og_page)
    for fn in steps:
        snap = {**data, "fields": list(data["fields"]), "notes": list(data["notes"])}
        try:
            await fn(x, p, data)
            break
        except ERRORS as e:
            data = snap
            data["notes"].append(f"{NAMES.get(fn, fn.__name__)}: {_msg(e)}")
        except Exception as e:  # noqa: BLE001 - one odd response must not break the whole audit
            log.warning("presence collector %s failed: %s", fn.__name__, e)
            data = snap
            data["notes"].append(f"{NAMES.get(fn, fn.__name__)}: unexpected response.")
    if data["source"] == "limited" and data["source_label"] == "No data":
        data["source_label"] = "No data (the profile couldn't be read)"
    if acc is not None:
        if data["posts"] is None:
            ours = await app_posts(x.session, acc.id)
            if ours:
                data["posts"] = ours
                data["fields"].append("posts published from this app")
        elif data["posts"] and all(q.get("eng") is None for q in data["posts"]):
            ours = [q for q in await app_posts(x.session, acc.id) if q.get("eng") is not None]
            if ours:
                data["engagement_sample"] = ours
                data["fields"].append("engagement of posts published from this app")
        data["followers_30d_ago"] = await follower_history(x.session, acc.id)
        if data["avatar_url"] is None and acc.avatar_url:
            data["avatar_url"] = acc.avatar_url
        if not data["name"]:
            data["name"] = acc.name
    if data["source"] == "limited" and p.platform in LIMITED_HINT:
        data["notes"].append(LIMITED_HINT[p.platform])
    return data
