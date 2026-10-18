"""Daily rank tracking: Google positions per tracked keyword, history, movers, visibility index and alerts.

With the optional paid provider (DataForSEO) each active keyword is checked once a day through the live SERP API
(top 100 organic results; batched, within the daily budget). Without it, rank checks are free: the configured web
search (DuckDuckGo / SearXNG / ...) gives an approximate position in its top 20, and Search Console gives the average
position over its latest period; both are labelled.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization, RankCheck, TrackedKeyword
from app.services.keywords import service as svc
from app.services.keywords.provider import ProviderError, domain_matches

log = logging.getLogger(__name__)
BATCH = 5
FREE_DEPTH = 20  # free web search returns at most ~20 results, so positions beyond that read "not in the top 20"
DEFAULT_VOLUME = 10  # weight for keywords without a known search volume
# Share of clicks by organic position (rough, public CTR curves). Used only for the visibility index.
CTR = {1: 0.28, 2: 0.15, 3: 0.11, 4: 0.08, 5: 0.07, 6: 0.05, 7: 0.04, 8: 0.03, 9: 0.03, 10: 0.025}


def ctr_weight(pos: int | None) -> float:
    if pos is None or pos < 1:
        return 0.0
    if pos <= 10:
        return CTR[pos]
    return 0.01 if pos <= 20 else 0.0


def visibility(pairs: list[tuple[int | None, int | None]]) -> float | None:
    """0–100: share of the clicks we would get if every keyword ranked #1."""
    if not pairs:
        return None
    top = sum(CTR[1] * (v or DEFAULT_VOLUME) for _, v in pairs)
    got = sum(ctr_weight(p) * (v or DEFAULT_VOLUME) for p, v in pairs)
    return round(100 * got / top, 1) if top else None


def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    p = urlsplit(u if "://" in u else "https://" + u)
    host = (p.hostname or "").lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/') or ''}"


def find_position(rows: list[dict], domain: str) -> tuple[int | None, str | None]:
    for r in rows:
        if domain_matches(r.get("domain") or r.get("url"), domain):
            return r.get("position"), r.get("url")
    return None, None


def alerts_for(changes: list[tuple[TrackedKeyword, int | None, int | None]], cfg: dict) -> list[dict]:
    a = cfg.get("alerts") or {}
    if not a.get("enabled", True):
        return []
    drop = int(a.get("drop") or 5)
    out = []
    for kw, prev, cur in changes:
        if prev is not None and (cur is None or cur - prev >= drop):
            out.append({"kind": "drop", "keyword": kw.keyword, "from": prev, "to": cur})
        elif cur is not None and cur <= 3 and (prev is None or prev > 3) and a.get("top3", True):
            out.append({"kind": "top3", "keyword": kw.keyword, "from": prev, "to": cur})
        elif cur is not None and cur <= 10 and (prev is None or prev > 10) and a.get("top10", True):
            out.append({"kind": "top10", "keyword": kw.keyword, "from": prev, "to": cur})
    return out


def alert_message(alerts: list[dict], depth: int = 100) -> tuple[str, str]:
    drops = [x for x in alerts if x["kind"] == "drop"]
    ups = [x for x in alerts if x["kind"] != "drop"]

    def pos(v):
        return f"#{v}" if v is not None else f"not in the top {depth}"
    parts = []
    if ups:
        parts.append("Up: " + "; ".join(f"“{x['keyword']}” {pos(x['from'])} → {pos(x['to'])}" for x in ups[:6]))
    if drops:
        parts.append("Down: " + "; ".join(f"“{x['keyword']}” {pos(x['from'])} → {pos(x['to'])}" for x in drops[:6]))
    if drops and ups:
        title = f"Rankings changed: {len(ups)} up, {len(drops)} dropped"
    elif drops:
        title = f"{len(drops)} keyword{'s' if len(drops) != 1 else ''} dropped in Google"
    else:
        title = f"{len(ups)} keyword{'s' if len(ups) != 1 else ''} moved into the top {'3' if all(x['kind'] == 'top3' for x in ups) else '10'}"
    return title, ". ".join(parts)


async def _previous(session: AsyncSession, kid: uuid.UUID, before: str) -> RankCheck | None:
    return await session.scalar(select(RankCheck).where(RankCheck.tracked_keyword_id == kid, RankCheck.check_date < before)
                                .order_by(RankCheck.check_date.desc()).limit(1))


async def run_checks(session: AsyncSession, org: Organization, *, ids: list[uuid.UUID] | None = None, force: bool = False,
                     progress=None) -> dict:
    cfg = await svc.config_for(session, org.id)
    today = svc.local_day(org)
    prov = await svc.get_provider(session)
    ours = await svc.our_domain(session, org, cfg)
    gsc_data = await svc.latest_gsc(session)
    gsc = svc.gsc_index(gsc_data)
    q = select(TrackedKeyword).where(TrackedKeyword.active.is_(True)).order_by(TrackedKeyword.created_at)
    if ids:
        q = q.where(TrackedKeyword.id.in_(ids))
    kws = list((await session.scalars(q)).all())
    existing = {c.tracked_keyword_id: c for c in (await session.scalars(
        select(RankCheck).where(RankCheck.check_date == today))).all()}
    todo = [k for k in kws if force or k.id not in existing]
    use_provider = prov is not None and bool(ours)
    stats = {"date": today, "keywords": len(kws), "checked": 0, "skipped_budget": 0, "failed": 0, "already": len(kws) - len(todo),
             "cost_usd": 0.0, "source": "dataforseo" if use_provider else "search", "approximate": not use_provider,
             "depth": 100, "alerts": [], "errors": []}
    changes: list[tuple[TrackedKeyword, int | None, int | None]] = []

    async def store(k: TrackedKeyword, position, url, *, source, features=(), top=(), cost=0.0):
        g = gsc.get(svc.norm(k.keyword))
        gpos = round(float(g["position"]), 1) if g and g.get("position") is not None else None
        prev = await _previous(session, k.id, today)
        chk = existing.get(k.id)
        if chk is None:
            chk = RankCheck(organization_id=org.id, tracked_keyword_id=k.id, check_date=today)
            session.add(chk)
            existing[k.id] = chk
        chk.position, chk.url, chk.gsc_position, chk.source = position, url, gpos, source
        chk.serp_features, chk.top_urls, chk.cost_usd = list(features), list(top), round(cost, 6)
        k.previous_position = prev.position if prev else None
        k.position, k.url, k.gsc_position, k.source, k.last_checked_at = position, url, gpos, source, svc.now()
        if position is not None and (k.best_position is None or position < k.best_position):
            k.best_position = position
        if k.target_url:
            k.data = {**(k.data or {}), "target_ranking": bool(url) and _norm_url(url) == _norm_url(k.target_url)}
        if prev is not None:
            changes.append((k, prev.position, position))
        stats["checked"] += 1

    if use_provider:
        depth = int(cfg.get("serp_depth") or 100)
        per = prov.estimate("serp_organic", depth=depth)
        budget = float(cfg.get("daily_budget_usd") or 0)
        if budget > 0:
            room = max(0.0, budget - await svc.spend_on(session, today))
            allowed = int(room / per + 1e-9) if per else len(todo)
            stats["skipped_budget"] = max(0, len(todo) - allowed)
            todo = todo[:allowed]
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            if progress:
                await progress(f"Checking Google positions ({i + 1}–{i + len(batch)} of {len(todo)})")
            results = await asyncio.gather(*[
                prov.serp_organic(keyword=k.keyword, location_code=k.location_code, language_code=k.language_code,
                                  device=k.device, depth=depth) for k in batch], return_exceptions=True)
            for k, r in zip(batch, results):
                if isinstance(r, BaseException):
                    msg = str(r) if isinstance(r, ProviderError) else f"{type(r).__name__}"
                    svc.record_spend(session, org, prov.id, "serp_organic", "rank", 0, ok=False, error=msg)
                    stats["failed"] += 1
                    if msg not in stats["errors"]:
                        stats["errors"].append(msg[:300])
                    continue
                svc.record_spend(session, org, prov.id, "serp_organic", "rank", r.cost)
                stats["cost_usd"] = round(stats["cost_usd"] + r.cost, 6)
                pos, url = find_position(r.rows, ours)
                await store(k, pos, url, source="dataforseo", features=r.meta.get("serp_features") or [],
                            top=[x["url"] for x in r.rows[:10]], cost=r.cost)
            await session.flush()
    else:
        from app.services.keywords import free

        stats["depth"] = FREE_DEPTH
        for n, k in enumerate(todo):
            if progress and n % 5 == 0:
                await progress(f"Checking positions with free web search ({n + 1} of {len(todo)})")
            pos = url = None
            source, top = "gsc", []
            if ours:
                try:
                    rows = await free.serp(session, org, k.keyword, n=FREE_DEPTH, ttl_days=0.5)
                    pos, url = find_position(rows, ours)
                    source, top = "search", [r["url"] for r in rows[:10]]
                except Exception as e:  # noqa: BLE001 - fall back to Search Console
                    msg = f"Free web search failed: {str(e)[:200]}"
                    if msg not in stats["errors"]:
                        stats["errors"].append(msg)
            if source == "gsc":
                if gsc_data is None:
                    stats["failed"] += 1
                    continue
                g = gsc.get(svc.norm(k.keyword))
                pos = max(1, round(float(g["position"]))) if g and g.get("position") else None
            await store(k, pos, url, source=source, top=top)
        await session.flush()
        stats["source"] = "search" if ours else "gsc"
        if not stats["checked"] and todo:
            stats["note"] = ("Couldn't check positions: free web search didn't answer and Search Console isn't connected. "
                             "Try again later, or connect Search Console in Analytics.")
    if not ours:
        stats["note"] = "Add your website address in Settings → Organization so we can find it in Google results."
    stats["alerts"] = alerts_for(changes, cfg)
    return stats


# ============================================================ reporting
def _change(prev: int | None, cur: int | None) -> int | None:
    if prev is None or cur is None:
        return None
    return prev - cur  # positive = moved up


async def overview(session: AsyncSession, org: Organization, days: int = 30) -> dict:
    today = svc.local_day(org)
    since = (date.fromisoformat(today) - timedelta(days=max(7, min(days, 365)) - 1)).isoformat()
    kws = list((await session.scalars(select(TrackedKeyword).order_by(TrackedKeyword.created_at))).all())
    checks = (await session.scalars(select(RankCheck).where(RankCheck.check_date >= since)
                                    .order_by(RankCheck.check_date))).all()
    by_kw: dict[uuid.UUID, list[RankCheck]] = {}
    by_day: dict[str, list[RankCheck]] = {}
    for c in checks:
        by_kw.setdefault(c.tracked_keyword_id, []).append(c)
        by_day.setdefault(c.check_date, []).append(c)
    vol = {k.id: k.volume for k in kws}
    week_ago = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    rows = []
    for k in kws:
        hist = by_kw.get(k.id, [])
        first_week = next((c for c in hist if c.check_date >= week_ago), None)
        rows.append({
            "id": k.id, "keyword": k.keyword, "target_url": k.target_url, "location_code": k.location_code,
            "language_code": k.language_code, "device": k.device, "active": k.active, "tags": k.tags or [],
            "volume": k.volume, "position": k.position, "previous_position": k.previous_position,
            "change": _change(k.previous_position, k.position),
            "change_7d": _change(first_week.position, k.position) if first_week and first_week is not (hist[-1] if hist else None) else None,
            "best_position": k.best_position, "url": k.url, "gsc_position": k.gsc_position, "source": k.source,
            "last_checked_at": k.last_checked_at, "target_ranking": (k.data or {}).get("target_ranking"),
            "spark": [c.position for c in hist[-30:]], "lost": k.previous_position is not None and k.position is None,
            "new": k.previous_position is None and k.position is not None and len(hist) > 1,
        })
    active = [r for r in rows if r["active"]]
    ranked = [r for r in active if r["position"] is not None]
    series = []
    for d in sorted(by_day):
        cs = by_day[d]
        pos = [c.position for c in cs if c.position is not None]
        gp = [c.gsc_position for c in cs if c.gsc_position is not None]
        series.append({"date": d, "visibility": visibility([(c.position, vol.get(c.tracked_keyword_id)) for c in cs]),
                       "avg_position": round(sum(pos) / len(pos), 1) if pos else None,
                       "avg_gsc_position": round(sum(gp) / len(gp), 1) if gp else None,
                       "top10": sum(1 for p in pos if p <= 10), "checked": len(cs)})
    movers = [r for r in active if r["change_7d"] is not None]
    movers.sort(key=lambda r: r["change_7d"])
    return {
        "today": today, "rows": rows,
        "summary": {
            "tracked": len(rows), "active": len(active), "top3": sum(1 for r in ranked if r["position"] <= 3),
            "top10": sum(1 for r in ranked if r["position"] <= 10), "top100": len(ranked),
            "not_ranking": sum(1 for r in active if r["last_checked_at"] and r["position"] is None),
            "avg_position": round(sum(r["position"] for r in ranked) / len(ranked), 1) if ranked else None,
            "visibility": visibility([(r["position"], r["volume"]) for r in active if r["last_checked_at"]]),
            "improved": sum(1 for r in active if (r["change"] or 0) > 0),
            "declined": sum(1 for r in active if (r["change"] or 0) < 0 or r["lost"]),
        },
        "series": series,
        "best_movers": [r for r in reversed(movers) if r["change_7d"] > 0][:5],
        "worst_movers": [r for r in movers if r["change_7d"] < 0][:5],
    }


async def history(session: AsyncSession, kw: TrackedKeyword, days: int = 90) -> dict:
    checks = (await session.scalars(select(RankCheck).where(RankCheck.tracked_keyword_id == kw.id)
                                    .order_by(RankCheck.check_date.desc()).limit(max(7, min(days, 365))))).all()
    checks = list(reversed(checks))
    latest = checks[-1] if checks else None
    return {
        "id": kw.id, "keyword": kw.keyword,
        "points": [{"date": c.check_date, "position": c.position, "gsc_position": c.gsc_position, "url": c.url,
                    "source": c.source} for c in checks],
        "serp_features": latest.serp_features if latest else [], "top_urls": latest.top_urls if latest else [],
    }
