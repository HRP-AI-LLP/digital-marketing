"""PageSpeed Insights for the audit: lab metrics + scores (Lighthouse) and real-user CrUX field data."""

from __future__ import annotations

import asyncio

import httpx

PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# Tests replace this with httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None
CONCURRENCY = 3
BACKOFF_SECONDS = 5.0  # one retry after a 429, then the remaining pages are skipped

FIELD_METRICS = {
    "LARGEST_CONTENTFUL_PAINT_MS": "lcp_ms", "INTERACTION_TO_NEXT_PAINT": "inp_ms",
    "CUMULATIVE_LAYOUT_SHIFT_SCORE": "cls", "FIRST_CONTENTFUL_PAINT_MS": "fcp_ms",
    "EXPERIMENTAL_TIME_TO_FIRST_BYTE": "ttfb_ms",
}
# Google's Core Web Vitals thresholds: (good up to, poor from)
THRESHOLDS = {"lcp_ms": (2500, 4000), "inp_ms": (200, 500), "cls": (0.1, 0.25), "fcp_ms": (1800, 3000),
              "tbt_ms": (200, 600), "ttfb_ms": (800, 1800)}


def rate(metric: str, value: float | None) -> str | None:
    if value is None or metric not in THRESHOLDS:
        return None
    good, poor = THRESHOLDS[metric]
    return "good" if value <= good else "poor" if value > poor else "needs_improvement"


def _field(block: dict | None) -> dict | None:
    if not isinstance(block, dict) or not block.get("metrics"):
        return None
    out: dict = {"overall": (block.get("overall_category") or "").lower() or None,
                 "origin_fallback": bool(block.get("origin_fallback"))}
    for k, name in FIELD_METRICS.items():
        m = block["metrics"].get(k)
        if not isinstance(m, dict) or m.get("percentile") is None:
            continue
        v = m["percentile"]
        if name == "cls":
            v = round(v / 100, 3)
        out[name] = v
        out[f"{name}_rating"] = {"FAST": "good", "AVERAGE": "needs_improvement", "SLOW": "poor"}.get(m.get("category"), rate(name, v))
    return out


def summarize(payload: dict) -> dict:
    lh = payload.get("lighthouseResult") or {}
    cats = lh.get("categories") or {}
    audits = lh.get("audits") or {}

    def cat(k: str) -> int | None:
        sc = (cats.get(k) or {}).get("score")
        return round(sc * 100) if isinstance(sc, (int, float)) else None

    def num(k: str) -> float | None:
        v = (audits.get(k) or {}).get("numericValue")
        return round(v, 3) if isinstance(v, (int, float)) else None

    lab = {"lcp_ms": num("largest-contentful-paint"), "cls": num("cumulative-layout-shift"),
           "tbt_ms": num("total-blocking-time"), "fcp_ms": num("first-contentful-paint"),
           "speed_index_ms": num("speed-index"), "ttfb_ms": num("server-response-time"),
           "inp_ms": num("interaction-to-next-paint")}
    opps = []
    for k, a in audits.items():
        det = a.get("details") or {}
        if det.get("type") == "opportunity" and isinstance(a.get("score"), (int, float)) and a["score"] < 0.9:
            opps.append({"id": k, "title": a.get("title") or k, "savings_ms": round(det.get("overallSavingsMs") or 0),
                         "display": a.get("displayValue")})
    opps.sort(key=lambda o: -o["savings_ms"])
    return {
        "scores": {"performance": cat("performance"), "accessibility": cat("accessibility"),
                   "best_practices": cat("best-practices"), "seo": cat("seo")},
        "lab": lab, "lab_ratings": {k: rate(k, v) for k, v in lab.items() if v is not None},
        "field": _field(payload.get("loadingExperience")), "origin_field": _field(payload.get("originLoadingExperience")),
        "opportunities": opps[:6],
    }


async def run_pagespeed(client: httpx.AsyncClient, url: str, strategy: str, key: str | None) -> dict:
    params: list[tuple[str, str]] = [("url", url), ("strategy", strategy)]
    params += [("category", c) for c in ("performance", "accessibility", "best-practices", "seo")]
    if key:
        params.append(("key", key))
    try:
        r = await client.get(PAGESPEED_URL, params=params)
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            if r.status_code == 429:
                return {"error": "PageSpeed Insights' free daily limit was reached. Speed will be measured next time"
                                 " (an optional free API key in the Google integration raises the limit).", "quota": True}
            return {"error": f"PageSpeed Insights returned {r.status_code}. {msg[:160]}".strip()}
        return summarize(r.json())
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"PageSpeed Insights couldn't be reached ({type(e).__name__})."}


async def measure(urls: list[str], key: str | None, progress=None) -> dict:
    """{"pages": [{url, mobile, desktop}], "summary": {...}} — never raises."""
    results: dict[str, dict] = {u: {"url": u} for u in urls}
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0
    quota_hit = False
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0), transport=TRANSPORT) as client:
        async def one(u: str, strategy: str) -> None:
            nonlocal done, quota_hit
            async with sem:
                if quota_hit:  # free quota used up: skip the rest instead of hammering the API
                    results[u][strategy] = {"error": "Skipped: PageSpeed Insights' free limit was reached.", "quota": True}
                else:
                    res = await run_pagespeed(client, u, strategy, key)
                    if res.get("quota"):
                        await asyncio.sleep(BACKOFF_SECONDS)
                        res = await run_pagespeed(client, u, strategy, key)
                        quota_hit = quota_hit or bool(res.get("quota"))
                    results[u][strategy] = res
            done += 1
            if progress:
                try:
                    await progress(f"Measuring page speed ({done} of {len(urls) * 2})")
                except Exception:  # noqa: BLE001
                    pass

        await asyncio.gather(*(one(u, s) for u in urls for s in ("mobile", "desktop")))
    pages = [results[u] for u in urls]
    return {"pages": pages, "summary": overview(pages), "quota_reached": quota_hit, "with_key": bool(key)}


def overview(pages: list[dict]) -> dict:
    """Site-level numbers for the Core Web Vitals cards: median lab values per strategy, field data of the home page."""
    out: dict = {}
    for strategy in ("mobile", "desktop"):
        ok = [p[strategy] for p in pages if isinstance(p.get(strategy), dict) and not p[strategy].get("error")]
        if not ok:
            errs = [p[strategy]["error"] for p in pages if isinstance(p.get(strategy), dict) and p[strategy].get("error")]
            out[strategy] = {"measured": 0, "error": errs[0] if errs else None}
            continue

        def med(vals: list[float]) -> float | None:
            vals = sorted(v for v in vals if v is not None)
            return vals[len(vals) // 2] if vals else None

        lab = {k: med([o["lab"].get(k) for o in ok]) for k in ("lcp_ms", "cls", "tbt_ms", "fcp_ms", "inp_ms", "ttfb_ms")}
        scores = {k: med([o["scores"].get(k) for o in ok]) for k in ("performance", "accessibility", "best_practices", "seo")}
        field = next((o["field"] for o in ok if o.get("field")), None) or next((o["origin_field"] for o in ok if o.get("origin_field")), None)
        out[strategy] = {"measured": len(ok), "lab": lab, "lab_ratings": {k: rate(k, v) for k, v in lab.items() if v is not None},
                         "scores": scores, "field": field}
    return out
