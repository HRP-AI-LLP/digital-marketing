"""Keyword data providers behind one small interface, so a second provider can be added later.

Today there is one: DataForSEO (https://api.dataforseo.com/v3, HTTP Basic auth with the API login + password).
Every method returns normalised rows and the provider's reported cost in USD; caching, spend tracking and
budgets live in `service.py`, not here.

Normalised keyword row:
  {keyword, volume, kd, cpc, competition, competition_level, intent, trend: [int], trend_months: ["YYYY-MM"],
   serp_features: [str], source, estimated}
"""

from __future__ import annotations

import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests put an httpx.MockTransport here
TIMEOUT = httpx.Timeout(90.0, connect=15.0)

# Locations / languages offered in the UI (DataForSEO location codes; India is the default).
LOCATIONS: dict[int, str] = {
    2356: "India", 2840: "United States", 2826: "United Kingdom", 2784: "United Arab Emirates", 2702: "Singapore",
    2036: "Australia", 2124: "Canada", 2276: "Germany", 2682: "Saudi Arabia", 2458: "Malaysia",
}
LANGUAGES: dict[str, str] = {"en": "English", "hi": "Hindi"}
DEVICES = ("desktop", "mobile")

# Estimated list prices (USD) used before a call is made. The real charge reported by the provider is recorded.
LABS_TASK = 0.01
LABS_ROW = 0.0001
SERP_LIVE = 0.002


class ProviderError(Exception):
    """A provider call failed. `message` is safe to show to users."""


@dataclass
class Result:
    rows: list[dict]
    cost: float = 0.0
    meta: dict = field(default_factory=dict)


class KeywordDataProvider(ABC):
    id: str = ""
    label: str = ""

    @abstractmethod
    async def keyword_suggestions(self, seed: str, *, location_code: int, language_code: str, limit: int) -> Result: ...

    @abstractmethod
    async def related_keywords(self, seed: str, *, location_code: int, language_code: str, limit: int) -> Result: ...

    @abstractmethod
    async def keyword_ideas(self, seeds: list[str], *, location_code: int, language_code: str, limit: int) -> Result: ...

    @abstractmethod
    async def ranked_keywords(self, domain: str, *, location_code: int, language_code: str, limit: int) -> Result: ...

    @abstractmethod
    async def domain_intersection(self, first: str, second: str, *, intersections: bool, location_code: int,
                                  language_code: str, limit: int) -> Result: ...

    @abstractmethod
    async def competitors_domain(self, domain: str, *, location_code: int, language_code: str, limit: int) -> Result: ...

    @abstractmethod
    async def serp_organic(self, keyword: str, *, location_code: int, language_code: str, device: str,
                           depth: int = 100) -> Result: ...

    def estimate(self, method: str, **params) -> float:
        """Estimated cost in USD of one call (before it is made)."""
        if method == "serp_organic":
            return SERP_LIVE * max(1, (int(params.get("depth") or 100) + 99) // 100)
        return round(LABS_TASK + LABS_ROW * int(params.get("limit") or 100), 6)


# ============================================================ helpers
def clean_domain(value: str | None) -> str:
    """'https://www.Example.com/path' → 'example.com'."""
    v = (value or "").strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].split(":", 1)[0]
    if v.startswith("www."):
        v = v[4:]
    return v.strip(".")


def domain_matches(host: str | None, domain: str) -> bool:
    h = clean_domain(host)
    d = clean_domain(domain)
    return bool(h and d) and (h == d or h.endswith("." + d))


def _num(v, cast=float):
    try:
        return cast(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def norm_keyword(kd: dict | None, *, source: str = "dataforseo") -> dict | None:
    """Normalise a DataForSEO Labs keyword object (keyword + keyword_info + keyword_properties + ...)."""
    if not isinstance(kd, dict) or not str(kd.get("keyword") or "").strip():
        return None
    info = kd.get("keyword_info") or {}
    props = kd.get("keyword_properties") or {}
    intent = (kd.get("search_intent_info") or {}).get("main_intent")
    serp = kd.get("serp_info") or {}
    months = sorted(
        [m for m in (info.get("monthly_searches") or []) if isinstance(m, dict) and m.get("year") and m.get("month")],
        key=lambda m: (int(m["year"]), int(m["month"])),
    )[-12:]
    return {
        "keyword": " ".join(str(kd["keyword"]).split())[:200],
        "volume": _num(info.get("search_volume"), int),
        "kd": _num(props.get("keyword_difficulty"), int),
        "cpc": round(_num(info.get("cpc")) or 0, 2) if info.get("cpc") is not None else None,
        "competition": round(_num(info.get("competition")) or 0, 2) if info.get("competition") is not None else None,
        "competition_level": info.get("competition_level"),
        "intent": str(intent).lower() if intent else None,
        "trend": [int(m.get("search_volume") or 0) for m in months],
        "trend_months": [f"{int(m['year']):04d}-{int(m['month']):02d}" for m in months],
        "serp_features": [t for t in (serp.get("serp_item_types") or []) if isinstance(t, str) and t != "organic"][:12],
        "source": source,
        "estimated": False,
    }


def _serp_el(el: dict | None) -> dict | None:
    if not isinstance(el, dict):
        return None
    item = el.get("serp_item") if isinstance(el.get("serp_item"), dict) else el
    pos = _num(item.get("rank_group"), int)
    if pos is None:
        return None
    return {"position": pos, "url": item.get("url"), "type": item.get("type")}


# ============================================================ DataForSEO
class DataForSEO(KeywordDataProvider):
    id = "dataforseo"
    label = "DataForSEO"

    def __init__(self, login: str, password: str, base_url: str | None = None):
        if not login or not password:
            raise ProviderError("DataForSEO needs the API login and API password.")
        self.base = (base_url or "https://api.dataforseo.com/v3").rstrip("/")
        token = base64.b64encode(f"{login}:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    async def post(self, endpoint: str, task: dict) -> tuple[list, float]:
        """POST one task (live endpoints take one task per call). Returns (result list, cost USD)."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT) as c:
                r = await c.post(f"{self.base}/{endpoint}", json=[task], headers=self._headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"Couldn't reach DataForSEO ({type(e).__name__}). Try again in a minute.") from e
        if r.status_code == 401:
            raise ProviderError("DataForSEO rejected the API login or password. Check them in Settings → Integrations.")
        if r.status_code == 402:
            raise ProviderError("DataForSEO says the account is out of credit. Top up the balance to continue.")
        if r.status_code >= 500:
            raise ProviderError(f"DataForSEO is having trouble right now (HTTP {r.status_code}). Try again later.")
        try:
            body = r.json()
        except ValueError as e:
            raise ProviderError(f"DataForSEO sent an unexpected reply (HTTP {r.status_code}).") from e
        if r.status_code >= 400 or body.get("status_code") not in (20000, None):
            raise ProviderError(_friendly(body.get("status_code"), body.get("status_message"), r.status_code))
        tasks = body.get("tasks") or []
        if not tasks:
            raise ProviderError("DataForSEO returned no result for this request.")
        t = tasks[0] or {}
        if t.get("status_code") != 20000:
            raise ProviderError(_friendly(t.get("status_code"), t.get("status_message"), r.status_code))
        cost = float(t.get("cost") if t.get("cost") is not None else body.get("cost") or 0)
        return [x for x in (t.get("result") or []) if isinstance(x, dict)], cost

    async def account(self) -> dict:
        """Free call: the account's balance (used by the connection test)."""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT) as c:
                r = await c.get(f"{self.base}/appendix/user_data", headers=self._headers)
        except httpx.HTTPError as e:
            raise ProviderError(f"Couldn't reach DataForSEO ({type(e).__name__}).") from e
        if r.status_code == 401:
            raise ProviderError("DataForSEO rejected the API login or password.")
        try:
            body = r.json()
            t = (body.get("tasks") or [{}])[0]
            if t.get("status_code") != 20000:
                raise ProviderError(_friendly(t.get("status_code"), t.get("status_message"), r.status_code))
            res = (t.get("result") or [{}])[0] or {}
        except (ValueError, AttributeError, IndexError) as e:
            raise ProviderError(f"DataForSEO sent an unexpected reply (HTTP {r.status_code}).") from e
        money = res.get("money") or {}
        return {"login": res.get("login"), "balance": money.get("balance"), "currency": "USD"}

    @staticmethod
    def _base(location_code: int, language_code: str) -> dict:
        return {"location_code": int(location_code), "language_code": language_code}

    async def keyword_suggestions(self, seed, *, location_code, language_code, limit):
        res, cost = await self.post("dataforseo_labs/google/keyword_suggestions/live", {
            "keyword": seed, **self._base(location_code, language_code), "include_seed_keyword": True,
            "include_serp_info": True, "limit": int(limit), "order_by": ["keyword_info.search_volume,desc"],
        })
        rows: list[dict] = []
        for r in res:
            seed_row = norm_keyword(r.get("seed_keyword_data") if isinstance(r.get("seed_keyword_data"), dict) else None)
            if seed_row:
                rows.append({**seed_row, "seed": True})
            rows += [x for x in (norm_keyword(i) for i in (r.get("items") or [])) if x]
        return Result(rows, cost, {"total_count": res[0].get("total_count") if res else 0})

    async def related_keywords(self, seed, *, location_code, language_code, limit):
        res, cost = await self.post("dataforseo_labs/google/related_keywords/live", {
            "keyword": seed, **self._base(location_code, language_code), "depth": 2, "include_seed_keyword": True,
            "include_serp_info": True, "limit": int(limit),
        })
        rows = []
        for r in res:
            for i in r.get("items") or []:
                row = norm_keyword((i or {}).get("keyword_data"))
                if row:
                    rows.append({**row, "depth": i.get("depth")})
        return Result(rows, cost, {"total_count": res[0].get("total_count") if res else 0})

    async def keyword_ideas(self, seeds, *, location_code, language_code, limit):
        res, cost = await self.post("dataforseo_labs/google/keyword_ideas/live", {
            "keywords": list(seeds)[:200], **self._base(location_code, language_code), "include_serp_info": True,
            "limit": int(limit), "order_by": ["keyword_info.search_volume,desc"],
        })
        rows = [x for r in res for x in (norm_keyword(i) for i in (r.get("items") or [])) if x]
        return Result(rows, cost, {"total_count": res[0].get("total_count") if res else 0})

    async def ranked_keywords(self, domain, *, location_code, language_code, limit):
        res, cost = await self.post("dataforseo_labs/google/ranked_keywords/live", {
            "target": clean_domain(domain), **self._base(location_code, language_code), "limit": int(limit),
            "order_by": ["keyword_data.keyword_info.search_volume,desc"],
        })
        rows = []
        for r in res:
            for i in r.get("items") or []:
                row = norm_keyword((i or {}).get("keyword_data"))
                el = _serp_el((i or {}).get("ranked_serp_element"))
                if row:
                    rows.append({**row, "position": el["position"] if el else None, "url": el["url"] if el else None})
        return Result(rows, cost, {"total_count": res[0].get("total_count") if res else 0})

    async def domain_intersection(self, first, second, *, intersections, location_code, language_code, limit):
        res, cost = await self.post("dataforseo_labs/google/domain_intersection/live", {
            "target1": clean_domain(first), "target2": clean_domain(second), "intersections": bool(intersections),
            **self._base(location_code, language_code), "limit": int(limit),
            "order_by": ["keyword_data.keyword_info.search_volume,desc"],
        })
        rows = []
        for r in res:
            for i in r.get("items") or []:
                row = norm_keyword((i or {}).get("keyword_data"))
                if row:
                    rows.append({**row, "first": _serp_el(i.get("first_domain_serp_element")),
                                 "second": _serp_el(i.get("second_domain_serp_element"))})
        return Result(rows, cost, {"total_count": res[0].get("total_count") if res else 0})

    async def competitors_domain(self, domain, *, location_code, language_code, limit):
        res, cost = await self.post("dataforseo_labs/google/competitors_domain/live", {
            "target": clean_domain(domain), **self._base(location_code, language_code), "limit": int(limit),
            "exclude_top_domains": True,
        })
        rows = []
        me = clean_domain(domain)
        for r in res:
            for i in r.get("items") or []:
                d = clean_domain((i or {}).get("domain"))
                if not d or d == me:
                    continue
                organic = ((i.get("full_domain_metrics") or i.get("metrics") or {}).get("organic") or {})
                rows.append({"domain": d, "shared_keywords": _num(i.get("intersections"), int),
                             "avg_position": round(_num(i.get("avg_position")) or 0, 1),
                             "keywords": _num(organic.get("count"), int), "traffic": _num(organic.get("etv"))})
        return Result(rows, cost)

    async def serp_organic(self, keyword, *, location_code, language_code, device, depth=100):
        res, cost = await self.post("serp/google/organic/live/advanced", {
            "keyword": keyword, **self._base(location_code, language_code), "device": device if device in DEVICES else "desktop",
            "depth": int(depth),
        })
        r = res[0] if res else {}
        organic = []
        for i in r.get("items") or []:
            if isinstance(i, dict) and i.get("type") == "organic" and i.get("url"):
                organic.append({"position": _num(i.get("rank_group"), int), "rank_absolute": _num(i.get("rank_absolute"), int),
                                "url": i.get("url"), "domain": clean_domain(i.get("domain") or i.get("url")),
                                "title": (i.get("title") or "")[:300]})
        features = [t for t in (r.get("item_types") or []) if isinstance(t, str) and t != "organic"]
        return Result(organic, cost, {"serp_features": features, "check_url": r.get("check_url"),
                                      "results_count": r.get("se_results_count")})


def _friendly(code, message, http_status) -> str:
    code = int(code or 0)
    msg = str(message or "").strip()
    if code in (40100, 40101, 40102, 40103, 40104):
        return "DataForSEO rejected the API login or password. Check them in Settings → Integrations."
    if code in (40200, 40210):
        return "DataForSEO says the account is out of credit. Top up the balance to continue."
    if code == 40202:
        return "DataForSEO's rate limit was reached. Try again in a minute."
    if code in (40501, 40503):
        return f"DataForSEO couldn't use one of the settings: {msg or 'invalid field'}."
    if code == 40400:
        return "DataForSEO doesn't offer this data for the chosen location or language."
    return f"DataForSEO error{f' {code}' if code else f' (HTTP {http_status})'}: {msg or 'unknown problem'}."[:400]
