"""Web search over the configured search providers (Settings → Integrations → Search and hosting).

Providers are tried in the order they were added; DuckDuckGo (`ddgs`, no key) is used when none
is configured. Every provider returns the same shape: [{title, url, snippet}].
"""

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.integrations import tester
from app.core.crypto import decrypt_json
from app.models import ProviderConfig
from app.services.llm.catalog import BY_ID

log = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class SearchError(Exception):
    pass


def _clean(items: list[dict], n: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for it in items:
        url = str(it.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        out.append({"title": str(it.get("title") or "").strip()[:300], "url": url,
                    "snippet": str(it.get("snippet") or "").strip()[:600]})
        if len(out) >= n:
            break
    return out


async def _ddgs(query: str, n: int, base_url: str, secrets: dict) -> list[dict]:
    def run() -> list[dict]:
        from ddgs import DDGS

        return list(DDGS(timeout=15).text(query, max_results=n) or [])

    rows = await asyncio.to_thread(run)
    return [{"title": r.get("title"), "url": r.get("href") or r.get("url"), "snippet": r.get("body")} for r in rows]


async def _searxng(query: str, n: int, base_url: str, secrets: dict) -> list[dict]:
    if not base_url:
        raise SearchError("SearXNG needs its address, for example http://localhost:8888.")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{base_url.rstrip('/')}/search", params={"q": query, "format": "json"},
                        headers={"Accept": "application/json"})
        if r.status_code == 403:
            raise SearchError("SearXNG refused JSON output. Enable 'json' under search.formats in its settings.yml.")
        r.raise_for_status()
        rows = r.json().get("results") or []
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")} for x in rows]


async def _brave(query: str, n: int, base_url: str, secrets: dict) -> list[dict]:
    key = secrets.get("api_key")
    if not key:
        raise SearchError("Brave Search needs an API key.")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{base_url.rstrip('/')}/web/search", params={"q": query, "count": min(n, 20)},
                        headers={"X-Subscription-Token": key, "Accept": "application/json"})
        r.raise_for_status()
        rows = (r.json().get("web") or {}).get("results") or []
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("description")} for x in rows]


async def _tavily(query: str, n: int, base_url: str, secrets: dict) -> list[dict]:
    key = secrets.get("api_key")
    if not key:
        raise SearchError("Tavily needs an API key.")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{base_url.rstrip('/')}/search", headers={"Authorization": f"Bearer {key}"},
                         json={"api_key": key, "query": query, "max_results": min(n, 20)})
        r.raise_for_status()
        rows = r.json().get("results") or []
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")} for x in rows]


async def _serper(query: str, n: int, base_url: str, secrets: dict) -> list[dict]:
    key = secrets.get("api_key")
    if not key:
        raise SearchError("Serper needs an API key.")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{base_url.rstrip('/')}/search", headers={"X-API-KEY": key},
                         json={"q": query, "num": min(n, 20)})
        r.raise_for_status()
        rows = r.json().get("organic") or []
    return [{"title": x.get("title"), "url": x.get("link"), "snippet": x.get("snippet")} for x in rows]


STYLES = {"ddgs": _ddgs, "searxng": _searxng, "brave": _brave, "tavily": _tavily, "serper": _serper}


async def _run(cfg_provider: str, base_url: str | None, secrets: dict, query: str, n: int) -> list[dict]:
    spec = BY_ID.get(cfg_provider)
    style = spec.style if spec else cfg_provider
    fn = STYLES.get(style)
    if fn is None:
        raise SearchError(f"Unsupported search provider: {cfg_provider}")
    base = base_url or (spec.base_url if spec else "")
    return _clean(await fn(query, n, base, secrets), n)


async def web_search(session: AsyncSession, query: str, n: int = 10) -> list[dict]:
    """Search the web through the configured providers. Raises SearchError when every provider fails."""
    query = (query or "").strip()
    if not query:
        return []
    n = max(1, min(n, 20))
    cfgs = (await session.scalars(
        select(ProviderConfig).where(ProviderConfig.kind == "search", ProviderConfig.enabled.is_(True))
        .order_by(ProviderConfig.created_at)
    )).all()
    attempts: list[tuple[str, str, str | None, dict]] = [
        (c.name, c.provider, c.base_url, decrypt_json(c.secrets_enc)) for c in cfgs
    ] or [("DuckDuckGo", "ddgs", None, {})]
    errors: list[str] = []
    for name, provider, base, secrets in attempts:
        try:
            return await _run(provider, base, secrets, query, n)
        except Exception as e:
            log.info("search via %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    raise SearchError("Web search failed. " + " | ".join(errors)[:800])


@tester("ddgs", "searxng", "brave", "tavily", "serper")
async def _test_search(cfg: ProviderConfig) -> str:
    rows = await _run(cfg.provider, cfg.base_url, decrypt_json(cfg.secrets_enc), "HRP AI HRMS software", 5)
    if not rows:
        return "Connected, but the test search returned no results."
    return f"Connected. The test search returned {len(rows)} results."
