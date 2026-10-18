"""Keyword research and competitor keyword gap, with response caching, spend tracking and a daily budget.

Data comes from the configured keyword data provider (Settings → Integrations → DataForSEO). Without one, research
falls back to free data already in the app: Search Console queries (latest `gsc` analytics snapshot) and AI keyword
ideas. Those rows have no search volumes and are flagged `estimated`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import (
    AnalyticsSnapshot, Competitor, KeywordCache, KeywordReport, KeywordSettings, Organization, ProviderConfig, ProviderSpend,
)
from app.services.keywords import provider as prov_mod
from app.services.keywords.provider import KeywordDataProvider, ProviderError, Result, clean_domain
from app.services.llm import router as llm_router

log = logging.getLogger(__name__)
Progress = Callable[[str], Awaitable[None]] | None

DEFAULTS: dict = {
    "location_code": 2356, "language_code": "en", "device": "desktop",
    "daily_budget_usd": 1.0,  # 0 = no limit
    "max_tracked": 100, "check_time": "06:00", "rank_tracking_enabled": True,
    "alerts": {"enabled": True, "drop": 5, "top3": True, "top10": True},
    "research_limit": 100, "gap_limit": 300, "cache_days": 30, "serp_depth": 100, "cluster_overlap": 3,
    "our_domain": "",
}
NO_PROVIDER_NOTE = ("Free data: Google Autocomplete suggestions, related questions from web search, your Search Console "
                    "queries and AI ideas. There are no search volumes; “demand” is an estimated relative score (0–100). "
                    "Add DataForSEO (optional, paid) in Settings → Integrations for volumes, difficulty and cost per click.")
NO_DATA = ("No keyword ideas came back. Google Autocomplete and web search may be busy; try again in a few minutes. "
           "Connecting Search Console in Analytics or adding an AI text model gives more ideas.")
GAP_FREE_NOTE = ("Free and approximate: compares topics on competitors' pages with your Search Console queries and pages. "
                 "Add DataForSEO (optional) for real keyword rankings.")
STOP = {"a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "is", "are", "by", "at", "from", "vs", "best"}
DOMAIN_RE = re.compile(r"^(https?://)?(www\.)?([a-z0-9-]+\.)+[a-z]{2,}(/.*)?$", re.I)


class BudgetError(Exception):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def org_tz(org: Organization | None) -> ZoneInfo:
    try:
        return ZoneInfo(org.timezone if org and org.timezone else "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


def local_day(org: Organization | None, at: datetime | None = None) -> str:
    return (at or now()).astimezone(org_tz(org)).date().isoformat()


def looks_like_domain(text: str) -> bool:
    t = (text or "").strip()
    return " " not in t and bool(DOMAIN_RE.match(t))


# ============================================================ settings
async def get_settings_row(session: AsyncSession, org_id: uuid.UUID) -> KeywordSettings:
    row = await session.scalar(select(KeywordSettings).where(KeywordSettings.organization_id == org_id))
    if row is None:
        row = KeywordSettings(organization_id=org_id, config={}, data={})
        session.add(row)
        await session.flush()
    return row


def effective_config(row: KeywordSettings | None) -> dict:
    cfg = {**DEFAULTS, **{k: v for k, v in ((row.config if row else None) or {}).items() if k in DEFAULTS}}
    cfg["alerts"] = {**DEFAULTS["alerts"], **(cfg.get("alerts") or {})}
    return cfg


async def config_for(session: AsyncSession, org_id: uuid.UUID) -> dict:
    row = await session.scalar(select(KeywordSettings).where(KeywordSettings.organization_id == org_id))
    return effective_config(row)


async def our_domain(session: AsyncSession, org: Organization | None, cfg: dict | None = None) -> str:
    cfg = cfg or (await config_for(session, org.id) if org else DEFAULTS)
    return clean_domain(cfg.get("our_domain") or (org.website if org else "") or "")


# ============================================================ provider
async def provider_config(session: AsyncSession) -> ProviderConfig | None:
    return await session.scalar(
        select(ProviderConfig).where(ProviderConfig.kind == "seo_data", ProviderConfig.enabled.is_(True))
        .order_by(ProviderConfig.created_at)
    )


def build_provider(cfg: ProviderConfig) -> KeywordDataProvider:
    secrets = decrypt_json(cfg.secrets_enc)
    if cfg.provider == "dataforseo":
        return prov_mod.DataForSEO(secrets.get("login") or "", secrets.get("password") or "", cfg.base_url)
    raise ProviderError(f"Unsupported keyword data provider: {cfg.provider}")


async def get_provider(session: AsyncSession) -> KeywordDataProvider | None:
    cfg = await provider_config(session)
    if cfg is None:
        return None
    try:
        return build_provider(cfg)
    except ProviderError as e:
        log.info("keyword provider not usable: %s", e)
        return None


# ============================================================ cache & spend
def cache_key(provider_id: str, method: str, params: dict) -> str:
    raw = json.dumps({"p": provider_id, "m": method, "q": params}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def cached(session: AsyncSession, key: str) -> KeywordCache | None:
    row = await session.scalar(select(KeywordCache).where(KeywordCache.cache_key == key))
    return row if row is not None and row.expires_at > now() else None


async def spend_on(session: AsyncSession, day: str) -> float:
    total = await session.scalar(select(func.coalesce(func.sum(ProviderSpend.cost_usd), 0)).where(ProviderSpend.day == day))
    return float(total or 0)


async def ensure_budget(session: AsyncSession, org: Organization, estimate: float, cfg: dict | None = None) -> None:
    cfg = cfg or await config_for(session, org.id)
    budget = float(cfg.get("daily_budget_usd") or 0)
    if budget <= 0:
        return
    spent = await spend_on(session, local_day(org))
    if spent + estimate > budget + 1e-9:
        raise BudgetError(f"Today's keyword data budget (${budget:.2f}) is used up (${spent:.2f} spent). "
                          "It resets tomorrow, or raise it in Keywords → Settings.")


def record_spend(session: AsyncSession, org: Organization, provider_id: str, endpoint: str, purpose: str, cost: float,
                 ok: bool = True, error: str | None = None) -> None:
    session.add(ProviderSpend(organization_id=org.id, provider=provider_id, endpoint=endpoint[:120], purpose=purpose[:40],
                              cost_usd=round(float(cost or 0), 6), day=local_day(org), ok=ok,
                              error=(error or None) and error[:1000]))


async def cached_call(session: AsyncSession, org: Organization, provider_id: str, method: str, params: dict, *,
                      ttl_days: float, call, purpose: str = "research", paid: bool = False, estimate: float = 0.0,
                      cfg: dict | None = None) -> tuple[Result, bool]:
    """Serve `call()` (→ Result) from the cache when fresh. Paid calls check the daily budget and record their cost."""
    key = cache_key(provider_id, method, params)
    row = await cached(session, key)
    if row is not None:
        resp = row.response if isinstance(row.response, dict) else {"rows": row.response}
        return Result(resp.get("rows") or [], 0.0, resp.get("meta") or {}), True
    if paid:
        await ensure_budget(session, org, estimate, cfg)
    try:
        res: Result = await call()
    except ProviderError as e:
        if paid:
            record_spend(session, org, provider_id, method, purpose, 0, ok=False, error=str(e))
        raise
    if paid:
        record_spend(session, org, provider_id, method, purpose, res.cost)
    expires = now() + timedelta(days=ttl_days)
    old = await session.scalar(select(KeywordCache).where(KeywordCache.cache_key == key))
    if old is None:
        session.add(KeywordCache(organization_id=org.id, provider=provider_id, endpoint=method, cache_key=key, request=params,
                                 response={"rows": res.rows, "meta": res.meta}, cost_usd=res.cost, expires_at=expires))
    else:
        old.response, old.cost_usd, old.created_at, old.expires_at = {"rows": res.rows, "meta": res.meta}, res.cost, now(), expires
    await session.flush()
    return res, False


async def fetch(session: AsyncSession, org: Organization, prov: KeywordDataProvider, method: str, purpose: str, *,
                ttl_days: float, cfg: dict | None = None, **params) -> tuple[Result, bool]:
    """Call the paid provider's `prov.<method>(**params)` through the cache. Returns (result, served_from_cache)."""
    return await cached_call(session, org, prov.id, method, params, ttl_days=ttl_days, purpose=purpose, paid=True,
                             estimate=prov.estimate(method, **params), cfg=cfg,
                             call=lambda: getattr(prov, method)(**params))


async def spend_summary(session: AsyncSession, org: Organization, cfg: dict) -> dict:
    today = local_day(org)
    month_start = today[:8] + "01"
    month = await session.scalar(select(func.coalesce(func.sum(ProviderSpend.cost_usd), 0)).where(ProviderSpend.day >= month_start))
    rows = (await session.execute(
        select(ProviderSpend.purpose, func.count(), func.coalesce(func.sum(ProviderSpend.cost_usd), 0))
        .where(ProviderSpend.day >= month_start).group_by(ProviderSpend.purpose)
    )).all()
    since = (date.fromisoformat(today) - timedelta(days=29)).isoformat()
    daily = (await session.execute(
        select(ProviderSpend.day, func.coalesce(func.sum(ProviderSpend.cost_usd), 0)).where(ProviderSpend.day >= since)
        .group_by(ProviderSpend.day).order_by(ProviderSpend.day)
    )).all()
    return {
        "today_usd": round(await spend_on(session, today), 4), "month_usd": round(float(month or 0), 4),
        "daily_budget_usd": float(cfg.get("daily_budget_usd") or 0),
        "by_purpose": [{"purpose": p, "calls": n, "cost_usd": round(float(c), 4)} for p, n, c in rows],
        "daily": [{"day": d, "cost_usd": round(float(c), 4)} for d, c in daily],
    }


# ============================================================ Search Console (free data)
async def latest_gsc(session: AsyncSession) -> dict | None:
    snap = await session.scalar(
        select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "gsc").order_by(AnalyticsSnapshot.created_at.desc())
    )
    return (snap.data or {}) if snap else None


def gsc_index(data: dict | None) -> dict[str, dict]:
    return {norm(str(r.get("query") or "")): r for r in ((data or {}).get("queries") or []) if r.get("query")}


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def tokens(text: str) -> set[str]:
    return {t for t in norm(text).split() if len(t) > 2 and t not in STOP}


def guess_intent(keyword: str) -> str | None:
    k = f" {norm(keyword)} "
    if re.search(r" (buy|price|pricing|cost|quote|demo|trial|order|hire|near me) ", k):
        return "transactional"
    if re.search(r" (best|top|vs|versus|review|reviews|compare|comparison|alternative|alternatives|software|tool|tools|app|company|companies|services?) ", k):
        return "commercial"
    if re.search(r" (how|what|why|when|guide|tips|ideas|examples|meaning|definition|template|checklist) ", k):
        return "informational"
    return None


def _attach_gsc(rows: list[dict], gsc: dict[str, dict]) -> None:
    for r in rows:
        g = gsc.get(norm(r["keyword"]))
        if g:
            r["gsc"] = {"clicks": g.get("clicks"), "impressions": g.get("impressions"), "position": g.get("position"),
                        "ctr": g.get("ctr")}


# ============================================================ research
def research_params(cfg: dict, body: dict) -> dict:
    seeds = [" ".join(str(s).split())[:120] for s in (body.get("seeds") or []) if str(s).strip()][:5]
    mode = body.get("mode") or "suggestions"
    if mode != "domain" and seeds and all(looks_like_domain(s) for s in seeds):
        mode = "domain"
    if mode == "domain":
        seeds = [clean_domain(s) for s in seeds][:1]
    return {
        "seeds": seeds, "mode": mode if mode in ("suggestions", "related", "ideas", "domain") else "suggestions",
        "location_code": int(body.get("location_code") or cfg["location_code"]),
        "language_code": str(body.get("language_code") or cfg["language_code"]),
        "limit": max(10, min(int(body.get("limit") or cfg["research_limit"]), 1000)),
    }


def _research_calls(p: dict) -> list[tuple[str, dict]]:
    base = {"location_code": p["location_code"], "language_code": p["language_code"], "limit": p["limit"]}
    if p["mode"] == "domain":
        return [("ranked_keywords", {"domain": p["seeds"][0], **base})]
    if p["mode"] == "ideas":
        return [("keyword_ideas", {"seeds": p["seeds"], **base})]
    method = "related_keywords" if p["mode"] == "related" else "keyword_suggestions"
    return [(method, {"seed": s, **base}) for s in p["seeds"]]


async def estimate_research(session: AsyncSession, prov: KeywordDataProvider | None, p: dict) -> dict:
    if prov is None:
        return {"cost_usd": 0.0, "cached": False, "provider": None, "note": "Free: Search Console and AI ideas (no volumes)."}
    total, all_cached = 0.0, True
    for method, params in _research_calls(p):
        if await cached(session, cache_key(prov.id, method, params)) is None:
            total += prov.estimate(method, **params)
            all_cached = False
    return {"cost_usd": round(total, 4), "cached": all_cached, "provider": prov.label,
            "note": "Already fetched recently: free." if all_cached else "Estimated; the exact charge is recorded after it runs."}


async def run_research(session: AsyncSession, org: Organization, report: KeywordReport, progress: Progress = None) -> dict:
    cfg = await config_for(session, org.id)
    p = report.params
    prov = await get_provider(session)
    gsc_data = await latest_gsc(session)
    gsc = gsc_index(gsc_data)
    meta: dict = {"cost_usd": 0.0, "cached": 0, "calls": 0}
    rows: list[dict] = []
    if prov is not None:
        meta.update({"source": prov.id, "provider": prov.label, "estimated": False})
        for method, params in _research_calls(p):
            if progress:
                await progress(f"Getting keyword data for “{params.get('seed') or params.get('domain') or ', '.join(params.get('seeds') or [])}”")
            res, hit = await fetch(session, org, prov, method, "research", ttl_days=cfg["cache_days"], cfg=cfg, **params)
            meta["cost_usd"] = round(meta["cost_usd"] + res.cost, 6)
            meta["cached"] += int(hit)
            meta["calls"] += 1
            rows += res.rows
    else:
        meta.update({"source": "free", "provider": None, "estimated": True, "note": NO_PROVIDER_NOTE})
        rows = await _free_research(session, org, p, gsc_data, progress, meta)
    seen: dict[str, dict] = {}
    for r in rows:
        k = norm(r["keyword"])
        if not k:
            continue
        if k not in seen or (seen[k].get("volume") is None and r.get("volume") is not None):
            prev = seen.get(k)
            seen[k] = {**(prev or {}), **r} if prev else r
        else:  # same keyword from another source: fill what is missing (e.g. autocomplete demand on a GSC row)
            for f, v in r.items():
                if seen[k].get(f) in (None, [], "") and v not in (None, [], ""):
                    seen[k][f] = v
    out = list(seen.values())
    _attach_gsc(out, gsc)
    for r in out:
        if not r.get("intent"):
            g = guess_intent(r["keyword"])
            if g:
                r["intent"], r["intent_guessed"] = g, True
    if prov is None:
        from app.services.keywords import free

        if progress:
            await progress("Working out search intent")
        await free.classify_intents(session, out)
        _relative_demand(out)
    out.sort(key=lambda r: (r.get("volume") is None, -(r.get("volume") or 0), -(r.get("demand") or 0),
                            -((r.get("gsc") or {}).get("impressions") or r.get("impressions") or 0)))
    meta["count"] = len(out)
    meta["gsc_connected"] = gsc_data is not None
    return {"rows": out[:1000], "meta": meta}


IDEAS_SYSTEM = ("You suggest search keywords for a company's website. Return realistic phrases people type into Google. "
                "Never invent numbers such as search volumes, difficulty or prices.")


def _relative_demand(rows: list[dict]) -> None:
    """Estimated relative demand 0–100 from Autocomplete prominence and Search Console impressions."""
    imp = [((r.get("gsc") or {}).get("impressions") or r.get("impressions") or 0) for r in rows]
    top = max(imp, default=0) or 0
    for r, i in zip(rows, imp):
        d = r.get("demand") or 0
        if top and i:
            d = max(d, round(100 * (i / top) ** 0.5))
        r["demand"] = d or None


async def _free_research(session: AsyncSession, org: Organization, p: dict, gsc_data: dict | None, progress: Progress,
                         meta: dict) -> list[dict]:
    from app.services.keywords import free

    rows: list[dict] = []
    seed_tokens = set().union(*(tokens(s) for s in p["seeds"])) if p["seeds"] else set()
    ours = await our_domain(session, org)
    if p["mode"] == "domain" and p["seeds"] and p["seeds"][0] != ours:
        # someone else's site: free data can only show the topics its pages target
        pages, _ = await free._competitor_pages(session, p["seeds"][0])
        for url, title, m in pages:
            for t in free.page_topics(title, m):
                rows.append({"keyword": t.lower(), "volume": None, "kd": None, "cpc": None, "competition": None,
                             "intent": None, "trend": [], "trend_months": [], "serp_features": [], "url": url,
                             "source": "competitor_pages", "estimated": True})
        gsc_data = None
    for q in (gsc_data or {}).get("queries") or []:
        text = str(q.get("query") or "").strip()
        if not text:
            continue
        if p["mode"] != "domain" and seed_tokens and not (tokens(text) & seed_tokens):
            continue
        rows.append({"keyword": text, "volume": None, "kd": None, "cpc": None, "competition": None, "intent": None,
                     "trend": [], "trend_months": [], "serp_features": [], "source": "search_console", "estimated": True,
                     "position": q.get("position"), "impressions": q.get("impressions"), "clicks": q.get("clicks")})
    if p["mode"] != "domain":
        for seed in p["seeds"]:
            if progress:
                await progress(f"Reading Google Autocomplete for “{seed}”")
            sugg, m = await free.suggestions(session, org, seed, location_code=p["location_code"],
                                             language_code=p["language_code"], deep=p["mode"] != "related")
            meta["autocomplete_calls"] = meta.get("autocomplete_calls", 0) + m["autocomplete_calls"]
            rows += sugg
            if progress:
                await progress(f"Looking for questions people ask about “{seed}”")
            rows += await free.questions(session, org, seed)
    ai_rows: list[dict] = []
    try:
        if progress:
            await progress("Asking AI for more keyword ideas")
        from app.services.knowledge import company_context

        company = await company_context(session, 2000)
        prompt = (
            f"Seed keywords: {', '.join(p['seeds']) or '(none: use the company focus)'}\n"
            f"Market: {prov_mod.LOCATIONS.get(p['location_code'], 'India')}, language {p['language_code']}\n\n"
            f"Company facts:\n{company or '(not available)'}\n\n"
            "List 30 keyword phrases worth targeting: a mix of long-tail questions, comparisons and buying searches. "
            'Return JSON: {"keywords": [{"keyword": "...", "intent": "informational|commercial|transactional|navigational"}]}'
        )
        data = await llm_router.complete_json(session, "plan", prompt, system=IDEAS_SYSTEM, temperature=0.5)
        raw = data.get("keywords") if isinstance(data, dict) else data
        for i in raw or []:
            kw = " ".join(str((i or {}).get("keyword") if isinstance(i, dict) else i or "").split())[:200]
            if not kw:
                continue
            intent = str((i.get("intent") if isinstance(i, dict) else "") or "").lower()
            ai_rows.append({"keyword": kw, "volume": None, "kd": None, "cpc": None, "competition": None,
                            "intent": intent if intent in ("informational", "commercial", "transactional", "navigational") else None,
                            "trend": [], "trend_months": [], "serp_features": [], "source": "ai", "estimated": True})
    except (llm_router.NoProviderError, llm_router.AllProvidersFailed):
        pass
    rows += ai_rows
    if not rows:
        raise ValueError(NO_DATA)
    return rows


# ============================================================ keyword gap
def gap_params(cfg: dict, body: dict, ours: str) -> dict:
    comps: list[str] = []
    for c in body.get("competitors") or []:
        d = clean_domain(str(c))
        if d and d != ours and d not in comps and "." in d:
            comps.append(d)
    return {"our_domain": clean_domain(body.get("our_domain") or ours), "competitors": comps[:4],
            "location_code": int(body.get("location_code") or cfg["location_code"]),
            "language_code": str(body.get("language_code") or cfg["language_code"]),
            "limit": max(10, min(int(body.get("limit") or cfg["gap_limit"]), 1000))}


def _gap_calls(p: dict) -> list[tuple[str, str, dict]]:
    base = {"location_code": p["location_code"], "language_code": p["language_code"], "limit": p["limit"]}
    calls = []
    for comp in p["competitors"]:
        calls.append((comp, "shared", {"first": p["our_domain"], "second": comp, "intersections": True, **base}))
        calls.append((comp, "missing", {"first": comp, "second": p["our_domain"], "intersections": False, **base}))
    return calls


async def estimate_gap(session: AsyncSession, prov: KeywordDataProvider | None, p: dict) -> dict:
    if prov is None:
        return {"cost_usd": 0.0, "cached": False, "provider": None, "note": GAP_FREE_NOTE}
    total, all_cached = 0.0, True
    for _, _, params in _gap_calls(p):
        if await cached(session, cache_key(prov.id, "domain_intersection", params)) is None:
            total += prov.estimate("domain_intersection", **params)
            all_cached = False
    return {"cost_usd": round(total, 4), "cached": all_cached, "provider": prov.label,
            "note": "Already fetched recently: free." if all_cached else "Estimated; the exact charge is recorded after it runs."}


def classify(ours: int | None, theirs: list[int]) -> str:
    if ours is None:
        return "missing"
    if theirs and min(theirs) < ours:
        return "weak"
    return "strong"


async def run_gap(session: AsyncSession, org: Organization, report: KeywordReport, progress: Progress = None) -> dict:
    p = report.params
    if not p.get("our_domain"):
        raise ValueError("Add your website address in Settings → Organization (or in Keywords → Settings) first.")
    if not p.get("competitors"):
        raise ValueError("Pick at least one competitor domain.")
    prov = await get_provider(session)
    if prov is None:
        from app.services.keywords import free

        return await free.gap(session, org, p, progress)
    cfg = await config_for(session, org.id)
    by_kw: dict[str, dict] = {}
    meta = {"cost_usd": 0.0, "cached": 0, "calls": 0, "source": prov.id, "provider": prov.label, "estimated": False}
    for comp, part, params in _gap_calls(p):
        if progress:
            await progress(f"Comparing with {comp}")
        res, hit = await fetch(session, org, prov, "domain_intersection", "gap", ttl_days=7, cfg=cfg, **params)
        meta["cost_usd"] = round(meta["cost_usd"] + res.cost, 6)
        meta["cached"] += int(hit)
        meta["calls"] += 1
        for r in res.rows:
            k = norm(r["keyword"])
            row = by_kw.setdefault(k, {**{f: r.get(f) for f in ("keyword", "volume", "kd", "cpc", "competition", "intent",
                                                                  "trend", "trend_months", "serp_features")},
                                       "ours": None, "our_url": None, "competitors": {}})
            if part == "shared":
                mine, theirs = r.get("first"), r.get("second")
            else:
                mine, theirs = None, r.get("first")
            if mine and (row["ours"] is None or mine["position"] < row["ours"]):
                row["ours"], row["our_url"] = mine["position"], mine.get("url")
            if theirs:
                row["competitors"][comp] = {"position": theirs["position"], "url": theirs.get("url")}
    rows = []
    for row in by_kw.values():
        theirs = [c["position"] for c in row["competitors"].values() if c.get("position")]
        row["category"] = classify(row["ours"], theirs)
        row["shared"] = row["ours"] is not None and bool(theirs)
        row["best_competitor"] = min(theirs) if theirs else None
        rows.append(row)
    rows.sort(key=lambda r: -(r.get("volume") or 0))
    counts = {c: sum(1 for r in rows if r["category"] == c) for c in ("missing", "weak", "strong")}
    counts["shared"] = sum(1 for r in rows if r["shared"])
    meta.update({"counts": counts, "count": len(rows)})
    return {"rows": rows, "meta": meta}


async def competitor_domains(session: AsyncSession) -> list[dict]:
    rows = (await session.scalars(select(Competitor).where(Competitor.status != "rejected")
                                  .order_by(Competitor.status, Competitor.overlap_score.desc()).limit(20))).all()
    out, seen = [], set()
    for c in rows:
        d = clean_domain(c.domain or c.website)
        if d and d not in seen:
            seen.add(d)
            out.append({"domain": d, "name": c.name, "status": c.status})
    return out


async def suggest_competitors(session: AsyncSession, org: Organization) -> dict:
    prov = await get_provider(session)
    if prov is None:
        return {"domains": [{"domain": c["domain"], "name": c["name"]} for c in await competitor_domains(session)][:10],
                "cost_usd": 0.0, "cached": True,
                "note": "From your Company intelligence competitors. Add DataForSEO (optional) to find competitors by shared rankings."}
    cfg = await config_for(session, org.id)
    ours = await our_domain(session, org, cfg)
    if not ours:
        raise ValueError("Add your website address in Settings → Organization first.")
    res, hit = await fetch(session, org, prov, "competitors_domain", "gap", ttl_days=14, cfg=cfg, domain=ours,
                           location_code=cfg["location_code"], language_code=cfg["language_code"], limit=10)
    return {"domains": res.rows[:10], "cost_usd": res.cost, "cached": hit}


def csv_rows(report: KeywordReport, category: str | None = None) -> list[list]:
    if report.kind == "gap":
        comps = report.params.get("competitors") or []
        head = ["keyword", "category", "volume", "kd", "cpc", "intent", "our_position", "our_url",
                *[f"{c}_position" for c in comps]]
        out = [head]
        for r in report.rows:
            if category and category != "all" and not (r.get("category") == category or (category == "shared" and r.get("shared"))):
                continue
            out.append([r.get("keyword"), r.get("category"), r.get("volume"), r.get("kd"), r.get("cpc"), r.get("intent"),
                        r.get("ours"), r.get("our_url"), *[((r.get("competitors") or {}).get(c) or {}).get("position") for c in comps]])
        return out
    out = [["keyword", "volume", "kd", "cpc", "competition", "intent", "trend", "serp_features", "source", "estimated"]]
    for r in report.rows:
        out.append([r.get("keyword"), r.get("volume"), r.get("kd"), r.get("cpc"), r.get("competition"), r.get("intent"),
                    " ".join(str(x) for x in r.get("trend") or []), " ".join(r.get("serp_features") or []), r.get("source"),
                    "yes" if r.get("estimated") else "no"])
    return out
