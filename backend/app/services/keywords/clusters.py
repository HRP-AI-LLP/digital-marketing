"""Group a keyword list into topic clusters (pillar + supporting keywords).

Methods, best first:
  serp        keywords whose top-10 results share at least N URLs belong together (Google results via DataForSEO when
              it is set up, otherwise the free web search, approximate; cached 7 days, reused from today's rank checks)
  embeddings  cosine similarity of embeddings (needs an embedding model in Task routing)
  ai          the text model groups them
  words       shared meaningful words (always available)
The pillar of each cluster is its highest-volume keyword.
"""

from __future__ import annotations

import logging
import math
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KeywordList, Organization
from app.services.keywords import service as svc
from app.services.keywords.provider import ProviderError
from app.services.llm import router as llm_router

log = logging.getLogger(__name__)
MAX_SERP_KEYWORDS = 150
SIM_THRESHOLD = 0.82


def _vol(item: dict) -> int:
    return int(item.get("volume") or 0)


def _cluster(name_kw: dict, members: list[dict], method: str) -> dict:
    vols = [m.get("volume") for m in members if m.get("volume") is not None]
    kds = [m.get("kd") for m in members if m.get("kd") is not None]
    return {
        "id": uuid.uuid4().hex[:12], "name": name_kw["keyword"], "pillar": name_kw["keyword"],
        "keywords": [m["keyword"] for m in members], "volume": sum(vols) if vols else None,
        "kd": round(sum(kds) / len(kds)) if kds else None, "intent": name_kw.get("intent"), "method": method,
    }


def greedy(items: list[dict], related, method: str) -> list[dict]:
    """Hard clustering: take the highest-volume free keyword as pillar, attach every free keyword related to it."""
    order = sorted(items, key=lambda i: (-_vol(i), len(i["keyword"])))
    free = {i["keyword"] for i in order}
    clusters = []
    for pillar in order:
        if pillar["keyword"] not in free:
            continue
        free.discard(pillar["keyword"])
        members = [pillar]
        for other in order:
            if other["keyword"] in free and related(pillar, other):
                members.append(other)
                free.discard(other["keyword"])
        clusters.append(_cluster(pillar, members, method))
    clusters.sort(key=lambda c: (-len(c["keywords"]), -(c["volume"] or 0)))
    return clusters


def by_serp(items: list[dict], serps: dict[str, list[str]], overlap: int) -> list[dict]:
    sets = {k: set(v[:10]) for k, v in serps.items()}
    return greedy(items, lambda a, b: len(sets.get(a["keyword"], set()) & sets.get(b["keyword"], set())) >= overlap, "serp")


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def by_vectors(items: list[dict], vecs: list[list[float]], threshold: float = SIM_THRESHOLD) -> list[dict]:
    v = {i["keyword"]: vec for i, vec in zip(items, vecs)}
    return greedy(items, lambda a, b: _cos(v[a["keyword"]], v[b["keyword"]]) >= threshold, "embeddings")


def by_words(items: list[dict]) -> list[dict]:
    toks = {i["keyword"]: svc.tokens(i["keyword"]) for i in items}

    def related(a, b):
        ta, tb = toks[a["keyword"]], toks[b["keyword"]]
        if not ta or not tb:
            return False
        return len(ta & tb) / min(len(ta), len(tb)) >= 0.67
    return greedy(items, related, "words")


CLUSTER_SYSTEM = ("You group search keywords into topic clusters for website content planning. Each cluster should be one "
                  "page's topic. Use only keywords from the list, each exactly once.")


async def by_ai(session: AsyncSession, items: list[dict]) -> list[dict] | None:
    listing = "\n".join(f"- {i['keyword']}" for i in items[:300])
    prompt = (f"Keywords:\n{listing}\n\nGroup them into clusters that one article could cover each. "
              'Return JSON: {"clusters": [{"name": "short topic name", "keywords": ["..."]}]}')
    try:
        data = await llm_router.complete_json(session, "plan", prompt, system=CLUSTER_SYSTEM, temperature=0.2, max_tokens=4000)
    except (llm_router.NoProviderError, llm_router.AllProvidersFailed, ValueError):
        return None
    by_norm = {svc.norm(i["keyword"]): i for i in items}
    used: set[str] = set()
    out = []
    for c in (data.get("clusters") if isinstance(data, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        members = []
        for k in c.get("keywords") or []:
            it = by_norm.get(svc.norm(str(k)))
            if it and it["keyword"] not in used:
                used.add(it["keyword"])
                members.append(it)
        if not members:
            continue
        members.sort(key=lambda m: -_vol(m))
        cl = _cluster(members[0], members, "ai")
        if str(c.get("name") or "").strip():
            cl["name"] = str(c["name"]).strip()[:120]
        out.append(cl)
    rest = [i for i in items if i["keyword"] not in used]
    out += by_words(rest) if rest else []
    return out or None


async def _serps(session: AsyncSession, org: Organization, items: list[dict], cfg: dict, progress) -> tuple[dict, float, int]:
    """Top-10 organic URLs per keyword, from today's rank checks or the provider (cached 7 days)."""
    from sqlalchemy import select

    from app.models import RankCheck, TrackedKeyword

    prov = await svc.get_provider(session)
    serps: dict[str, list[str]] = {}
    today = svc.local_day(org)
    rows = (await session.execute(
        select(TrackedKeyword.keyword, RankCheck.top_urls).join(RankCheck, RankCheck.tracked_keyword_id == TrackedKeyword.id)
        .where(RankCheck.check_date == today, TrackedKeyword.location_code == cfg["location_code"])
    )).all()
    for kw, urls in rows:
        if urls:
            serps[svc.norm(kw)] = list(urls)
    cost, calls = 0.0, 0
    out: dict[str, list[str]] = {}
    for n, it in enumerate(items[:MAX_SERP_KEYWORDS]):
        k = svc.norm(it["keyword"])
        if k in serps:
            out[it["keyword"]] = serps[k]
            continue
        if progress and n % 10 == 0:
            await progress(f"Reading search results ({n + 1} of {min(len(items), MAX_SERP_KEYWORDS)})")
        if prov is None:  # free: the configured web search (approximate)
            from app.services.keywords import free

            try:
                out[it["keyword"]] = [r["url"] for r in (await free.serp(session, org, it["keyword"], n=10))[:10]]
            except Exception as e:  # noqa: BLE001 - leave this keyword to the fallback grouping
                log.info("free search for clustering failed: %s", e)
            continue
        res, hit = await svc.fetch(session, org, prov, "serp_organic", "cluster", ttl_days=7, cfg=cfg, keyword=it["keyword"],
                                   location_code=cfg["location_code"], language_code=cfg["language_code"],
                                   device=cfg["device"], depth=10)
        out[it["keyword"]] = [r["url"] for r in res.rows[:10]]
        cost += res.cost
        calls += 0 if hit else 1
    return out, cost, calls


async def estimate(session: AsyncSession, org: Organization, kl: KeywordList, use_serp: bool) -> dict:
    prov = await svc.get_provider(session)
    if not use_serp or prov is None:
        return {"cost_usd": 0.0, "method": "free"}
    cfg = await svc.config_for(session, org.id)
    total = 0.0
    for it in (kl.items or [])[:MAX_SERP_KEYWORDS]:
        params = {"keyword": it["keyword"], "location_code": cfg["location_code"], "language_code": cfg["language_code"],
                  "device": cfg["device"], "depth": 10}
        if await svc.cached(session, svc.cache_key(prov.id, "serp_organic", params)) is None:
            total += prov.estimate("serp_organic", **params)
    return {"cost_usd": round(total, 4), "method": "serp"}


async def cluster_list(session: AsyncSession, org: Organization, kl: KeywordList, *, use_serp: bool = True,
                       progress=None) -> dict:
    items = [i for i in (kl.items or []) if isinstance(i, dict) and i.get("keyword")]
    if len(items) < 2:
        raise ValueError("Add at least two keywords to the list first.")
    cfg = await svc.config_for(session, org.id)
    meta: dict = {"cost_usd": 0.0, "at": svc.now().isoformat()}
    clusters = None
    if use_serp:
        try:
            serps, cost, calls = await _serps(session, org, items, cfg, progress)
            if len(serps) < max(2, len(items) // 2):
                raise ProviderError("too few search results came back")
            paid = await svc.get_provider(session) is not None
            meta.update({"cost_usd": round(cost, 6), "calls": calls, "approximate": not paid,
                         "serp_source": "dataforseo" if paid else "web_search"})
            covered = [i for i in items if i["keyword"] in serps]
            clusters = by_serp(covered, serps, int(cfg["cluster_overlap"]))
            rest = [i for i in items if i["keyword"] not in serps]
            clusters += by_words(rest) if rest else []
            meta["method"] = "serp"
        except (ProviderError, svc.BudgetError) as e:
            meta["note"] = f"Google results weren't available ({e}); grouped by meaning instead."
            clusters = None
    if clusters is None:
        if progress:
            await progress("Grouping keywords by meaning")
        vecs, model = await llm_router.embed_texts(session, [i["keyword"] for i in items])
        if vecs and len(vecs) == len(items):
            clusters, meta["method"] = by_vectors(items, vecs), "embeddings"
        else:
            clusters = await by_ai(session, items)
            meta["method"] = "ai" if clusters else "words"
            clusters = clusters or by_words(items)
    kl.clusters = clusters
    kl.cluster_meta = meta
    return {"clusters": len(clusters), "method": meta["method"], "cost_usd": meta["cost_usd"], "note": meta.get("note")}
