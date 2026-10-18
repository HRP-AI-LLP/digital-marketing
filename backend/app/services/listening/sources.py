"""Listening sources. Each collector takes (ctx, spec) and returns a list of Found items; it raises SourceError with
a friendly message when the source can't be used. All are optional: free sources need no key, the rest switch on
when their credentials exist (YouTube Data API key or a connected YouTube channel, an X bearer token)."""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import Organization, ProviderConfig, SocialAccount
from app.models.listening import ListeningSettings
from app.services.inbox.base import parse_time
from app.services.listening.text import as_hashtag, domain_of, or_query, quote, strip_html
from app.services.social import net
from app.services.social.common import PublishError, account_tokens

log = logging.getLogger("listening")

PER_QUERY_TERMS = 5  # sources that need one call per term search at most this many terms per query
MAX_AGE_DAYS = 30

# id → label, default on/off, minimum minutes between calls for one query (quota-friendly), note for the UI
SOURCES: dict[str, dict] = {
    "google_news": {"label": "Google News", "default": True, "min_minutes": 0, "engine": True,
                    "note": "Free. News articles from Google News search."},
    "news": {"label": "News search", "default": True, "min_minutes": 180, "engine": True,
             "note": "Free Bing/DuckDuckGo news, or the news search of providers you added in Settings → Integrations."},
    "web": {"label": "Web search", "default": True, "min_minutes": 360, "engine": True,
            "note": "Blogs, forums and review sites from the last week. Free with DuckDuckGo or SearXNG; paid search APIs "
                    "(Brave, Tavily, Serper) are optional and used only if you add them in Settings → Integrations."},
    "reddit": {"label": "Reddit", "default": True, "min_minutes": 0, "engine": False,
               "note": "Free. Public Reddit posts, newest first."},
    "hackernews": {"label": "Hacker News", "default": True, "min_minutes": 0, "engine": False,
                   "note": "Free. Stories and comments on Hacker News."},
    "bluesky": {"label": "Bluesky", "default": True, "min_minutes": 0, "engine": False,
                "note": "Public Bluesky posts. Works best with a Bluesky account connected in Social accounts."},
    "mastodon": {"label": "Mastodon", "default": True, "min_minutes": 0, "engine": False,
                 "note": "Hashtag timelines on your Mastodon server (or the public server you set below)."},
    "youtube": {"label": "YouTube", "default": True, "min_minutes": 720, "engine": True,
                "note": "Free quota. New videos. Needs a YouTube Data API key (Settings → Integrations) or a connected YouTube "
                        "channel. Uses about 100 units of the free daily quota per topic."},
    "x": {"label": "X (Twitter)", "default": True, "min_minutes": 240, "engine": False, "paid": True,
          "note": "Paid, optional. Recent posts. Only used when you add an X API bearer token on a plan that includes "
                  "search (Settings → Integrations); everything else works without it."},
}

COUNTRY_CODES = {
    "india": "IN", "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB", "canada": "CA",
    "australia": "AU", "singapore": "SG", "united arab emirates": "AE", "uae": "AE", "germany": "DE", "france": "FR",
    "japan": "JP", "south africa": "ZA", "new zealand": "NZ", "ireland": "IE", "malaysia": "MY", "philippines": "PH",
    "indonesia": "ID", "nigeria": "NG", "kenya": "KE", "saudi arabia": "SA", "pakistan": "PK", "bangladesh": "BD",
    "sri lanka": "LK", "nepal": "NP",
}

REDDIT_UA = "web:in.hrpai.studio.listening:1.0 (social listening for {who})"


class SourceError(Exception):
    def __init__(self, message: str, *, setup: bool = False):
        super().__init__(message)
        self.message = message
        self.setup = setup  # True: needs configuration, not a temporary failure


@dataclass
class Found:
    source: str
    external_id: str
    url: str | None = None
    title: str | None = None
    text: str | None = None
    author_name: str | None = None
    author_handle: str | None = None
    author_url: str | None = None
    author_followers: int | None = None
    published_at: datetime | None = None
    language: str | None = None
    score: int | None = None
    comments: int | None = None
    reach: int | None = None
    publisher: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Spec:
    """What one query searches for (from ListeningQuery)."""

    terms: list[str]
    context_terms: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)


@dataclass
class Ctx:
    session: AsyncSession
    settings: ListeningSettings
    since: datetime
    org: Organization | None = None
    bluesky: SocialAccount | None = None
    mastodon: SocialAccount | None = None
    youtube_account: SocialAccount | None = None
    youtube_key: str | None = None
    x_token: str | None = None

    @property
    def country(self) -> str:
        c = (self.org.country if self.org else None) or "India"
        return COUNTRY_CODES.get(c.strip().lower(), c.strip().upper() if len(c.strip()) == 2 else "IN")

    @property
    def contact(self) -> str:
        if self.org and self.org.website:
            return self.org.website
        return self.org.name if self.org else "a small business"


# ------------------------------------------------------------------ credentials and availability
async def provider_key(session: AsyncSession, provider: str) -> str | None:
    cfg = await session.scalar(select(ProviderConfig).where(
        ProviderConfig.provider == provider, ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at))
    if cfg is None:
        return None
    return (decrypt_json(cfg.secrets_enc) or {}).get("api_key") or None


async def _account(session: AsyncSession, platform: str) -> SocialAccount | None:
    return await session.scalar(select(SocialAccount).where(
        SocialAccount.platform == platform, SocialAccount.status == "connected").order_by(SocialAccount.created_at))


async def build_ctx(session: AsyncSession, settings: ListeningSettings, since: datetime) -> Ctx:
    org_id = session.info.get("org_id") or settings.organization_id
    return Ctx(
        session=session, settings=settings, since=since, org=await session.get(Organization, org_id),
        bluesky=await _account(session, "bluesky"), mastodon=await _account(session, "mastodon"),
        youtube_account=await _account(session, "youtube"), youtube_key=await provider_key(session, "youtube_data") or await provider_key(session, "youtube_api"),
        x_token=await provider_key(session, "x_api"),
    )


def availability(ctx: Ctx) -> dict[str, tuple[bool, str | None]]:
    """(available, what's missing) per source."""
    out: dict[str, tuple[bool, str | None]] = {s: (True, None) for s in SOURCES}
    if not ctx.mastodon and not (ctx.settings.mastodon_instance or "").strip():
        out["mastodon"] = (False, "Connect a Mastodon account or type a public Mastodon server below.")
    if not ctx.youtube_key and not ctx.youtube_account:
        out["youtube"] = (False, "Add a YouTube Data API key in Settings → Integrations, or connect YouTube.")
    if not ctx.x_token:
        out["x"] = (False, "Optional paid extra: add an X API bearer token in Settings → Integrations to include X.")
    return out


def enabled_sources(settings: ListeningSettings) -> dict[str, bool]:
    chosen = settings.sources or {}
    return {s: bool(chosen.get(s, meta["default"])) for s, meta in SOURCES.items()}


# ------------------------------------------------------------------ HTTP helpers
async def _get(url: str, what: str, *, params=None, headers=None, timeout: float = 25.0) -> httpx.Response:
    try:
        async with net.client(timeout) as c:
            r = await c.get(url, params=params, headers=headers or {})
    except httpx.HTTPError as e:
        raise SourceError(f"Couldn't reach {what} ({type(e).__name__}). It will try again next time.") from e
    if r.status_code in (401, 403):
        raise SourceError(f"{what} refused the request (HTTP {r.status_code}). Check the key or connection.",
                          setup=True)
    if r.status_code == 429:
        raise SourceError(f"{what} is rate-limiting us. It will try again next time.")
    if r.status_code >= 400:
        raise SourceError(f"{what} returned an error (HTTP {r.status_code}). It will try again next time.")
    return r


def _json(r: httpx.Response, what: str):
    try:
        return r.json()
    except ValueError as e:
        raise SourceError(f"{what} sent something we couldn't read.") from e


def _int(v) -> int | None:
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _negatives(spec: Spec, limit: int = 6) -> str:
    return " ".join(f"-{quote(x)}" for x in spec.exclude[:limit] if x)


def _fresh(f: Found) -> bool:
    return f.published_at is None or f.published_at >= datetime.now(UTC) - timedelta(days=MAX_AGE_DAYS)


# ------------------------------------------------------------------ Google News RSS
def parse_rss(xml_text: str, source: str = "google_news") -> list[Found]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SourceError("Google News sent a feed we couldn't read.") from e
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        src = item.find("source")
        publisher = (src.text or "").strip() if src is not None and src.text else None
        publisher_url = src.get("url") if src is not None else None
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "")
            when = when if when.tzinfo else when.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            when = None
        desc = strip_html(item.findtext("description"))
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -(len(publisher) + 3)].strip()
        if not link or not title:
            continue
        out.append(Found(
            source=source, external_id=guid, url=link, title=title,
            text=desc if desc and desc.lower() != title.lower() and not desc.lower().startswith(title.lower()) else None,
            author_name=publisher or domain_of(publisher_url), author_url=publisher_url, published_at=when,
            publisher=publisher, raw={"publisher_url": publisher_url, "guid": guid},
        ))
    return out


async def google_news(ctx: Ctx, spec: Spec) -> list[Found]:
    lang = (spec.languages[0] if spec.languages else "en").lower()
    country = (spec.countries[0] if spec.countries else ctx.country).upper()
    days = max(1, min(30, (datetime.now(UTC) - ctx.since).days + 1))
    q = " ".join(p for p in [or_query(spec.terms, quote_all=True), or_query(spec.context_terms),
                             _negatives(spec), f"when:{days}d"] if p)
    r = await _get("https://news.google.com/rss/search", "Google News",
                   params={"q": q, "hl": f"{lang}-{country}", "gl": country, "ceid": f"{country}:{lang}"})
    rows = parse_rss(r.text)
    for f in rows:
        f.language = lang
    return rows[:40]


# ------------------------------------------------------------------ news / web via the configured search providers
def _engine_query(spec: Spec) -> str:
    return " ".join(p for p in [or_query(spec.terms, quote_all=True), or_query(spec.context_terms), _negatives(spec)] if p)


async def news(ctx: Ctx, spec: Spec) -> list[Found]:
    from app.services.inbox.listening import news_search

    try:
        rows = await news_search(ctx.session, _engine_query(spec), n=15)
    except PublishError as e:
        raise SourceError(e.message) from e
    out = []
    for r in rows:
        out.append(Found(source="news", external_id=r["url"], url=r["url"], title=r.get("title"),
                         text=strip_html(r.get("snippet")) or None, author_name=r.get("source") or domain_of(r["url"]),
                         publisher=r.get("source"), published_at=parse_time(r.get("date")), raw={"provider_source": r.get("source")}))
    return out


async def web(ctx: Ctx, spec: Spec) -> list[Found]:
    from app.services import search

    try:
        rows, _errors = await search.search_all(ctx.session, _engine_query(spec), n=10, freshness="week", max_providers=2)
    except search.SearchError as e:
        raise SourceError(str(e)[:300]) from e
    return [Found(source="web", external_id=r["url"], url=r["url"], title=r.get("title"), text=r.get("snippet") or None,
                  author_name=domain_of(r["url"]), raw={"provider": r.get("provider")}) for r in rows]


# ------------------------------------------------------------------ Reddit
async def reddit(ctx: Ctx, spec: Spec) -> list[Found]:
    days = (datetime.now(UTC) - ctx.since).days
    q = " ".join(p for p in [or_query(spec.terms, quote_all=True), _negatives(spec)] if p)
    r = await _get("https://www.reddit.com/search.json", "Reddit",
                   params={"q": q, "sort": "new", "t": "day" if days < 1 else "week" if days < 7 else "month",
                           "limit": 50, "raw_json": 1},
                   headers={"User-Agent": REDDIT_UA.format(who=ctx.contact), "Accept": "application/json"})
    d = _json(r, "Reddit")
    out = []
    for ch in ((d or {}).get("data") or {}).get("children") or []:
        p = ch.get("data") or {}
        if not p.get("id") or p.get("over_18"):
            continue
        link = f"https://www.reddit.com{p['permalink']}" if p.get("permalink") else p.get("url")
        author = p.get("author")
        out.append(Found(
            source="reddit", external_id=p.get("name") or p["id"], url=link, title=p.get("title"),
            text=(p.get("selftext") or "")[:3000] or None, author_name=author,
            author_handle=f"u/{author}" if author else None,
            author_url=f"https://www.reddit.com/user/{author}" if author and author != "[deleted]" else None,
            published_at=parse_time(p.get("created_utc")), score=_int(p.get("score")), comments=_int(p.get("num_comments")),
            publisher=p.get("subreddit_name_prefixed"),
            raw={"subreddit": p.get("subreddit_name_prefixed"), "link": p.get("url")},
        ))
    return out


# ------------------------------------------------------------------ Hacker News (Algolia)
async def hackernews(ctx: Ctx, spec: Spec) -> list[Found]:
    out: list[Found] = []
    for term in spec.terms[:PER_QUERY_TERMS]:
        r = await _get("https://hn.algolia.com/api/v1/search_by_date", "Hacker News", params={
            "query": term, "tags": "(story,comment)", "hitsPerPage": 30,
            "numericFilters": f"created_at_i>{int(ctx.since.timestamp())}"})
        for h in (_json(r, "Hacker News") or {}).get("hits") or []:
            oid = h.get("objectID")
            if not oid:
                continue
            is_comment = bool(h.get("comment_text"))
            title = h.get("title") or (f"Comment on: {h['story_title']}" if h.get("story_title") else "Hacker News comment")
            out.append(Found(
                source="hackernews", external_id=str(oid), url=f"https://news.ycombinator.com/item?id={oid}", title=title,
                text=strip_html(h.get("comment_text") or h.get("story_text"))[:3000] or None, author_name=h.get("author"),
                author_url=f"https://news.ycombinator.com/user?id={h['author']}" if h.get("author") else None,
                published_at=parse_time(h.get("created_at_i") or h.get("created_at")), score=_int(h.get("points")),
                comments=_int(h.get("num_comments")), publisher="Hacker News", language="en",
                raw={"link": h.get("url") or h.get("story_url"), "comment": is_comment, "story_id": h.get("story_id")},
            ))
    return out


# ------------------------------------------------------------------ Bluesky
def _bsky_url(handle: str, uri: str) -> str:
    return f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"


async def bluesky(ctx: Ctx, spec: Spec) -> list[Found]:
    from app.services.inbox import bluesky as ibs

    lang = spec.languages[0] if len(spec.languages) == 1 else None
    posts: list[dict] = []
    for term in spec.terms[:PER_QUERY_TERMS]:
        q = quote(term)
        if ctx.bluesky is not None:
            try:
                posts += await ibs.search(ctx.bluesky, q, lang=lang, limit=25)
                continue
            except PublishError as e:
                log.info("bluesky search with the account failed, trying the public API: %s", e.message)
        params = {"q": q, "sort": "latest", "limit": 25, "since": ctx.since.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if lang:
            params["lang"] = lang
        try:
            r = await _get("https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts", "Bluesky", params=params)
        except SourceError as e:
            if e.setup:
                raise SourceError("Bluesky's public search needs sign-in now. Connect a Bluesky account in "
                                  "Settings → Social accounts.", setup=True) from e
            raise
        posts += (_json(r, "Bluesky") or {}).get("posts") or []
    out = []
    for p in posts:
        uri, author, rec = p.get("uri"), p.get("author") or {}, p.get("record") or {}
        if not uri:
            continue
        handle = author.get("handle") or author.get("did") or ""
        langs = rec.get("langs") or []
        out.append(Found(
            source="bluesky", external_id=uri, url=_bsky_url(handle, uri), text=rec.get("text"),
            author_name=author.get("displayName") or handle, author_handle=f"@{handle}" if handle else None,
            author_url=f"https://bsky.app/profile/{handle}" if handle else None,
            published_at=parse_time(rec.get("createdAt") or p.get("indexedAt")), language=langs[0] if langs else None,
            score=(_int(p.get("likeCount")) or 0) + (_int(p.get("repostCount")) or 0) or None,
            comments=_int(p.get("replyCount")), raw={"uri": uri, "cid": p.get("cid")},
        ))
    return out


# ------------------------------------------------------------------ Mastodon
def _instance_url(v: str) -> str:
    v = v.strip().rstrip("/")
    return v if v.startswith(("http://", "https://")) else f"https://{v}"


async def mastodon(ctx: Ctx, spec: Spec) -> list[Found]:
    headers: dict = {}
    if ctx.mastodon is not None:
        tokens = account_tokens(ctx.mastodon)
        inst = tokens.get("instance") or (ctx.mastodon.settings or {}).get("instance") or ""
        if tokens.get("access_token"):
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    else:
        inst = ctx.settings.mastodon_instance or ""
    if not inst:
        raise SourceError("Connect a Mastodon account or set a public Mastodon server.", setup=True)
    base = _instance_url(inst)
    tags = [t for t in (as_hashtag(x) for x in spec.terms) if t][:PER_QUERY_TERMS]
    out: list[Found] = []
    for tag in tags:
        r = await _get(f"{base}/api/v1/timelines/tag/{tag}", "Mastodon", params={"limit": 40}, headers=headers)
        for st in _json(r, "Mastodon") or []:
            if not isinstance(st, dict) or not st.get("id"):
                continue
            created = parse_time(st.get("created_at"))
            if created and created < ctx.since:
                continue
            acc = st.get("account") or {}
            out.append(Found(
                source="mastodon", external_id=st.get("uri") or str(st["id"]), url=st.get("url") or st.get("uri"),
                text=strip_html(st.get("content")), author_name=acc.get("display_name") or acc.get("acct"),
                author_handle=f"@{acc['acct']}" if acc.get("acct") else None, author_url=acc.get("url"),
                author_followers=_int(acc.get("followers_count")), published_at=created, language=st.get("language"),
                score=(_int(st.get("favourites_count")) or 0) + (_int(st.get("reblogs_count")) or 0) or None,
                comments=_int(st.get("replies_count")), raw={"tag": tag},
            ))
    return out


# ------------------------------------------------------------------ YouTube Data API
async def youtube(ctx: Ctx, spec: Spec) -> list[Found]:
    from app.services.social import google

    headers: dict = {}
    auth: dict = {}
    if ctx.youtube_key:
        auth = {"key": ctx.youtube_key}
    elif ctx.youtube_account is not None:
        tok = await google.google_access_token(ctx.session)
        if not tok:
            raise SourceError("Google isn't connected any more. Reconnect it, or add a YouTube Data API key.", setup=True)
        headers = {"Authorization": f"Bearer {tok}"}
    else:
        raise SourceError("Add a YouTube Data API key in Settings → Integrations.", setup=True)
    q = "|".join(quote(t) for t in spec.terms[:8])
    if spec.exclude:
        q += " " + _negatives(spec, 4)
    params = {"part": "snippet", "q": q, "type": "video", "order": "date", "maxResults": 25,
              "publishedAfter": ctx.since.strftime("%Y-%m-%dT%H:%M:%SZ"), **auth}
    if spec.languages:
        params["relevanceLanguage"] = spec.languages[0]
    if spec.countries:
        params["regionCode"] = spec.countries[0].upper()
    d = _json(await _get(f"{google.YOUTUBE_API}/search", "YouTube", params=params, headers=headers), "YouTube")
    items = [v for v in (d or {}).get("items") or [] if (v.get("id") or {}).get("videoId")]
    stats: dict[str, dict] = {}
    if items:
        try:
            sd = _json(await _get(f"{google.YOUTUBE_API}/videos", "YouTube", headers=headers, params={
                "part": "statistics", "id": ",".join(v["id"]["videoId"] for v in items), **auth}), "YouTube")
            stats = {x.get("id"): x.get("statistics") or {} for x in (sd or {}).get("items") or []}
        except SourceError:
            stats = {}
    out = []
    for v in items:
        vid = v["id"]["videoId"]
        sn = v.get("snippet") or {}
        st = stats.get(vid, {})
        out.append(Found(
            source="youtube", external_id=vid, url=f"https://www.youtube.com/watch?v={vid}", title=strip_html(sn.get("title")),
            text=strip_html(sn.get("description")) or None, author_name=sn.get("channelTitle"),
            author_url=f"https://www.youtube.com/channel/{sn['channelId']}" if sn.get("channelId") else None,
            published_at=parse_time(sn.get("publishedAt")), language=sn.get("defaultAudioLanguage"),
            score=_int(st.get("likeCount")), comments=_int(st.get("commentCount")), reach=_int(st.get("viewCount")),
            publisher="YouTube", raw={"channel_id": sn.get("channelId")},
        ))
    return out


# ------------------------------------------------------------------ X recent search
async def x(ctx: Ctx, spec: Spec) -> list[Found]:
    if not ctx.x_token:
        raise SourceError("Add an X API bearer token in Settings → Integrations.", setup=True)
    q = " ".join(p for p in [or_query(spec.terms, quote_all=True), or_query(spec.context_terms), _negatives(spec, 4),
                             "-is:retweet"] if p)
    if len(spec.languages) == 1:
        q += f" lang:{spec.languages[0]}"
    start = max(ctx.since, datetime.now(UTC) - timedelta(days=6, hours=23))
    r = await _get("https://api.x.com/2/tweets/search/recent", "X", headers={"Authorization": f"Bearer {ctx.x_token}"},
                   params={"query": q[:512], "max_results": 25, "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "tweet.fields": "created_at,public_metrics,lang,author_id", "expansions": "author_id",
                           "user.fields": "username,name,public_metrics"})
    d = _json(r, "X") or {}
    users = {u.get("id"): u for u in (d.get("includes") or {}).get("users") or []}
    out = []
    for t in d.get("data") or []:
        u = users.get(t.get("author_id")) or {}
        m = t.get("public_metrics") or {}
        uname = u.get("username")
        out.append(Found(
            source="x", external_id=str(t.get("id")),
            url=f"https://x.com/{uname or 'i/web'}/status/{t.get('id')}", text=t.get("text"),
            author_name=u.get("name") or uname, author_handle=f"@{uname}" if uname else None,
            author_url=f"https://x.com/{uname}" if uname else None,
            author_followers=_int((u.get("public_metrics") or {}).get("followers_count")),
            published_at=parse_time(t.get("created_at")), language=t.get("lang"),
            score=(_int(m.get("like_count")) or 0) + (_int(m.get("retweet_count")) or 0) or None,
            comments=_int(m.get("reply_count")), reach=_int(m.get("impression_count")),
        ))
    return out


COLLECTORS = {
    "google_news": google_news, "news": news, "web": web, "reddit": reddit, "hackernews": hackernews,
    "bluesky": bluesky, "mastodon": mastodon, "youtube": youtube, "x": x,
}


async def collect(source: str, ctx: Ctx, spec: Spec) -> list[Found]:
    try:
        rows = await asyncio.wait_for(COLLECTORS[source](ctx, spec), timeout=120)
    except TimeoutError as e:
        raise SourceError(f"{SOURCES[source]['label']} took too long. It will try again next time.") from e
    return [f for f in rows if _fresh(f) and (f.url or f.external_id) and re.search(r"\w", f"{f.title or ''}{f.text or ''}")]
