"""Analytics: Google Analytics 4, Search Console and PageSpeed snapshots, plus social performance.

Google endpoints and versions are constants below. HTTP goes through `http_client()` so tests can
swap in an httpx.MockTransport. Snapshots are stored as AnalyticsSnapshot rows
(source ga4 | gsc | pagespeed, plus a `refresh` marker row per run).
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import (
    AnalyticsSnapshot,
    ContentPillar,
    Organization,
    Post,
    PostMetric,
    ProviderConfig,
    SocialAccount,
)

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ Google endpoints (one place)
GA4_RUN_REPORT = "https://analyticsdata.googleapis.com/v1beta/properties/{property}:runReport"
GSC_QUERY = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
PAGESPEED_RUN = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
PERIOD_DAYS = 28
GSC_LAG_DAYS = 3  # Search Console data is final after ~2–3 days
STALE_HOURS = 20
DAILY_HOUR = 6  # org local time
MIN_BUCKET_POSTS = 5

GA4_BASE_METRICS = ["sessions", "activeUsers", "newUsers", "engagementRate", "averageSessionDuration", "screenPageViews"]
GA4_METRIC_SETS = [GA4_BASE_METRICS + ["keyEvents"], GA4_BASE_METRICS + ["conversions"], GA4_BASE_METRICS]

Progress = Callable[[str], Awaitable[None]] | None


def http_client(**kw) -> httpx.AsyncClient:
    """Factory for outgoing HTTP (patched in tests)."""
    return httpx.AsyncClient(timeout=httpx.Timeout(kw.pop("timeout", 60.0)), **kw)


class GoogleApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


async def _post(token: str, url: str, body: dict) -> dict:
    async with http_client() as http:
        r = await http.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        raise GoogleApiError(r.status_code, str(msg)[:500])
    return r.json()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _delta(cur: float, prev: float) -> float | None:
    if not prev:
        return None
    return round((cur - prev) * 100 / prev, 1)


def periods(today: date | None = None, lag: int = 1) -> tuple[tuple[date, date], tuple[date, date]]:
    end = (today or datetime.now(UTC).date()) - timedelta(days=lag)
    start = end - timedelta(days=PERIOD_DAYS - 1)
    p_end = start - timedelta(days=1)
    p_start = p_end - timedelta(days=PERIOD_DAYS - 1)
    return (start, end), (p_start, p_end)


# ------------------------------------------------------------------ connection helpers
async def _google():
    try:
        from app.services.social import google
    except ImportError:
        return None
    return google


async def google_status(session: AsyncSession) -> dict:
    g = await _google()
    if g is None:
        return {"connected": False, "account": None, "ga4_property": None, "gsc_site": None}
    try:
        acc = await g.google_account(session)
    except Exception:  # noqa: BLE001
        log.exception("google_account failed")
        acc = None
    s = (acc.settings or {}) if acc else {}
    return {
        "connected": acc is not None and acc.status != "expired",
        "account": {"id": str(acc.id), "name": acc.name, "status": acc.status} if acc else None,
        "ga4_property": s.get("ga4_property"), "gsc_site": s.get("gsc_site"),
    }


async def pagespeed_key(session: AsyncSession) -> str | None:
    cfgs = (await session.scalars(
        select(ProviderConfig).where(ProviderConfig.kind == "google", ProviderConfig.enabled.is_(True))
    )).all()
    for c in cfgs:
        k = decrypt_json(c.secrets_enc).get("pagespeed_key")
        if k:
            return k
    return None


def normalize_property(p: str | None) -> str | None:
    if not p:
        return None
    p = str(p).strip()
    return p.split("/", 1)[1] if p.startswith("properties/") else p


# ------------------------------------------------------------------ GA4
def _ga_rows(resp: dict) -> list[dict]:
    dims = [h["name"] for h in resp.get("dimensionHeaders") or []]
    mets = [h["name"] for h in resp.get("metricHeaders") or []]
    out = []
    for row in resp.get("rows") or []:
        d = {n: v.get("value") for n, v in zip(dims, row.get("dimensionValues") or [])}
        d.update({n: _num(v.get("value")) for n, v in zip(mets, row.get("metricValues") or [])})
        out.append(d)
    return out


async def _ga_report(token: str, prop: str, body: dict) -> dict:
    return await _post(token, GA4_RUN_REPORT.format(property=prop), body)


def _is_metric_error(e: GoogleApiError) -> bool:
    m = str(e).lower()
    return e.status == 400 and ("metric" in m or "field" in m or "keyevents" in m or "conversions" in m)


async def fetch_ga4(token: str, prop: str, today: date | None = None) -> tuple[dict, tuple[date, date]]:
    (cs, ce), (ps, pe) = periods(today, lag=1)
    ranges = [{"startDate": cs.isoformat(), "endDate": ce.isoformat(), "name": "current"},
              {"startDate": ps.isoformat(), "endDate": pe.isoformat(), "name": "previous"}]
    cur_range = [ranges[0]]

    totals_resp, metrics = None, GA4_BASE_METRICS
    last_err: GoogleApiError | None = None
    for mset in GA4_METRIC_SETS:
        try:
            totals_resp = await _ga_report(token, prop, {"dateRanges": ranges, "metrics": [{"name": m} for m in mset]})
            metrics = mset
            break
        except GoogleApiError as e:
            last_err = e
            if not _is_metric_error(e):
                raise
    if totals_resp is None:
        raise last_err or GoogleApiError(400, "GA4 report failed")

    cur, prev = {m: 0.0 for m in metrics}, {m: 0.0 for m in metrics}
    for r in _ga_rows(totals_resp):
        which = r.get("dateRange", "current")
        target = prev if which in ("previous", "date_range_1") else cur
        for m in metrics:
            target[m] = r.get(m, 0.0)
    conv_key = "keyEvents" if "keyEvents" in metrics else ("conversions" if "conversions" in metrics else None)
    totals = {
        "current": cur, "previous": prev,
        "deltas": {m: _delta(cur[m], prev[m]) for m in metrics},
        "conversion_metric": conv_key,
    }

    async def rep(dims: list[str], mets: list[str], order: str | None = None, limit: int = 10, rngs=None):
        body = {"dateRanges": rngs or cur_range, "dimensions": [{"name": d} for d in dims],
                "metrics": [{"name": m} for m in mets], "limit": limit}
        if order:
            body["orderBys"] = [{"metric": {"metricName": order}, "desc": True}]
        try:
            return _ga_rows(await _ga_report(token, prop, body))
        except GoogleApiError as e:
            log.warning("GA4 %s report failed: %s", dims, e)
            return []

    by_day = await rep(["date"], ["sessions", "activeUsers"], limit=100)
    for r in by_day:
        d = r.get("date") or ""
        r["date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
    by_day.sort(key=lambda r: r["date"])
    prev_by_day = await rep(["date"], ["sessions"], limit=100, rngs=[ranges[1]])
    prev_by_day.sort(key=lambda r: r.get("date") or "")
    pages = await rep(["pagePath"], ["screenPageViews", "sessions"], "screenPageViews", 20)
    channels = await rep(["sessionDefaultChannelGroup"], ["sessions"], "sessions", 12)
    sources = await rep(["sessionSource"], ["sessions"], "sessions", 12)
    countries = await rep(["country"], ["activeUsers"], "activeUsers", 10)
    devices = await rep(["deviceCategory"], ["sessions"], "sessions", 5)

    data = {
        "property": prop, "metrics": metrics, "totals": totals,
        "by_day": [{"date": r["date"], "sessions": r.get("sessions", 0), "activeUsers": r.get("activeUsers", 0)} for r in by_day],
        "previous_by_day": [r.get("sessions", 0) for r in prev_by_day],
        "pages": [{"path": r.get("pagePath"), "views": r.get("screenPageViews", 0), "sessions": r.get("sessions", 0)} for r in pages],
        "channels": [{"name": r.get("sessionDefaultChannelGroup"), "sessions": r.get("sessions", 0)} for r in channels],
        "sources": [{"name": r.get("sessionSource"), "sessions": r.get("sessions", 0)} for r in sources],
        "countries": [{"name": r.get("country"), "users": r.get("activeUsers", 0)} for r in countries],
        "devices": [{"name": r.get("deviceCategory"), "sessions": r.get("sessions", 0)} for r in devices],
        "previous_period": {"start": ps.isoformat(), "end": pe.isoformat()},
    }
    return data, (cs, ce)


# ------------------------------------------------------------------ Search Console
def is_opportunity(row: dict) -> bool:
    pos, imp, ctr = _num(row.get("position")), _num(row.get("impressions")), _num(row.get("ctr"))
    return (5 <= pos <= 20 and imp >= 10) or (imp >= 100 and ctr < 0.02)


def _gsc_rows(resp: dict, dim: str | None) -> list[dict]:
    out = []
    for r in resp.get("rows") or []:
        row = {"clicks": _num(r.get("clicks")), "impressions": _num(r.get("impressions")),
               "ctr": round(_num(r.get("ctr")), 4), "position": round(_num(r.get("position")), 1)}
        if dim:
            row[dim] = (r.get("keys") or [""])[0]
        out.append(row)
    return out


def _gsc_totals(rows: list[dict]) -> dict:
    clicks = sum(r["clicks"] for r in rows)
    imp = sum(r["impressions"] for r in rows)
    pos = (sum(r["position"] * r["impressions"] for r in rows) / imp) if imp else 0.0
    return {"clicks": clicks, "impressions": imp, "ctr": round(clicks / imp, 4) if imp else 0.0, "position": round(pos, 1)}


async def fetch_gsc(token: str, site: str, today: date | None = None) -> tuple[dict, tuple[date, date]]:
    (cs, ce), (ps, pe) = periods(today, lag=GSC_LAG_DAYS)
    url = GSC_QUERY.format(site=quote(site, safe=""))

    async def q(start: date, end: date, dims: list[str], limit: int) -> dict:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": limit}
        if dims:
            body["dimensions"] = dims
        return await _post(token, url, body)

    by_date = _gsc_rows(await q(cs, ce, ["date"], 100), "date")
    by_date.sort(key=lambda r: r["date"])
    queries = _gsc_rows(await q(cs, ce, ["query"], 250), "query")
    pages = _gsc_rows(await q(cs, ce, ["page"], 100), "page")
    prev_rows = _gsc_rows(await q(ps, pe, ["date"], 100), "date")
    for r in queries:
        r["opportunity"] = is_opportunity(r)
    queries.sort(key=lambda r: r["clicks"], reverse=True)
    pages.sort(key=lambda r: r["clicks"], reverse=True)
    cur_t, prev_t = _gsc_totals(by_date), _gsc_totals(prev_rows)
    data = {
        "site": site,
        "totals": {"current": cur_t, "previous": prev_t,
                   "deltas": {k: _delta(cur_t[k], prev_t[k]) for k in cur_t}},
        "by_date": by_date, "queries": queries, "pages": pages,
        "opportunities": sorted([r for r in queries if r["opportunity"]], key=lambda r: r["impressions"], reverse=True)[:25],
        "previous_period": {"start": ps.isoformat(), "end": pe.isoformat()},
    }
    return data, (cs, ce)


# ------------------------------------------------------------------ PageSpeed
FIELD_METRICS = {
    "LARGEST_CONTENTFUL_PAINT_MS": ("lcp", "ms"), "INTERACTION_TO_NEXT_PAINT": ("inp", "ms"),
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": ("cls", "score"), "FIRST_CONTENTFUL_PAINT_MS": ("fcp", "ms"),
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": ("ttfb", "ms"),
}
LAB_AUDITS = {
    "largest-contentful-paint": "lcp", "cumulative-layout-shift": "cls", "total-blocking-time": "tbt",
    "first-contentful-paint": "fcp", "speed-index": "si",
}


def parse_pagespeed(resp: dict) -> dict:
    lh = resp.get("lighthouseResult") or {}
    cats = {k: round(_num(v.get("score")) * 100) for k, v in (lh.get("categories") or {}).items() if v.get("score") is not None}
    audits = lh.get("audits") or {}
    lab = {}
    for aid, key in LAB_AUDITS.items():
        a = audits.get(aid) or {}
        if a:
            lab[key] = {"value": a.get("numericValue"), "display": a.get("displayValue"),
                        "score": round(_num(a.get("score")) * 100) if a.get("score") is not None else None}
    field = {}
    for k, v in ((resp.get("loadingExperience") or {}).get("metrics") or {}).items():
        if k in FIELD_METRICS:
            key, unit = FIELD_METRICS[k]
            pct = v.get("percentile")
            if key == "cls" and pct is not None:
                pct = round(_num(pct) / 100, 3)
            field[key] = {"p75": pct, "category": v.get("category"), "unit": unit}
    return {"scores": cats, "lab": lab, "field": field,
            "field_overall": (resp.get("loadingExperience") or {}).get("overall_category"),
            "fetched_url": lh.get("finalUrl") or resp.get("id")}


async def fetch_pagespeed(url: str, key: str | None) -> dict:
    out = {}
    async with http_client(timeout=120.0) as http:
        for strategy in ("mobile", "desktop"):
            params = [("url", url), ("strategy", strategy), ("category", "performance"), ("category", "accessibility"),
                      ("category", "best-practices"), ("category", "seo")]
            if key:
                params.append(("key", key))
            r = await http.get(PAGESPEED_RUN, params=params)
            if r.status_code >= 400:
                try:
                    msg = r.json().get("error", {}).get("message") or r.text
                except ValueError:
                    msg = r.text
                out[strategy] = {"error": str(msg)[:300]}
                continue
            out[strategy] = parse_pagespeed(r.json())
    return out


# ------------------------------------------------------------------ refresh
async def latest(session: AsyncSession, source: str) -> AnalyticsSnapshot | None:
    return await session.scalar(
        select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == source).order_by(AnalyticsSnapshot.created_at.desc())
    )


async def refresh_all(session: AsyncSession, org_id: uuid.UUID, progress: Progress = None) -> dict:
    async def step(m: str):
        if progress:
            await progress(m)

    org = await session.get(Organization, org_id)
    notes: list[str] = []
    done: dict[str, bool] = {"ga4": False, "gsc": False, "pagespeed": False}
    g = await _google()
    token = None
    acc = None
    if g is not None:
        try:
            acc = await g.google_account(session)
            token = await g.google_access_token(session) if acc else None
        except Exception as e:  # noqa: BLE001
            log.warning("google token failed: %s", e)
            notes.append("Google needs reconnecting in Settings → Social accounts.")
    if g is None or acc is None:
        notes.append("Google isn't connected, so Analytics and Search Console were skipped. Connect it in Settings → Social accounts.")
    settings = (acc.settings or {}) if acc else {}

    if token:
        prop = normalize_property(settings.get("ga4_property"))
        if prop:
            await step("Reading Google Analytics")
            try:
                data, (s, e) = await fetch_ga4(token, prop)
                session.add(AnalyticsSnapshot(organization_id=org_id, source="ga4", key=prop, period_start=s.isoformat(),
                                              period_end=e.isoformat(), data=data))
                done["ga4"] = True
            except (GoogleApiError, httpx.HTTPError) as e:
                notes.append(f"Google Analytics: {e}")
        else:
            notes.append("Pick a Google Analytics property in Analytics → Settings.")
        site = settings.get("gsc_site")
        if site:
            await step("Reading Search Console")
            try:
                data, (s, e) = await fetch_gsc(token, site)
                session.add(AnalyticsSnapshot(organization_id=org_id, source="gsc", key=site[:200], period_start=s.isoformat(),
                                              period_end=e.isoformat(), data=data))
                done["gsc"] = True
            except (GoogleApiError, httpx.HTTPError) as e:
                notes.append(f"Search Console: {e}")
        else:
            notes.append("Pick a Search Console site in Analytics → Settings.")

    website = (org.website or "").strip() if org else ""
    if website:
        if "://" not in website:
            website = "https://" + website
        await step("Running PageSpeed Insights")
        try:
            data = await fetch_pagespeed(website, await pagespeed_key(session))
            if any("scores" in v for v in data.values()):
                today = datetime.now(UTC).date().isoformat()
                session.add(AnalyticsSnapshot(organization_id=org_id, source="pagespeed", key=website[:200],
                                              period_start=today, period_end=today, data=data))
                done["pagespeed"] = True
            else:
                notes.append("PageSpeed: " + "; ".join(v.get("error", "") for v in data.values()))
        except httpx.HTTPError as e:
            notes.append(f"PageSpeed: {e}")
    else:
        notes.append("Add your website in Settings → Organization to get PageSpeed reports.")

    ok = [k for k, v in done.items() if v]
    labels = {"ga4": "Google Analytics", "gsc": "Search Console", "pagespeed": "PageSpeed"}
    message = ("Updated " + ", ".join(labels[k] for k in ok) + "." if ok else "Nothing was updated.")
    if notes:
        message += " " + " ".join(notes)
    session.add(AnalyticsSnapshot(organization_id=org_id, source="refresh", key="last_run",
                                  data={"updated": ok, "notes": notes, "message": message}))
    await session.flush()
    return {"updated": ok, "notes": notes, "message": message}


# ------------------------------------------------------------------ social performance
def engagements(m: PostMetric | None) -> int:
    if m is None:
        return 0
    return int(m.likes + m.comments + m.shares + m.saves + m.clicks)


def _rate(eng: float, imp: float) -> float:
    return round(eng / max(imp, 1), 4)


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


async def social_performance(session: AsyncSession, days: int = 30, tz: str = "Asia/Kolkata") -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    posts = (await session.scalars(
        select(Post).where(Post.status == "published", Post.published_at.is_not(None), Post.published_at >= since)
    )).all()
    ids = [p.id for p in posts]
    metrics: dict[uuid.UUID, PostMetric] = {}
    if ids:
        rows = (await session.scalars(
            select(PostMetric).where(PostMetric.post_id.in_(ids))
            .distinct(PostMetric.post_id).order_by(PostMetric.post_id, PostMetric.fetched_at.desc())
        )).all()
        metrics = {m.post_id: m for m in rows}
    pillars = {p.id: p for p in (await session.scalars(select(ContentPillar))).all()}
    accounts = {a.id: a for a in (await session.scalars(select(SocialAccount))).all()}
    try:
        zone = ZoneInfo(tz or "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        zone = ZoneInfo("Asia/Kolkata")

    def blank():
        return {"posts": 0, "impressions": 0, "reach": 0, "engagements": 0, "clicks": 0, "likes": 0, "comments": 0,
                "shares": 0, "saves": 0, "video_views": 0}

    def add(b: dict, m: PostMetric | None):
        b["posts"] += 1
        if m:
            for k in ("impressions", "reach", "clicks", "likes", "comments", "shares", "saves", "video_views"):
                b[k] += int(getattr(m, k) or 0)
            b["engagements"] += engagements(m)

    def fin(b: dict) -> dict:
        b["engagement_rate"] = _rate(b["engagements"], b["impressions"])
        return b

    totals = blank()
    by_platform: dict[str, dict] = defaultdict(blank)
    by_account: dict[str, dict] = defaultdict(blank)
    by_pillar: dict[str, dict] = defaultdict(blank)
    by_type: dict[str, dict] = defaultdict(blank)
    by_day: dict[str, dict] = defaultdict(blank)
    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    top = []
    for p in posts:
        m = metrics.get(p.id)
        add(totals, m)
        add(by_platform[p.platform], m)
        add(by_account[str(p.social_account_id) if p.social_account_id else "none"], m)
        add(by_pillar[str(p.pillar_id) if p.pillar_id else "none"], m)
        add(by_type[p.post_type or "text"], m)
        local = p.published_at.astimezone(zone)
        add(by_day[local.date().isoformat()], m)
        eng, imp = engagements(m), (m.impressions if m else 0)
        if m:
            buckets[(local.weekday(), local.hour)].append(_rate(eng, imp))
        top.append({
            "id": str(p.id), "platform": p.platform, "post_type": p.post_type,
            "title": (p.title or p.topic or (p.text or "")[:90] or "Untitled").strip(),
            "published_at": p.published_at.isoformat(), "external_url": p.external_url,
            "impressions": imp, "engagements": eng, "clicks": m.clicks if m else 0,
            "engagement_rate": _rate(eng, imp), "has_metrics": m is not None,
        })
    top.sort(key=lambda r: (r["engagements"], r["engagement_rate"]), reverse=True)

    grid = []
    for (wd, hr), rates in sorted(buckets.items()):
        grid.append({"weekday": wd, "day": WEEKDAYS[wd], "hour": hr, "posts": len(rates),
                     "avg_engagement_rate": round(sum(rates) / len(rates), 4), "enough": len(rates) >= MIN_BUCKET_POSTS})
    best = sorted([g for g in grid if g["enough"]], key=lambda g: g["avg_engagement_rate"], reverse=True)[:5]
    best_msg = None if best else (
        f"Not enough data yet: best times need at least {MIN_BUCKET_POSTS} posts with metrics in the same weekday and hour."
    )

    def named(d: dict, label) -> list[dict]:
        return sorted([{"key": k, "label": label(k), **fin(v)} for k, v in d.items()],
                      key=lambda r: r["engagements"], reverse=True)

    from app.services.platforms import PLATFORMS

    def plat_label(k):
        return PLATFORMS[k].label if k in PLATFORMS else k

    def acc_label(k):
        try:
            a = accounts.get(uuid.UUID(k))
        except ValueError:
            a = None
        return f"{a.name} ({plat_label(a.platform)})" if a else "No account"

    def pillar_label(k):
        try:
            p = pillars.get(uuid.UUID(k))
        except ValueError:
            p = None
        return p.name if p else "No pillar"

    return {
        "days": days, "timezone": str(zone), "totals": fin(totals),
        "by_platform": named(by_platform, plat_label),
        "by_account": named(by_account, acc_label),
        "by_pillar": [{**r, "color": (pillars.get(uuid.UUID(r["key"])).color if r["key"] != "none" and
                                      pillars.get(uuid.UUID(r["key"])) else None)} for r in named(by_pillar, pillar_label)],
        "by_post_type": named(by_type, lambda k: k.replace("_", " ").capitalize()),
        "by_day": [{"date": k, **fin(v)} for k, v in sorted(by_day.items())],
        "heatmap": grid, "best_times": best, "best_times_message": best_msg, "min_bucket_posts": MIN_BUCKET_POSTS,
        "top_posts": top[:10],
    }


# ------------------------------------------------------------------ summaries for the API
def snapshot_out(s: AnalyticsSnapshot | None) -> dict | None:
    if s is None:
        return None
    return {"id": str(s.id), "source": s.source, "key": s.key, "period_start": s.period_start, "period_end": s.period_end,
            "created_at": s.created_at.isoformat() if s.created_at else None, "data": s.data}


async def needs_refresh(session: AsyncSession, hours: int = STALE_HOURS) -> bool:
    last = await latest(session, "refresh")
    if last is None:
        return True
    return last.created_at < datetime.now(UTC) - timedelta(hours=hours)
