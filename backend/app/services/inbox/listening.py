"""Social listening: tracked keywords searched through official APIs only.

Sources and their minimum intervals (rate- and quota-friendly; see base.LISTENING_SOURCES):
  web        configured web search providers (app.services.search)            every 6 h
  news       the same providers' news endpoints (DuckDuckGo, Brave, Serper, Tavily, SearXNG) every 3 h
  bluesky    app.bsky.feed.searchPosts with the connected Bluesky session       hourly
  mastodon   hashtag timelines (and status search) on the connected instance    hourly
  youtube    search.list for recent videos (100 quota units a call, 3 keywords) every 12 h
  instagram  ig_hashtag_search + recent_media, only when Meta has granted it    every 6 h
"""

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import ProviderConfig, SocialAccount
from app.models.inbox import InboxItem, InboxSettings
from app.services.inbox import bluesky as ibs
from app.services.inbox import mastodon as imd
from app.services.inbox.base import LISTENING_SOURCES, Incoming, InboxPermissionError, clip, normalize_url, parse_time
from app.services.inbox.meta import check as graph_check
from app.services.inbox.store import store
from app.services.inbox.youtube import check as yt_check
from app.services.llm.catalog import BY_ID
from app.services.social import google, net
from app.services.social import meta as graph_api
from app.services.social.common import PublishError, account_tokens, ok_json

log = logging.getLogger("inbox")

MAX_KEYWORDS = 10
MAX_YOUTUBE_KEYWORDS = 3
MAX_IG_HASHTAGS = 5  # Instagram allows 30 unique hashtags per 7 days
RECENT_DAYS = 7
LABELS = {"bluesky": "Bluesky", "mastodon": "Mastodon", "youtube": "Google (YouTube)", "instagram": "Instagram"}
DEFAULT_SOURCES = {"web": True, "news": True, "bluesky": True, "mastodon": True, "youtube": False, "instagram": True}


def _now() -> datetime:
    return datetime.now(UTC)


def _domain(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _hashtag(kw: str) -> str | None:
    t = kw.strip().lstrip("#").replace(" ", "")
    return t.lower() if t and re.fullmatch(r"\w{2,60}", t) else None


def _mentions_keyword(text: str, kw: str) -> bool:
    low = text.lower()
    k = kw.lower().strip()
    return k in low or k.lstrip("#") in low or (k.replace(" ", "") in low.replace(" ", ""))


# ------------------------------------------------------------------ news through the configured providers
async def _news_ddgs(query: str, n: int, base: str, secrets: dict) -> list[dict]:
    def run() -> list[dict]:
        from ddgs import DDGS

        return list(DDGS(timeout=15).news(query, max_results=n) or [])

    rows = await asyncio.to_thread(run)
    return [{"title": r.get("title"), "url": r.get("url") or r.get("href"), "snippet": r.get("body"),
             "date": r.get("date"), "source": r.get("source")} for r in rows]


async def _news_brave(query: str, n: int, base: str, secrets: dict) -> list[dict]:
    async with net.client(20.0) as c:
        r = await c.get(f"{base.rstrip('/')}/news/search", params={"q": query, "count": min(n, 20)},
                        headers={"X-Subscription-Token": secrets.get("api_key", ""), "Accept": "application/json"})
    rows = ok_json(r, "Brave news").get("results") or []
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("description"), "date": x.get("page_age"),
             "source": (x.get("meta_url") or {}).get("hostname")} for x in rows]


async def _news_serper(query: str, n: int, base: str, secrets: dict) -> list[dict]:
    async with net.client(20.0) as c:
        r = await c.post(f"{base.rstrip('/')}/news", headers={"X-API-KEY": secrets.get("api_key", "")},
                         json={"q": query, "num": min(n, 20)})
    rows = ok_json(r, "Serper news").get("news") or []
    return [{"title": x.get("title"), "url": x.get("link"), "snippet": x.get("snippet"), "date": None,
             "source": x.get("source")} for x in rows]


async def _news_tavily(query: str, n: int, base: str, secrets: dict) -> list[dict]:
    key = secrets.get("api_key", "")
    async with net.client(20.0) as c:
        r = await c.post(f"{base.rstrip('/')}/search", headers={"Authorization": f"Bearer {key}"},
                         json={"api_key": key, "query": query, "topic": "news", "max_results": min(n, 20)})
    rows = ok_json(r, "Tavily news").get("results") or []
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content"),
             "date": x.get("published_date"), "source": None} for x in rows]


async def _news_searxng(query: str, n: int, base: str, secrets: dict) -> list[dict]:
    async with net.client(20.0) as c:
        r = await c.get(f"{base.rstrip('/')}/search", params={"q": query, "format": "json", "categories": "news"})
    rows = ok_json(r, "SearXNG news").get("results") or []
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content"),
             "date": x.get("publishedDate"), "source": x.get("engine")} for x in rows[:n]]


NEWS_STYLES = {"ddgs": _news_ddgs, "brave": _news_brave, "serper": _news_serper, "tavily": _news_tavily,
               "searxng": _news_searxng}


async def news_search(session: AsyncSession, query: str, n: int = 10) -> list[dict]:
    cfgs = (await session.scalars(select(ProviderConfig).where(
        ProviderConfig.kind == "search", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at))).all()
    attempts = [(c.provider, c.base_url, decrypt_json(c.secrets_enc)) for c in cfgs] or [("ddgs", None, {})]
    errors = []
    for provider, base, secrets in attempts:
        spec = BY_ID.get(provider)
        fn = NEWS_STYLES.get(spec.style if spec else provider)
        if fn is None:
            continue
        try:
            rows = await fn(query, n, base or (spec.base_url if spec else "") or "", secrets)
            return [r for r in rows if str(r.get("url") or "").startswith(("http://", "https://"))][:n]
        except Exception as e:  # noqa: BLE001 - try the next provider
            errors.append(f"{provider}: {e}")
    if errors:
        raise PublishError("News search failed. " + " | ".join(errors)[:500], retryable=True)
    return []


# ------------------------------------------------------------------ per-source collectors
async def _web(session, kw: str) -> list[Incoming]:
    from app.services import search

    rows = await search.web_search(session, f'"{kw}"', n=10)
    return [Incoming(external_id=normalize_url(r["url"]) or r["url"], kind="mention", title=r.get("title"),
                     text=r.get("snippet"), url=r["url"], author_name=_domain(r["url"]),
                     author_url=f"https://{_domain(r['url'])}", platform="web", raw=r) for r in rows]


async def _news(session, kw: str) -> list[Incoming]:
    rows = await news_search(session, f'"{kw}"', n=10)
    return [Incoming(external_id=normalize_url(r["url"]) or r["url"], kind="mention", title=r.get("title"),
                     text=r.get("snippet"), url=r["url"], author_name=r.get("source") or _domain(r["url"]),
                     author_url=f"https://{_domain(r['url'])}", published_at=parse_time(r.get("date")),
                     platform="news", raw=r) for r in rows]


async def _bluesky(account: SocialAccount, kw: str, langs: list[str]) -> list[Incoming]:
    posts = await ibs.search(account, kw, lang=langs[0] if len(langs) == 1 else None)
    own = account_tokens(account).get("did") or account.external_id
    out = []
    for p in posts:
        if not p.get("uri"):
            continue
        it = ibs.post_to_incoming(p, kind="mention", own_did=own)
        it.platform = "bluesky"
        out.append(it)
    return out


async def _mastodon(account: SocialAccount, kw: str) -> list[Incoming]:
    own = (account.external_id or "").rsplit(":", 1)[-1]
    tag = _hashtag(kw)
    statuses = await imd.hashtag_timeline(account, tag) if tag else []
    if not tag or " " in kw.strip():
        try:
            statuses += await imd.search_statuses(account, kw)
        except PublishError:
            pass
    out = []
    for st in statuses:
        if st.get("id"):
            it = imd.status_to_incoming(st, kind="mention", own_id=own)
            it.platform = "mastodon"
            out.append(it)
    return out


async def _youtube(session, account: SocialAccount, kw: str, langs: list[str]) -> list[Incoming]:
    tok = await google.google_access_token(session)
    if not tok:
        raise PublishError("Google isn't connected.", reconnect=True)
    params = {"part": "snippet", "q": kw, "type": "video", "order": "date", "maxResults": 10,
              "publishedAfter": (_now() - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if langs:
        params["relevanceLanguage"] = langs[0]
    async with net.client() as c:
        d = yt_check(await c.get(f"{google.YOUTUBE_API}/search", params=params,
                                 headers={"Authorization": f"Bearer {tok}"}), "YouTube search")
    out = []
    for v in d.get("items", []):
        vid = (v.get("id") or {}).get("videoId")
        sn = v.get("snippet") or {}
        if not vid:
            continue
        out.append(Incoming(
            external_id=vid, kind="mention", title=sn.get("title"), text=sn.get("description"),
            url=f"https://www.youtube.com/watch?v={vid}", author_name=sn.get("channelTitle"),
            author_external_id=sn.get("channelId"),
            author_url=f"https://www.youtube.com/channel/{sn['channelId']}" if sn.get("channelId") else None,
            author_avatar_url=((sn.get("thumbnails") or {}).get("default") or {}).get("url"),
            published_at=parse_time(sn.get("publishedAt")), is_own=sn.get("channelId") == account.external_id,
            platform="youtube", raw={"id": vid, "snippet": {k: sn.get(k) for k in ("title", "channelId", "channelTitle")}},
        ))
    return out


async def _instagram(account: SocialAccount, tags: list[str], state: dict) -> list[Incoming]:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No Instagram access token.", reconnect=True)
    cache: dict = state.setdefault("ig_hashtags", {})
    out: list[Incoming] = []
    async with net.client() as c:
        for tag in tags[:MAX_IG_HASHTAGS]:
            hid = cache.get(tag)
            if not hid:
                d = graph_check(await c.get(f"{graph_api.GRAPH}/ig_hashtag_search", params={
                    "user_id": account.external_id, "q": tag, "access_token": tok}),
                    "Instagram hashtag search", "instagram", "Instagram Public Content Access")
                hid = ((d.get("data") or [{}])[0]).get("id")
                if not hid:
                    continue
                cache[tag] = hid
            d = graph_check(await c.get(f"{graph_api.GRAPH}/{hid}/recent_media", params={
                "user_id": account.external_id, "limit": 25, "access_token": tok,
                "fields": "id,caption,permalink,timestamp,like_count,comments_count,media_type"}),
                "Instagram hashtag posts", "instagram", "Instagram Public Content Access")
            for m in d.get("data", []):
                if not m.get("id"):
                    continue
                out.append(Incoming(
                    external_id=str(m["id"]), kind="mention", text=m.get("caption"), url=m.get("permalink"),
                    author_name="Instagram post", published_at=parse_time(m.get("timestamp")), keyword=f"#{tag}",
                    engagement=(m.get("like_count") or 0) + (m.get("comments_count") or 0), platform="instagram",
                    raw=m,
                ))
    return out


# ------------------------------------------------------------------ orchestration
async def first_account(session: AsyncSession, platform: str) -> SocialAccount | None:
    return await session.scalar(select(SocialAccount).where(
        SocialAccount.platform == platform, SocialAccount.status == "connected").order_by(SocialAccount.created_at))


def _due(state: dict, source: str, now: datetime, force: bool) -> bool:
    last = parse_time((state.get(source) or {}).get("last_run_at"))
    if last is None:
        return True
    minimum = timedelta(minutes=10) if force else timedelta(minutes=LISTENING_SOURCES[source]["interval_minutes"])
    return now - last >= minimum


def _keep(it: Incoming, excluded: list[str], langs: list[str], kw: str, strict: bool) -> bool:
    text = " ".join(filter(None, [it.title, it.text]))
    low = text.lower()
    if any(x and x.lower() in low for x in excluded):
        return False
    if langs and it.language and it.language.split("-")[0].lower() not in {lang.lower() for lang in langs}:
        return False
    if it.published_at and it.published_at < _now() - timedelta(days=RECENT_DAYS * 4):
        return False
    return not strict or _mentions_keyword(text, kw)


async def run(session: AsyncSession, org_id: uuid.UUID, settings: InboxSettings, *, force: bool = False,
              only: list[str] | None = None) -> dict:
    """Run every enabled, due listening source. Returns {"created": [items], "sources": {...}}."""
    keywords = [str(k).strip() for k in (settings.keywords or []) if str(k).strip()][:MAX_KEYWORDS]
    excluded = [str(x).strip() for x in (settings.excluded or []) if str(x).strip()]
    langs = [str(x).strip() for x in (settings.languages or []) if str(x).strip()]
    sources = {**DEFAULT_SOURCES, **(settings.sources or {})}
    state = dict(settings.state or {})
    src_state: dict = dict(state.get("sources") or {})
    now = _now()
    created: list[InboxItem] = []
    if not keywords or not settings.listening_enabled:
        return {"created": [], "sources": src_state}
    for source, spec in LISTENING_SOURCES.items():
        if only and source not in only:
            continue
        if not sources.get(source):
            continue
        if not _due(src_state, source, now, force):
            continue
        account = await first_account(session, spec["needs"]) if spec["needs"] else None
        entry = {"last_run_at": now.isoformat(), "ok": True, "message": None, "found": 0}
        if spec["needs"] and account is None:
            entry.update(ok=False, message=f"Connect {LABELS[spec['needs']]} in Settings → Social accounts to search it.")
            src_state[source] = entry
            continue
        found: list[Incoming] = []
        try:
            if source == "instagram":
                tags = [t for t in (_hashtag(k) for k in keywords) if t]
                for it in await _instagram(account, tags, state):
                    if _keep(it, excluded, langs, it.keyword or "", False):
                        found.append(it)
            else:
                kws = keywords[:MAX_YOUTUBE_KEYWORDS] if source == "youtube" else keywords
                for kw in kws:
                    if source == "web":
                        rows = await _web(session, kw)
                    elif source == "news":
                        rows = await _news(session, kw)
                    elif source == "bluesky":
                        rows = await _bluesky(account, kw, langs)
                    elif source == "mastodon":
                        rows = await _mastodon(account, kw)
                    else:
                        rows = await _youtube(session, account, kw, langs)
                    for it in rows:
                        it.keyword = it.keyword or kw
                        if _keep(it, excluded, langs, kw, strict=source in ("bluesky", "mastodon", "youtube")):
                            found.append(it)
        except InboxPermissionError as e:
            entry.update(ok=False, message=(
                "Instagram hashtag search needs Meta's Instagram Public Content Access feature. Skipped."
                if source == "instagram" else e.message), missing_permission=e.permission)
        except (PublishError, httpx.HTTPError) as e:
            entry.update(ok=False, message=getattr(e, "message", None) or f"{type(e).__name__}: {e}"[:300])
        except Exception as e:  # noqa: BLE001 - search providers raise their own errors; never stop other sources
            log.warning("listening source %s failed: %s", source, e)
            entry.update(ok=False, message=clip(str(e), 300))
        if found:
            new = await store(session, org_id, found, account=account, source="listening", platform=source)
            entry["found"] = len(new)
            created.extend(new)
        src_state[source] = entry
    state["sources"] = src_state
    settings.state = state
    return {"created": created, "sources": src_state}
