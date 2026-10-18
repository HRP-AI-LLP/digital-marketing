"""Free keyword data (no paid key needed). Used whenever DataForSEO isn't configured.

- Google Autocomplete (suggestqueries.google.com, no key): keyword suggestions. The order and how often a phrase
  comes back across the seed's expansions give an *estimated relative demand* (0–100), never a search volume.
- Web search (app/services/search.py: DuckDuckGo / SearXNG / ... whatever is configured): question-style related
  searches, and approximate Google-like result positions for rank checks and SERP-overlap clustering.
- Search Console (already in the app): real impressions, clicks and average positions for the site's own queries.
- Competitor keyword gap: topics from competitors' crawled pages (titles, H1, H2) compared with our Search Console
  queries and our own pages.
Everything from here is labelled `estimated` / `approximate`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Competitor, KnowledgeDocument, KnowledgeSource, Organization
from app.services import search
from app.services.keywords import service as svc
from app.services.keywords.provider import LOCATIONS, Result, clean_domain
from app.services.llm import router as llm_router

log = logging.getLogger(__name__)

TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests put an httpx.MockTransport here
AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"
PAUSE_SECONDS = 0.4  # between free web searches (be polite; tests set 0)
COUNTRY = {2356: "in", 2840: "us", 2826: "gb", 2784: "ae", 2702: "sg", 2036: "au", 2124: "ca", 2276: "de", 2682: "sa",
           2458: "my"}
MODIFIERS = ["{s}", "how {s}", "what is {s}", "best {s}", "{s} vs", "{s} for", "{s} near me", "{s} price", "{s} {country}"]
LETTERS = "abcdefghijklmnopqrstuvwxyz"
QUESTION = re.compile(r"^(how|what|why|when|which|who|where|is|are|can|does|do|should)\b", re.I)
GENERIC = {"home", "homepage", "about", "about us", "contact", "contact us", "blog", "careers", "login", "sign in", "sign up",
           "privacy policy", "terms", "terms and conditions", "terms of service", "cookie policy", "faq", "faqs", "pricing",
           "news", "resources", "our team", "team", "get started", "book a demo", "request a demo", "thank you",
           "page not found", "404", "sitemap", "testimonials", "our clients", "partners", "support", "help"}


# ============================================================ Google Autocomplete
async def autocomplete(q: str, *, hl: str = "en", gl: str = "in") -> list[str]:
    params = {"client": "firefox", "q": q, "hl": hl, "gl": gl}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), transport=TRANSPORT,
                                 headers={"User-Agent": "Mozilla/5.0 (HRP Digital Marketing Studio)"}) as c:
        r = await c.get(AUTOCOMPLETE_URL, params=params)
        r.raise_for_status()
        try:
            data = json.loads(r.content.decode(r.encoding or "utf-8", errors="replace"))
        except ValueError:
            return []
    if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list):
        return [" ".join(str(x).split())[:200] for x in data[1] if str(x).strip()]
    return []


def _expansions(seed: str, location_code: int, deep: bool) -> list[str]:
    country = LOCATIONS.get(location_code, "India").lower()
    out = [m.format(s=seed, country=country) for m in MODIFIERS]
    if deep:
        out += [f"{seed} {ch}" for ch in LETTERS]
    return list(dict.fromkeys(out))


async def suggestions(session: AsyncSession, org: Organization, seed: str, *, location_code: int, language_code: str,
                      deep: bool = True, progress=None) -> tuple[list[dict], dict]:
    """Autocomplete rows for one seed with an estimated relative demand. Returns (rows, meta)."""
    gl = COUNTRY.get(location_code, "in")
    scores: dict[str, float] = {}
    shown: dict[str, str] = {}
    calls = failures = 0
    for q in _expansions(seed, location_code, deep):
        params = {"q": q, "hl": language_code, "gl": gl}

        async def call(q=q):
            return Result([{"s": s} for s in await autocomplete(q, hl=language_code, gl=gl)])
        try:
            res, _ = await svc.cached_call(session, org, "google_autocomplete", "autocomplete", params, ttl_days=7, call=call)
        except Exception as e:  # noqa: BLE001 - free source: skip what fails
            failures += 1
            log.info("autocomplete failed for %r: %s", q, e)
            if failures >= 4 and calls == 0:
                break
            continue
        calls += 1
        for rank, row in enumerate(res.rows[:10]):
            k = svc.norm(row["s"])
            if not k:
                continue
            shown.setdefault(k, row["s"])
            scores[k] = scores.get(k, 0.0) + (10 - rank) / 10
    top = max(scores.values(), default=0) or 1
    rows = [{"keyword": shown[k], "volume": None, "kd": None, "cpc": None, "competition": None, "intent": None,
             "trend": [], "trend_months": [], "serp_features": [], "source": "autocomplete", "estimated": True,
             "demand": max(1, round(100 * v / top))} for k, v in scores.items()]
    return rows, {"autocomplete_calls": calls, "autocomplete_failed": failures}


# ============================================================ web search (free providers)
async def serp(session: AsyncSession, org: Organization, keyword: str, *, n: int = 20, ttl_days: float = 7) -> list[dict]:
    """Approximate result positions from the configured free web search: [{position, url, domain, title}]."""
    async def call():
        rows = await search.web_search(session, keyword, n=n)
        await asyncio.sleep(PAUSE_SECONDS)
        return Result([{"position": i + 1, "url": r["url"], "domain": clean_domain(r["url"]), "title": r.get("title") or ""}
                       for i, r in enumerate(rows)])
    res, _ = await svc.cached_call(session, org, "web_search", "serp", {"q": keyword, "n": n}, ttl_days=ttl_days, call=call)
    return res.rows


async def questions(session: AsyncSession, org: Organization, seed: str) -> list[dict]:
    """'People also ask'-style phrases: question titles from free web results for the seed."""
    async def call():
        rows, _ = await search.search_all(session, f"{seed} questions", n=15, max_providers=2)
        await asyncio.sleep(PAUSE_SECONDS)
        out = []
        for r in rows:
            t = re.split(r"\s[|–—-]\s", str(r.get("title") or ""))[0].strip().rstrip("?").strip()
            if QUESTION.match(t) and 3 <= len(t.split()) <= 14:
                out.append({"q": t.lower()})
        return Result(out)
    try:
        res, _ = await svc.cached_call(session, org, "web_search", "questions", {"q": seed}, ttl_days=7, call=call)
    except Exception as e:  # noqa: BLE001
        log.info("question search failed: %s", e)
        return []
    return [{"keyword": r["q"], "volume": None, "kd": None, "cpc": None, "competition": None, "intent": "informational",
             "trend": [], "trend_months": [], "serp_features": [], "source": "web_search", "estimated": True,
             "question": True} for r in res.rows]


# ============================================================ AI intent classification
INTENT_SYSTEM = ("You classify the search intent of keywords: informational (wants to learn), commercial (comparing options), "
                 "transactional (ready to buy or sign up) or navigational (looking for a specific brand or site).")


async def classify_intents(session: AsyncSession, rows: list[dict], limit: int = 150) -> int:
    todo = [r for r in rows if not r.get("intent") or r.get("intent_guessed")][:limit]
    if not todo:
        return 0
    prompt = ("Keywords:\n" + "\n".join(f"- {r['keyword']}" for r in todo) +
              '\n\nReturn JSON: {"intents": {"<keyword exactly as given>": "informational|commercial|transactional|navigational"}}')
    try:
        data = await llm_router.complete_json(session, "extract", prompt, system=INTENT_SYSTEM, temperature=0, max_tokens=4000)
    except (llm_router.NoProviderError, llm_router.AllProvidersFailed, ValueError):
        return 0
    got = {svc.norm(k): str(v).lower() for k, v in ((data or {}).get("intents") or {}).items()} if isinstance(data, dict) else {}
    n = 0
    for r in todo:
        v = got.get(svc.norm(r["keyword"]))
        if v in ("informational", "commercial", "transactional", "navigational"):
            r["intent"], r["intent_guessed"] = v, True
            n += 1
    return n


# ============================================================ free keyword gap (crawled competitor pages)
def _topic(text: str) -> str | None:
    t = re.split(r"\s[|–—:-]\s", " ".join(str(text or "").split()))[0].strip(" .!?")
    if not t or len(t) > 90:
        return None
    words = t.split()
    if not 2 <= len(words) <= 9 or svc.norm(t) in GENERIC:
        return None
    return t


def page_topics(title: str | None, meta: dict | None) -> list[str]:
    meta = meta or {}
    cands = [title or meta.get("title") or "", *(meta.get("h1") or [])[:2], *(meta.get("h2") or [])[:8]]
    out, seen = [], set()
    for c in cands:
        t = _topic(c)
        k = svc.norm(t) if t else ""
        if t and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _match(topic_tokens: set[str], phrases: list[tuple[set[str], dict]]) -> dict | None:
    best, score = None, 0.0
    for toks, row in phrases:
        if not toks:
            continue
        inter = len(topic_tokens & toks)
        s = inter / min(len(topic_tokens), len(toks))
        if inter >= 2 or (inter == 1 and len(topic_tokens) == 1 and len(toks) == 1):
            if s >= 0.6 and (s > score or (s == score and best and (row.get("position") or 99) < (best.get("position") or 99))):
                best, score = row, s
    return best


async def _competitor_pages(session: AsyncSession, domain: str) -> tuple[list[tuple[str, str | None, dict]], str]:
    """[(url, title, meta)] for a competitor's crawled pages; falls back to reading its home page now."""
    comp = await session.scalar(select(Competitor).where(Competitor.domain == domain).limit(1))
    docs: list = []
    if comp is not None:
        docs = (await session.execute(
            select(KnowledgeDocument.url, KnowledgeDocument.title, KnowledgeDocument.meta)
            .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
            .where(KnowledgeSource.kind == "competitor", KnowledgeSource.competitor_id == comp.id).limit(300)
        )).all()
    if docs:
        return [(u, t, m or {}) for u, t, m in docs], "crawled"
    try:
        from app.services import crawler

        page = await crawler.fetch_page(f"https://{domain}/")
    except Exception as e:  # noqa: BLE001
        log.info("couldn't read %s: %s", domain, e)
        page = None
    if page is None:
        return [], "unreadable"
    return [(page.url, page.title, {"title": page.title, "h1": page.h1, "h2": page.h2})], "home_page"


async def gap(session: AsyncSession, org: Organization, p: dict, progress=None) -> dict:
    gsc_data = await svc.latest_gsc(session)
    gsc_rows = [r for r in ((gsc_data or {}).get("queries") or []) if r.get("query")]
    gsc_phr = [(svc.tokens(r["query"]), {"query": r["query"], "position": r.get("position"),
                                          "impressions": r.get("impressions")}) for r in gsc_rows]
    own_docs = (await session.execute(
        select(KnowledgeDocument.url, KnowledgeDocument.title, KnowledgeDocument.meta)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(KnowledgeSource.kind == "website").limit(500)
    )).all()
    page_phr = [(svc.tokens(t), {"url": u, "topic": t}) for u, title, m in own_docs for t in page_topics(title, m)]
    by_topic: dict[str, dict] = {}
    notes = []
    for comp in p["competitors"]:
        if progress:
            await progress(f"Reading {comp}'s pages")
        pages, how = await _competitor_pages(session, comp)
        if how == "home_page":
            notes.append(f"{comp} hasn't been crawled yet, so only its home page was used. Add it as a competitor in "
                         "Company intelligence and crawl it for a fuller picture.")
        elif how == "unreadable":
            notes.append(f"Couldn't read {comp}'s website.")
        for url, title, meta in pages:
            for t in page_topics(title, meta):
                toks = svc.tokens(t)
                if not toks:
                    continue
                key = " ".join(sorted(toks))
                row = by_topic.setdefault(key, {"keyword": t.lower(), "volume": None, "kd": None, "cpc": None,
                                                "competition": None, "intent": svc.guess_intent(t), "trend": [],
                                                "trend_months": [], "serp_features": [], "competitors": {},
                                                "estimated": True, "source": "competitor_pages"})
                row["competitors"].setdefault(comp, {"position": None, "url": url})
    rows = []
    for key, row in by_topic.items():
        toks = set(key.split())
        g = _match(toks, gsc_phr)
        pg = _match(toks, page_phr) if not g else None
        row["ours"] = round(float(g["position"])) if g and g.get("position") else None
        row["our_query"] = g["query"] if g else None
        row["gsc_impressions"] = g.get("impressions") if g else None
        row["our_url"] = pg["url"] if pg else None
        if g and row["ours"] is not None:
            row["category"] = "strong" if row["ours"] <= 10 else "weak"
        elif pg:
            row["category"] = "weak"  # we have a page on it, but Search Console shows no ranking for it
            row["note"] = "You have a page on this, but it isn't showing up in Search Console yet."
        else:
            row["category"] = "missing"
        row["shared"] = row["category"] != "missing"
        row["best_competitor"] = None
        row["demand"] = len(row["competitors"])  # how many competitors cover it (relative, estimated)
        rows.append(row)
    rows.sort(key=lambda r: (-r["demand"], r["category"] != "missing", r["keyword"]))
    if gsc_data is None:
        notes.append("Connect Search Console in Analytics so we can tell which topics you already rank for.")
    counts = {c: sum(1 for r in rows if r["category"] == c) for c in ("missing", "weak", "strong")}
    counts["shared"] = sum(1 for r in rows if r["shared"])
    return {"rows": rows[:1000], "meta": {
        "source": "free", "provider": None, "estimated": True, "approximate": True, "cost_usd": 0.0, "counts": counts,
        "count": len(rows), "notes": notes,
        "note": ("Approximate: topics come from competitors' page titles and headings, compared with your Search Console "
                 "queries and your own pages. Add DataForSEO for real keyword rankings, volumes and difficulty."),
    }}
