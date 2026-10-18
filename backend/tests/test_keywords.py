"""Keywords & rankings: DataForSEO adapter parsing and errors, research (provider + cache + spend + budget, and the free
Search Console / AI fallback), competitor keyword gap and CSV export, keyword lists with SERP / embedding / word
clustering, "Write article", rank tracking (positions, history, movers, visibility, alerts, Search Console only mode,
daily schedule), permissions and tenant isolation.

DataForSEO is mocked with an httpx.MockTransport returning JSON shaped like their documented live responses; the LLM
router's `complete` / `embed_texts` are faked. No network.
"""

import base64
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select, update

import app.modules.keywords as kw_mod
from app.core.crypto import encrypt_json
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import AnalyticsSnapshot, Competitor, Job, KeywordCache, ProviderConfig, ProviderSpend, RankCheck, SeoArticle, TrackedKeyword
from app.services.jobs import run_inline
from app.services import search
from app.services.keywords import free as free_mod
from app.services.keywords import provider as prov_mod
from app.services.keywords import ranks
from app.services.keywords import service as svc
from app.services.llm import router as llm_router
from app.services.llm.router import LlmResult, NoProviderError
from app.services.tenancy import provision_organization
from tests.conftest import auth, login

PW = "Str0ngPassw0rd!"
STATE: dict = {}


# ------------------------------------------------------------------ DataForSEO-shaped fixtures
def _kw(keyword, volume, kd, cpc=1.2, intent="commercial", features=("organic", "people_also_ask")):
    months = [{"year": 2026, "month": m, "search_volume": volume + m * 10} for m in range(9, 0, -1)]  # newest first, like the API
    return {
        "se_type": "google", "keyword": keyword, "location_code": 2356, "language_code": "en",
        "keyword_info": {"se_type": "google", "last_updated_time": "2026-10-01 10:00:00 +00:00", "competition": 0.41,
                         "competition_level": "MEDIUM", "cpc": cpc, "search_volume": volume, "low_top_of_page_bid": 0.5,
                         "high_top_of_page_bid": 2.1, "categories": [10019], "monthly_searches": months,
                         "search_volume_trend": {"monthly": 5, "quarterly": 10, "yearly": 20}},
        "keyword_properties": {"se_type": "google", "core_keyword": None, "synonym_clustering_algorithm": "text_processing",
                               "keyword_difficulty": kd, "detected_language": "en", "is_another_language": False},
        "serp_info": {"se_type": "google", "check_url": "https://www.google.co.in/search?q=x", "serp_item_types": list(features),
                      "se_results_count": 1200000, "last_updated_time": "2026-10-01", "previous_updated_time": "2026-09-01"},
        "avg_backlinks_info": {"backlinks": 12.5, "referring_domains": 4.1},
        "search_intent_info": {"se_type": "google", "main_intent": intent, "foreign_intent": ["informational"],
                               "last_updated_time": "2026-10-01"},
    }


def _envelope(result, cost=0.0103, path=("v3",), status=20000, message="Ok."):
    return {"version": "0.1.20260901", "status_code": 20000, "status_message": "Ok.", "time": "0.4 sec.", "cost": cost,
            "tasks_count": 1, "tasks_error": 0 if status == 20000 else 1,
            "tasks": [{"id": "10171200-1234-0367-0000-a1b2c3d4e5f6", "status_code": status, "status_message": message,
                       "time": "0.3 sec.", "cost": cost if status == 20000 else 0, "result_count": len(result or []),
                       "path": list(path), "data": {}, "result": result}]}


SUGGESTIONS = [_kw("hrms software india", 2400, 38), _kw("best hrms software", 1900, 52, intent="commercial"),
               _kw("hrms software for small business", 320, 21, intent="transactional"),
               _kw("what is hrms software", 210, 12, intent="informational")]
RANKS_BY_DOMAIN = {"kw-co.test": {"hrms software india": 8, "payroll software": 3, "leave management app": 25}}
GAP = {  # competitor → {keyword: (their pos, our pos or None)}
    "rival.test": {"hrms software india": (2, 8), "payroll software": (9, 3), "attendance app": (4, None)},
    "other.test": {"hrms software india": (5, 8), "employee portal": (7, None)},
}
SERPS = {  # keyword → top organic domains/urls (our site may appear)
    "hrms software": ["a.test/1", "b.test/2", "c.test/3", "d.test/4", "kw-co.test/hrms"],
    "hrms software india": ["a.test/1", "b.test/2", "c.test/3", "x.test/9", "y.test/8"],
    "payroll software": ["p.test/1", "q.test/2", "r.test/3", "kw-co.test/payroll", "s.test/5"],
    "payroll app": ["p.test/1", "q.test/2", "r.test/3", "t.test/4"],
    "leave policy template": ["l.test/1", "m.test/2"],
}


def _labs_item(keyword, extra=None):
    return {"se_type": "google", "keyword_data": _kw(keyword, 500 + len(keyword) * 10, 30), **(extra or {})}


def _serp_el(pos, url):
    return {"se_type": "google", "type": "organic", "rank_group": pos, "rank_absolute": pos + 1, "position": "left",
            "url": f"https://{url}", "domain": url.split("/")[0], "etv": 12.3}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    STATE.setdefault("calls", []).append(path)
    STATE["auth"] = request.headers.get("authorization")
    if STATE.get("http_status"):
        return httpx.Response(STATE["http_status"], json={"status_code": 40100, "status_message": "Not authorized"})
    if path.endswith("/appendix/user_data"):
        return httpx.Response(200, json=_envelope([{"login": "owner@kw-co.test", "money": {"balance": 12.5}}], cost=0))
    task = json.loads(request.content)[0]
    STATE.setdefault("tasks", []).append((path, task))
    if STATE.get("task_status"):
        return httpx.Response(200, json=_envelope(None, status=STATE["task_status"], message="Payment Required."))
    if path.endswith("keyword_suggestions/live"):
        return httpx.Response(200, json=_envelope([{
            "se_type": "google", "seed_keyword": task["keyword"], "seed_keyword_data": _kw(task["keyword"], 5400, 61),
            "location_code": 2356, "language_code": "en", "total_count": 4, "items_count": 4, "offset": 0,
            "offset_token": "abc", "items": SUGGESTIONS}], cost=0.0104))
    if path.endswith("related_keywords/live"):
        return httpx.Response(200, json=_envelope([{"seed_keyword": task["keyword"], "total_count": 2, "items": [
            {"se_type": "google", "keyword_data": _kw("hr software", 880, 40), "depth": 1, "related_keywords": ["hrms"]},
            {"se_type": "google", "keyword_data": _kw("hrms meaning", 300, 5, intent="informational"), "depth": 2}]}]))
    if path.endswith("ranked_keywords/live"):
        target = task["target"]
        return httpx.Response(200, json=_envelope([{"target": target, "total_count": 3, "items_count": 3, "items": [
            _labs_item(k, {"ranked_serp_element": {"se_type": "google", "serp_item": _serp_el(p, f"{target}/{k.replace(' ', '-')}"),
                                                   "check_url": "https://google.co.in", "keyword_difficulty": 30, "is_lost": False}})
            for k, p in RANKS_BY_DOMAIN.get(target, {}).items()]}]))
    if path.endswith("domain_intersection/live"):
        t1, t2, inter = task["target1"], task["target2"], task["intersections"]
        items = []
        if inter:  # t1 = us, t2 = competitor: both rank
            for k, (theirs, ours) in GAP.get(t2, {}).items():
                if ours is not None:
                    items.append(_labs_item(k, {"first_domain_serp_element": _serp_el(ours, f"{t1}/{k[:4]}"),
                                                "second_domain_serp_element": _serp_el(theirs, f"{t2}/{k[:4]}")}))
        else:  # t1 = competitor ranks, t2 = us does not
            for k, (theirs, ours) in GAP.get(t1, {}).items():
                if ours is None:
                    items.append(_labs_item(k, {"first_domain_serp_element": _serp_el(theirs, f"{t1}/{k[:4]}"),
                                                "second_domain_serp_element": None}))
        return httpx.Response(200, json=_envelope([{"total_count": len(items), "items_count": len(items), "items": items}],
                                                  cost=0.0102))
    if path.endswith("competitors_domain/live"):
        return httpx.Response(200, json=_envelope([{"target": task["target"], "total_count": 3, "items": [
            {"se_type": "google", "domain": task["target"], "avg_position": 10, "intersections": 100},
            {"se_type": "google", "domain": "rival.test", "avg_position": 7.4, "sum_position": 500, "intersections": 61,
             "full_domain_metrics": {"organic": {"pos_1": 3, "count": 1200, "etv": 4500.5}}},
            {"se_type": "google", "domain": "www.other.test", "avg_position": 11.2, "intersections": 20,
             "metrics": {"organic": {"count": 300, "etv": 120.0}}}]}]))
    if path.endswith("serp/google/organic/live/advanced"):
        k = task["keyword"]
        urls = STATE.get("serps", SERPS).get(k, [])
        items = [{"type": "featured_snippet", "rank_group": 1, "rank_absolute": 1, "domain": "snip.test", "url": "https://snip.test"}]
        for n, u in enumerate(urls, start=1):
            items.append({"type": "organic", "rank_group": n, "rank_absolute": n + 1, "position": "left", "xpath": "/html/body",
                          "domain": ("www." + u.split("/")[0]) if n == 2 else u.split("/")[0], "title": f"Result {n}",
                          "url": f"https://{u}", "description": "…", "breadcrumb": u})
        items.insert(3, {"type": "people_also_ask", "rank_group": 1, "rank_absolute": 4, "items": []})
        return httpx.Response(200, json=_envelope([{
            "keyword": k, "type": "organic", "se_domain": "google.co.in", "location_code": task["location_code"],
            "language_code": task["language_code"], "check_url": "https://www.google.co.in/search?q=x",
            "datetime": "2026-10-17 06:00:00 +00:00", "spell": None, "item_types": ["featured_snippet", "organic", "people_also_ask"],
            "se_results_count": 1000000, "items_count": len(items), "items": items}], cost=0.002))
    return httpx.Response(404, json={"status_code": 40400, "status_message": "Not Found."})


def _llm_payload(system: str, prompt: str):
    if system == svc.IDEAS_SYSTEM:
        return {"keywords": [{"keyword": "hrms for startups", "intent": "commercial"},
                             {"keyword": "how to choose an hrms", "intent": "informational"}, {"keyword": ""}, "payroll checklist"]}
    from app.services.keywords import clusters

    if system == free_mod.INTENT_SYSTEM:
        STATE["intent_prompt"] = prompt
        return {"intents": {"hrms software free": "commercial", "hrms software india": "commercial"}}
    if system == clusters.CLUSTER_SYSTEM:
        return {"clusters": [{"name": "Payroll", "keywords": ["payroll software", "payroll app", "nonexistent"]}]}
    raise AssertionError(system[:60])


async def fake_complete(session, task, prompt=None, *, messages=None, system=None, json_mode=False, temperature=0.7,
                        max_tokens=1500):
    if STATE.get("no_ai"):
        raise NoProviderError("No AI text provider is set up.")
    return LlmResult(json.dumps(_llm_payload(system or "", prompt or "")), "fake", uuid.uuid4(), "fake-model")


async def fake_embed(session, texts):
    if not STATE.get("vectors"):
        return None, None
    return [STATE["vectors"](t) for t in texts], "fake:embed"


AUTOCOMPLETE = {  # query → Google Autocomplete suggestions (client=firefox shape: [query, [suggestions]])
    "hrms software": ["hrms software", "hrms software india", "hrms software free", "hrms software for small business"],
    "best hrms software": ["best hrms software", "best hrms software india"],
    "hrms software a": ["hrms software app", "hrms software india"],
}


def _autocomplete(request: httpx.Request) -> httpx.Response:
    q = request.url.params.get("q")
    STATE.setdefault("autocomplete", []).append((q, request.url.params.get("gl"), request.url.params.get("client")))
    if STATE.get("no_autocomplete"):
        return httpx.Response(503)
    return httpx.Response(200, content=json.dumps([q, AUTOCOMPLETE.get(q, [])]).encode(),
                          headers={"content-type": "text/javascript; charset=UTF-8"})


async def fake_web_search(session, query, n=10):
    STATE.setdefault("web", []).append(query)
    if STATE.get("no_search"):
        raise search.SearchError("Web search failed. DuckDuckGo: blocked")
    urls = STATE.get("serps", SERPS).get(query, [])
    return [{"title": f"R{i}", "url": f"https://{u}", "snippet": ""} for i, u in enumerate(urls[:n])]


async def fake_search_all(session, query, n=10, *, freshness=None, max_providers=3):
    if STATE.get("no_search"):
        raise search.SearchError("Web search failed.")
    return [{"title": "How to choose HRMS software? | Blog", "url": "https://blog.test/a", "snippet": "", "provider": "fake"},
            {"title": "What is HRMS software - Guide", "url": "https://blog.test/b", "snippet": "", "provider": "fake"},
            {"title": "HRMS pricing page", "url": "https://blog.test/c", "snippet": "", "provider": "fake"}], []


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    STATE.clear()
    monkeypatch.setattr(prov_mod, "TRANSPORT", httpx.MockTransport(_handler))
    monkeypatch.setattr(free_mod, "TRANSPORT", httpx.MockTransport(_autocomplete))
    monkeypatch.setattr(free_mod, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(search, "web_search", fake_web_search)
    monkeypatch.setattr(search, "search_all", fake_search_all)
    monkeypatch.setattr(llm_router, "complete", fake_complete)
    monkeypatch.setattr(llm_router, "embed_texts", fake_embed)

    async def capture(org_id, event, title, body=None, **kw):
        STATE.setdefault("notified", []).append((event, title, body))
    monkeypatch.setattr(kw_mod, "notify_org", capture)
    yield


# ------------------------------------------------------------------ helpers
class tenant:
    def __init__(self, org_id):
        self.s = SessionLocal()
        set_tenant(self.s, org_id)

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        await self.s.close()


async def new_org(client, *, provider=True, name="KW Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@kw-test.in"
    async with platform_session() as s:
        org, _, _ = await provision_organization(s, name=f"{name} {uuid.uuid4().hex[:5]}", admin_email=email, admin_name="Owner",
                                                 admin_password=PW, timezone="Asia/Kolkata", website="https://www.kw-co.test")
        await s.commit()
        oid = org.id
    if provider:
        async with tenant(oid) as s:
            s.add(ProviderConfig(organization_id=oid, kind="seo_data", provider="dataforseo", name="DataForSEO",
                                 secrets_enc=encrypt_json({"login": "owner@kw-co.test", "password": "s3cret"}), settings={}))
            await s.commit()
    tokens = await login(client, email, PW)
    return {"id": oid, "h": auth(tokens)}


async def add_gsc(org_id, queries):
    async with tenant(org_id) as s:
        s.add(AnalyticsSnapshot(organization_id=org_id, source="gsc", key="sc-domain:kw-co.test",
                                data={"site": "sc-domain:kw-co.test", "queries": queries}))
        await s.commit()


async def job_for(org_id, job_id):
    async with tenant(org_id) as s:
        return await s.scalar(select(Job).where(Job.id == uuid.UUID(str(job_id))))


async def run_job(org, job_id, expect_ok=True):
    j = await job_for(org["id"], job_id)
    try:
        return await run_inline(j.kind, org["id"], j.payload)
    except Exception as e:  # noqa: BLE001
        if expect_ok:
            raise
        return e
    finally:  # run_inline doesn't touch the queue row; mark it finished so a new request queues a new job
        async with tenant(org["id"]) as s:
            await s.execute(update(Job).where(Job.id == j.id).values(status="done"))
            await s.commit()


@pytest.fixture
async def org(client, admin):
    return await new_org(client)


# ------------------------------------------------------------------ adapter
async def test_dataforseo_adapter_parses_documented_shapes():
    p = prov_mod.DataForSEO("owner@kw-co.test", "s3cret")
    res = await p.keyword_suggestions(seed="hrms software", location_code=2356, language_code="en", limit=50)
    assert STATE["auth"] == "Basic " + base64.b64encode(b"owner@kw-co.test:s3cret").decode()
    assert res.cost == pytest.approx(0.0104)
    seed, first = res.rows[0], res.rows[1]
    assert seed["keyword"] == "hrms software" and seed["seed"] is True and seed["volume"] == 5400 and seed["kd"] == 61
    assert first == {**first, "keyword": "hrms software india", "volume": 2400, "kd": 38, "cpc": 1.2, "competition": 0.41,
                     "competition_level": "MEDIUM", "intent": "commercial", "estimated": False, "source": "dataforseo"}
    assert first["trend_months"][0] == "2026-01" and first["trend_months"][-1] == "2026-09"  # oldest → newest
    assert first["trend"] == [2410 + i * 10 for i in range(9)]
    assert first["serp_features"] == ["people_also_ask"]
    path, task = STATE["tasks"][0]
    assert path == "/v3/dataforseo_labs/google/keyword_suggestions/live"
    assert task["location_code"] == 2356 and task["language_code"] == "en" and task["limit"] == 50

    serp = await p.serp_organic(keyword="hrms software", location_code=2356, language_code="en", device="mobile")
    assert [r["position"] for r in serp.rows] == [1, 2, 3, 4, 5] and serp.rows[1]["domain"] == "b.test"
    assert serp.meta["serp_features"] == ["featured_snippet", "people_also_ask"]
    assert STATE["tasks"][-1][1]["device"] == "mobile" and STATE["tasks"][-1][1]["depth"] == 100
    assert ranks.find_position(serp.rows, "www.kw-co.test") == (5, "https://kw-co.test/hrms")

    comps = await p.competitors_domain(domain="kw-co.test", location_code=2356, language_code="en", limit=10)
    assert [c["domain"] for c in comps.rows] == ["rival.test", "other.test"]
    assert comps.rows[0]["keywords"] == 1200 and comps.rows[0]["shared_keywords"] == 61
    assert (await p.account())["balance"] == 12.5


async def test_dataforseo_errors_are_friendly():
    p = prov_mod.DataForSEO("x", "y")
    STATE["http_status"] = 401
    with pytest.raises(prov_mod.ProviderError, match="login or password"):
        await p.ranked_keywords(domain="a.test", location_code=2356, language_code="en", limit=10)
    STATE.pop("http_status")
    STATE["task_status"] = 40200
    with pytest.raises(prov_mod.ProviderError, match="out of credit"):
        await p.keyword_ideas(seeds=["a"], location_code=2356, language_code="en", limit=10)
    with pytest.raises(prov_mod.ProviderError, match="login and API password"):
        prov_mod.DataForSEO("", "")
    assert prov_mod.clean_domain("https://WWW.Example.com:443/path?q=1") == "example.com"
    assert prov_mod.domain_matches("blog.example.com", "example.com") and not prov_mod.domain_matches("badexample.com", "example.com")


async def test_connection_test_and_catalog(client, org):
    cat = (await client.get("/integrations/catalog", headers=org["h"])).json()
    spec = next(p for p in cat["providers"] if p["id"] == "dataforseo")
    assert spec["kind"] == "seo_data" and {f["name"] for f in spec["fields"]} == {"login", "password"}
    prov = next(p for p in (await client.get("/integrations/providers", headers=org["h"])).json() if p["provider"] == "dataforseo")
    assert prov["secrets"]["password"].startswith("••") and "s3cret" not in json.dumps(prov)
    r = await client.post(f"/integrations/providers/{prov['id']}/test", headers=org["h"])
    assert r.status_code == 200 and "Balance: $12.50" in r.json()["message"], r.text


# ------------------------------------------------------------------ research
async def test_research_with_provider_cache_and_spend(client, org):
    await add_gsc(org["id"], [{"query": "HRMS software India", "clicks": 4, "impressions": 300, "ctr": 0.013, "position": 9.4}])
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "research", "research": {"seeds": ["hrms software"]}})).json()
    assert est["cost_usd"] == pytest.approx(0.02) and est["cached"] is False
    r = await client.post("/keywords/research", headers=org["h"], json={"seeds": ["hrms software"], "limit": 100})
    assert r.status_code == 202, r.text
    out = await run_job(org, r.json()["job_id"])
    assert out["count"] == 5 and out["estimated"] is False
    rep = (await client.get(f"/keywords/reports/{out['report_id']}", headers=org["h"])).json()
    assert rep["status"] == "done" and rep["meta"]["cost_usd"] == pytest.approx(0.0104) and rep["meta"]["cached"] == 0
    assert [x["keyword"] for x in rep["rows"]][:2] == ["hrms software", "hrms software india"]  # by volume
    india = next(x for x in rep["rows"] if x["keyword"] == "hrms software india")
    assert india["gsc"]["impressions"] == 300 and india["gsc"]["position"] == 9.4  # Search Console side by side

    # same request again: served from the cache, no new call, no new spend
    n_calls = len(STATE["calls"])
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "research", "research": {"seeds": ["hrms software"]}})).json()
    assert est["cached"] is True and est["cost_usd"] == 0
    r = await client.post("/keywords/research", headers=org["h"], json={"seeds": ["hrms software"]})
    out2 = await run_job(org, r.json()["job_id"])
    assert len(STATE["calls"]) == n_calls and out2["cost_usd"] == 0
    spend = (await client.get("/keywords/spend", headers=org["h"])).json()
    assert spend["today_usd"] == pytest.approx(0.0104) and spend["by_purpose"][0]["calls"] == 1
    ov = (await client.get("/keywords/overview", headers=org["h"])).json()
    assert ov["provider"]["configured"] and ov["recent_seeds"][0] == "hrms software" and ov["our_domain"] == "kw-co.test"

    # a domain as seed → what that domain ranks for
    r = await client.post("/keywords/research", headers=org["h"], json={"seeds": ["https://www.kw-co.test/"]})
    out = await run_job(org, r.json()["job_id"])
    rep = (await client.get(f"/keywords/reports/{out['report_id']}", headers=org["h"])).json()
    assert rep["params"]["mode"] == "domain" and {x["keyword"]: x["position"] for x in rep["rows"]}["payroll software"] == 3
    csv_text = (await client.get(f"/keywords/reports/{out['report_id']}/export.csv", headers=org["h"])).text
    assert csv_text.splitlines()[0].startswith("keyword,volume,kd,cpc")
    reports = (await client.get("/keywords/reports?kind=research", headers=org["h"])).json()
    assert len(reports) == 3 and "rows" not in reports[0]


async def test_budget_stops_paid_calls(client, org):
    r = await client.put("/keywords/settings", headers=org["h"], json={"daily_budget_usd": 0.005})
    assert r.status_code == 200 and r.json()["daily_budget_usd"] == 0.005
    r = await client.post("/keywords/research", headers=org["h"], json={"seeds": ["payroll"], "mode": "related"})
    err = await run_job(org, r.json()["job_id"], expect_ok=False)
    assert "budget" in str(err)
    rep = (await client.get("/keywords/reports", headers=org["h"])).json()[0]
    assert rep["status"] == "failed" and "budget" in rep["error"]
    assert not any("related_keywords" in c for c in STATE.get("calls", []))


async def test_free_research_without_provider(client, admin):
    org = await new_org(client, provider=False)
    await add_gsc(org["id"], [
        {"query": "hrms software pricing", "clicks": 2, "impressions": 120, "ctr": 0.016, "position": 12.1},
        {"query": "hrms software india", "clicks": 9, "impressions": 480, "ctr": 0.02, "position": 7.0},
        {"query": "office party ideas", "clicks": 0, "impressions": 40, "ctr": 0, "position": 30},
    ])
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "research", "research": {"seeds": ["hrms"]}})).json()
    assert est["cost_usd"] == 0 and est["provider"] is None
    r = await client.post("/keywords/research", headers=org["h"], json={"seeds": ["hrms software"]})
    out = await run_job(org, r.json()["job_id"])
    rep = (await client.get(f"/keywords/reports/{out['report_id']}", headers=org["h"])).json()
    assert rep["status"] == "done" and rep["meta"]["estimated"] is True and "no search volumes" in rep["meta"]["note"].lower()
    kws = {x["keyword"]: x for x in rep["rows"]}
    assert {"hrms software", "hrms software free", "hrms software app", "hrms software pricing", "how to choose hrms software",
            "what is hrms software", "hrms for startups", "payroll checklist"} <= set(kws)
    assert "office party ideas" not in kws and all(x["volume"] is None and x["estimated"] for x in rep["rows"])
    # autocomplete prominence + Search Console impressions → estimated relative demand
    assert kws["hrms software india"]["demand"] == 100 and kws["hrms software india"]["gsc"]["impressions"] == 480
    assert 0 < kws["hrms software free"]["demand"] < 100
    assert kws["hrms software free"]["intent"] == "commercial" and kws["hrms software free"]["intent_guessed"]  # AI classified
    assert kws["what is hrms software"]["source"] == "web_search" and kws["what is hrms software"]["intent"] == "informational"
    assert kws["hrms software pricing"]["source"] == "search_console" and kws["hrms software pricing"]["intent"] == "transactional"
    assert kws["hrms for startups"]["source"] == "ai"
    qs = [q for q, _, _ in STATE["autocomplete"]]
    assert "hrms software" in qs and "best hrms software" in qs and "hrms software z" in qs
    assert all(gl == "in" and c == "firefox" for _, gl, c in STATE["autocomplete"])
    assert not STATE.get("calls")  # nothing paid was called
    # autocomplete answers are cached: running again makes no new requests
    n = len(STATE["autocomplete"])
    await run_job(org, (await client.post("/keywords/research", headers=org["h"], json={"seeds": ["hrms software"]})).json()["job_id"])
    assert len(STATE["autocomplete"]) == n

    # every free source down and no AI → a clear message, not a crash
    bare = await new_org(client, provider=False)
    STATE.update(no_ai=True, no_autocomplete=True, no_search=True)
    r = await client.post("/keywords/research", headers=bare["h"], json={"seeds": ["leave app"]})
    await run_job(bare, r.json()["job_id"], expect_ok=False)
    rep = (await client.get("/keywords/reports", headers=bare["h"])).json()[0]
    assert rep["status"] == "failed" and "try again" in rep["error"].lower()


async def test_free_gap_from_competitor_pages(client, admin):
    from app.models import KnowledgeSource as KS, KnowledgeDocument as KD

    org = await new_org(client, provider=False)
    await add_gsc(org["id"], [
        {"query": "payroll software india", "clicks": 30, "impressions": 900, "ctr": 0.03, "position": 4.0},
        {"query": "attendance tracking app", "clicks": 1, "impressions": 200, "ctr": 0.005, "position": 18.0},
    ])
    async with tenant(org["id"]) as s:
        comp = Competitor(organization_id=org["id"], name="Rival", website="https://rival.test", domain="rival.test",
                          status="confirmed", data={})
        s.add(comp)
        await s.flush()
        src = KS(organization_id=org["id"], kind="competitor", title="Rival", url="https://rival.test", competitor_id=comp.id,
                 status="ready", meta={})
        mine = KS(organization_id=org["id"], kind="website", title="Us", url="https://kw-co.test", status="ready", meta={})
        s.add_all([src, mine])
        await s.flush()
        for url, title, h1, h2 in [
            ("https://rival.test/payroll", "Payroll Software India | Rival", ["Payroll software India"], ["Contact us"]),
            ("https://rival.test/attendance", "Attendance Tracking App - Rival", [], ["Shift scheduling for factories"]),
            ("https://rival.test/blog/okr", "OKR templates for HR teams", [], []),
        ]:
            s.add(KD(organization_id=org["id"], source_id=src.id, url=url, title=title, content_hash=uuid.uuid4().hex, text="x",
                     meta={"title": title, "h1": h1, "h2": h2}))
        s.add(KD(organization_id=org["id"], source_id=mine.id, url="https://kw-co.test/shifts", title="Shift scheduling made easy",
                 content_hash=uuid.uuid4().hex, text="x", meta={"title": "Shift scheduling made easy", "h1": [], "h2": []}))
        await s.commit()
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "gap", "gap": {"competitors": ["rival.test"]}})).json()
    assert est["cost_usd"] == 0 and "approximate" in est["note"].lower()
    r = await client.post("/keywords/gap", headers=org["h"], json={"competitors": ["rival.test"]})
    assert r.status_code == 202, r.text  # never blocked by a missing paid key
    out = await run_job(org, r.json()["job_id"])
    rep = (await client.get(f"/keywords/reports/{out['report_id']}", headers=org["h"])).json()
    rows = {x["keyword"]: x for x in rep["rows"]}
    assert rep["meta"]["approximate"] and rep["meta"]["source"] == "free"
    assert rows["payroll software india"]["category"] == "strong" and rows["payroll software india"]["ours"] == 4
    assert rows["attendance tracking app"]["category"] == "weak" and rows["attendance tracking app"]["ours"] == 18
    assert rows["shift scheduling for factories"]["category"] == "weak" and rows["shift scheduling for factories"]["our_url"].endswith("/shifts")
    assert rows["okr templates for hr teams"]["category"] == "missing"
    assert rows["okr templates for hr teams"]["competitors"]["rival.test"]["url"] == "https://rival.test/blog/okr"
    assert "contact us" not in rows and rep["meta"]["counts"]["missing"] == 1
    sug = (await client.post("/keywords/gap/suggest", headers=org["h"])).json()
    assert sug["domains"][0]["domain"] == "rival.test" and sug["cost_usd"] == 0


# ------------------------------------------------------------------ gap
async def test_keyword_gap_categories_and_csv(client, org):
    async with tenant(org["id"]) as s:
        s.add(Competitor(organization_id=org["id"], name="Rival", website="https://www.rival.test", domain="rival.test",
                         status="confirmed", data={}))
        await s.commit()
    comps = (await client.get("/keywords/gap/competitors", headers=org["h"])).json()["competitors"]
    assert comps[0]["domain"] == "rival.test"
    sug = (await client.post("/keywords/gap/suggest", headers=org["h"])).json()
    assert [d["domain"] for d in sug["domains"]] == ["rival.test", "other.test"]

    body = {"competitors": ["https://rival.test", "other.test", "kw-co.test", "rival.test"]}
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "gap", "gap": body})).json()
    assert est["cost_usd"] == pytest.approx(4 * 0.04)  # 2 competitors × 2 calls × ($0.01 + 300 rows × $0.0001)
    r = await client.post("/keywords/gap", headers=org["h"], json=body)
    assert r.status_code == 202, r.text
    assert r.json()["report"]["params"]["competitors"] == ["rival.test", "other.test"]
    out = await run_job(org, r.json()["job_id"])
    rep = (await client.get(f"/keywords/reports/{out['report_id']}", headers=org["h"])).json()
    rows = {x["keyword"]: x for x in rep["rows"]}
    assert rows["attendance app"]["category"] == "missing" and rows["attendance app"]["competitors"]["rival.test"]["position"] == 4
    assert rows["employee portal"]["category"] == "missing"
    assert rows["hrms software india"]["category"] == "weak" and rows["hrms software india"]["ours"] == 8
    assert rows["hrms software india"]["best_competitor"] == 2 and set(rows["hrms software india"]["competitors"]) == {"rival.test", "other.test"}
    assert rows["payroll software"]["category"] == "strong" and rows["payroll software"]["shared"]
    assert rep["meta"]["counts"] == {"missing": 2, "weak": 1, "strong": 1, "shared": 2}
    assert rep["meta"]["cost_usd"] == pytest.approx(4 * 0.0102)
    csv_text = (await client.get(f"/keywords/reports/{out['report_id']}/export.csv?category=missing", headers=org["h"])).text
    lines = csv_text.strip().splitlines()
    assert lines[0].endswith("rival.test_position,other.test_position") and len(lines) == 3
    assert all(",missing," in ln for ln in lines[1:])


# ------------------------------------------------------------------ lists, clusters, articles
ITEMS = [{"keyword": k, "volume": v, "kd": 20, "intent": "commercial"} for k, v in
         [("hrms software", 5000), ("hrms software india", 2400), ("payroll software", 3000), ("payroll app", 400),
          ("leave policy template", 90)]]


async def test_lists_serp_clusters_and_write_article(client, org):
    r = await client.post("/keywords/lists", headers=org["h"], json={"name": "HRMS topics", "items": ITEMS[:3]})
    assert r.status_code == 201
    lid = r.json()["id"]
    r = await client.post(f"/keywords/lists/{lid}/items", headers=org["h"],
                          json={"items": ITEMS[2:] + [{"keyword": "  HRMS   software "}, {"keyword": ""}]})
    assert r.json()["added"] == 2 and r.json()["count"] == 5
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "cluster", "list_id": lid})).json()
    assert est["cost_usd"] == pytest.approx(5 * 0.002)
    r = await client.post(f"/keywords/lists/{lid}/cluster", headers=org["h"], json={"use_serp": True})
    out = await run_job(org, r.json()["job_id"])
    assert out["method"] == "serp"
    kl = (await client.get(f"/keywords/lists/{lid}", headers=org["h"])).json()
    groups = {c["pillar"]: sorted(c["keywords"]) for c in kl["clusters"]}
    assert groups == {"hrms software": ["hrms software", "hrms software india"], "payroll software": ["payroll app", "payroll software"],
                      "leave policy template": ["leave policy template"]}
    hr = next(c for c in kl["clusters"] if c["pillar"] == "hrms software")
    assert hr["volume"] == 7400 and hr["method"] == "serp"
    assert kl["cluster_meta"]["cost_usd"] == pytest.approx(0.01)
    assert all(t[1]["depth"] == 10 for t in STATE["tasks"] if "serp" in t[0])

    # one click: article draft for the cluster (pillar + supporting keywords)
    r = await client.post("/keywords/write-article", headers=org["h"], json={"list_id": lid, "cluster_id": hr["id"]})
    assert r.status_code == 201, r.text
    art = r.json()["article"]
    assert art["keyword"] == "hrms software" and art["secondary_keywords"] == ["hrms software india"]
    assert art["search_intent"] == "commercial" and art["status"] == "queued"
    async with tenant(org["id"]) as s:
        assert (await s.scalar(select(SeoArticle).where(SeoArticle.id == uuid.UUID(art["id"])))) is not None
        assert await s.scalar(select(Job).where(Job.kind == "seo.write", Job.payload["article_id"].as_string() == art["id"]))

    r = await client.post(f"/keywords/lists/{lid}/items/remove", headers=org["h"], json={"keywords": ["payroll app"]})
    assert r.json()["count"] == 4 and ["payroll software"] in [c["keywords"] for c in r.json()["clusters"]]
    csv_text = (await client.get(f"/keywords/lists/{lid}/export.csv", headers=org["h"])).text
    assert "hrms software india,hrms software," in csv_text


async def test_cluster_fallbacks_without_serp(client, admin):
    org = await new_org(client, provider=False)
    lid = (await client.post("/keywords/lists", headers=org["h"], json={"name": "L", "items": ITEMS})).json()["id"]
    # free: result overlap from the configured web search (approximate), no paid calls
    out = await run_job(org, (await client.post(f"/keywords/lists/{lid}/cluster", headers=org["h"], json={})).json()["job_id"])
    kl = (await client.get(f"/keywords/lists/{lid}", headers=org["h"])).json()
    assert out["method"] == "serp" and out["cost_usd"] == 0 and kl["cluster_meta"]["serp_source"] == "web_search"
    assert sorted(next(c for c in kl["clusters"] if c["pillar"] == "payroll software")["keywords"]) == ["payroll app", "payroll software"]
    assert not STATE.get("calls")
    # without search results: embeddings (hrms* together, payroll* together)
    STATE["vectors"] = lambda t: [1.0, 0.0, 0.1] if "hrms" in t else [0.0, 1.0, 0.1] if "payroll" in t else [0.0, 0.0, 1.0]
    out = await run_job(org, (await client.post(f"/keywords/lists/{lid}/cluster", headers=org["h"], json={"use_serp": False})).json()["job_id"])
    assert out["method"] == "embeddings" and out["clusters"] == 3
    # no embeddings → the AI groups them; leftovers grouped by shared words
    STATE.pop("vectors")
    out = await run_job(org, (await client.post(f"/keywords/lists/{lid}/cluster", headers=org["h"], json={"use_serp": False})).json()["job_id"])
    kl = (await client.get(f"/keywords/lists/{lid}", headers=org["h"])).json()
    assert out["method"] == "ai" and kl["clusters"][0]["name"] == "Payroll"
    assert sorted(kl["clusters"][0]["keywords"]) == ["payroll app", "payroll software"]
    assert sorted(sum((c["keywords"] for c in kl["clusters"]), [])) == sorted(i["keyword"] for i in ITEMS)
    # no AI either → shared words
    STATE["no_ai"] = True
    out = await run_job(org, (await client.post(f"/keywords/lists/{lid}/cluster", headers=org["h"], json={"use_serp": False})).json()["job_id"])
    kl = (await client.get(f"/keywords/lists/{lid}", headers=org["h"])).json()
    assert out["method"] == "words" and {c["pillar"]: len(c["keywords"]) for c in kl["clusters"]}["hrms software"] == 2


# ------------------------------------------------------------------ rank tracking
async def _shift_checks_back(org_id, days=1):
    """Pretend the existing checks happened `days` earlier (oldest first, so dates never collide)."""
    async with tenant(org_id) as s:
        for c in (await s.scalars(select(RankCheck).order_by(RankCheck.check_date))).all():
            await s.execute(update(RankCheck).where(RankCheck.id == c.id).values(
                check_date=(date.fromisoformat(c.check_date) - timedelta(days=days)).isoformat()))
        await s.execute(delete(KeywordCache))  # a day later the cached search results have expired
        await s.commit()


async def test_rank_tracking_checks_history_movers_and_alerts(client, org):
    await add_gsc(org["id"], [{"query": "payroll software", "clicks": 10, "impressions": 500, "ctr": 0.02, "position": 4.2}])
    await client.put("/keywords/settings", headers=org["h"], json={"max_tracked": 3})
    r = await client.post("/keywords/ranks", headers=org["h"], json={
        "keywords": [{"keyword": "hrms software", "volume": 5000}, {"keyword": "payroll software", "volume": 3000},
                     "leave policy template", "hrms software", "payroll app"],
        "target_url": "https://kw-co.test/hrms", "device": "desktop"})
    assert r.status_code == 201
    assert r.json()["added"] == 3 and r.json()["skipped"] == 1 and r.json()["over_limit"] == 1
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "rank_check"})).json()
    assert est["cost_usd"] == pytest.approx(0.006)

    r = await client.post("/keywords/ranks/check", headers=org["h"], json={})
    stats = await run_job(org, r.json()["job_id"])
    assert stats["checked"] == 3 and stats["source"] == "dataforseo" and stats["cost_usd"] == pytest.approx(0.006)
    assert stats["alerts"] == []  # first check: nothing to compare with
    o = (await client.get("/keywords/ranks", headers=org["h"])).json()
    rows = {x["keyword"]: x for x in o["rows"]}
    assert rows["hrms software"]["position"] == 5 and rows["hrms software"]["url"] == "https://kw-co.test/hrms"
    assert rows["hrms software"]["target_ranking"] is True
    assert rows["payroll software"]["position"] == 4 and rows["payroll software"]["gsc_position"] == 4.2
    assert rows["payroll software"]["target_ranking"] is False
    assert rows["leave policy template"]["position"] is None
    assert o["summary"]["top10"] == 2 and o["summary"]["not_ranking"] == 1 and o["summary"]["avg_position"] == 4.5
    # visibility = (0.07×5000 + 0.08×3000) / (0.28 × 8010) × 100
    assert o["summary"]["visibility"] == round(100 * (0.07 * 5000 + 0.08 * 3000) / (0.28 * 8010), 1)

    # running again the same day costs nothing (already checked)
    stats = await run_job(org, (await client.post("/keywords/ranks/check", headers=org["h"], json={})).json()["job_id"])
    assert stats["checked"] == 0 and stats["already"] == 3

    # next day: hrms moves into the top 3, payroll drops out of the top 10
    await _shift_checks_back(org["id"])
    STATE["serps"] = {**SERPS, "hrms software": ["kw-co.test/hrms", "a.test/1"],
                      "payroll software": [f"z{i}.test/x" for i in range(12)] + ["kw-co.test/payroll"]}
    stats = await run_job(org, (await client.post("/keywords/ranks/check", headers=org["h"], json={})).json()["job_id"])
    kinds = {a["keyword"]: a["kind"] for a in stats["alerts"]}
    assert kinds == {"hrms software": "top3", "payroll software": "drop"}
    event, title, body = STATE["notified"][0]
    assert event == "rank_change" and "1 up, 1 dropped" in title and "“payroll software” #4 → #13" in body
    o = (await client.get("/keywords/ranks", headers=org["h"])).json()
    rows = {x["keyword"]: x for x in o["rows"]}
    assert rows["hrms software"]["change"] == 4 and rows["hrms software"]["best_position"] == 1
    assert rows["payroll software"]["change"] == -9 and rows["payroll software"]["previous_position"] == 4
    assert [m["keyword"] for m in o["best_movers"]] == ["hrms software"]
    assert [m["keyword"] for m in o["worst_movers"]] == ["payroll software"]
    assert len(o["series"]) == 2 and o["series"][-1]["top10"] == 1 and o["series"][0]["avg_gsc_position"] == 4.2
    h = (await client.get(f"/keywords/ranks/{rows['hrms software']['id']}/history", headers=org["h"])).json()
    assert [p["position"] for p in h["points"]] == [5, 1] and h["serp_features"] == ["featured_snippet", "people_also_ask"]
    assert h["top_urls"][0] == "https://kw-co.test/hrms"
    csv_text = (await client.get("/keywords/ranks-export.csv", headers=org["h"])).text
    assert csv_text.splitlines()[0].startswith("keyword,position,previous,change")

    # the daily budget limits how many are checked
    await _shift_checks_back(org["id"])
    await client.put("/keywords/settings", headers=org["h"], json={"daily_budget_usd": 0.016})  # 0.012 spent today
    stats = await run_job(org, (await client.post("/keywords/ranks/check", headers=org["h"], json={})).json()["job_id"])
    assert stats["checked"] == 2 and stats["skipped_budget"] == 1
    assert any(t == "Some rankings weren't checked today" for _, t, _ in STATE["notified"])

    # pause and untrack
    kid = rows["leave policy template"]["id"]
    assert (await client.patch(f"/keywords/ranks/{kid}", headers=org["h"], json={"active": False})).json()["active"] is False
    assert (await client.delete(f"/keywords/ranks/{kid}", headers=org["h"])).status_code == 204
    async with tenant(org["id"]) as s:
        assert await s.scalar(select(RankCheck).where(RankCheck.tracked_keyword_id == uuid.UUID(kid))) is None


async def test_free_rank_tracking_with_web_search_and_search_console(client, admin):
    org = await new_org(client, provider=False)
    r = await client.post("/keywords/ranks", headers=org["h"], json={"keywords": ["payroll software", "hrms software", "leave app"]})
    assert r.json()["added"] == 3
    est = (await client.post("/keywords/estimate", headers=org["h"], json={"action": "rank_check"})).json()
    assert est["cost_usd"] == 0 and "approximate" in est["note"]
    await add_gsc(org["id"], [{"query": "Payroll Software", "clicks": 3, "impressions": 90, "ctr": 0.03, "position": 6.6},
                              {"query": "leave app", "clicks": 0, "impressions": 20, "ctr": 0, "position": 31.0}])
    stats = await run_job(org, (await client.post("/keywords/ranks/check", headers=org["h"], json={})).json()["job_id"])
    assert stats["checked"] == 3 and stats["source"] == "search" and stats["approximate"] and stats["cost_usd"] == 0
    o = (await client.get("/keywords/ranks", headers=org["h"])).json()
    assert o["source"] == "search" and o["approximate"] and o["depth"] == 20
    rows = {x["keyword"]: x for x in o["rows"]}
    assert rows["payroll software"]["position"] == 4 and rows["payroll software"]["gsc_position"] == 6.6  # side by side
    assert rows["hrms software"]["position"] == 5 and rows["payroll software"]["source"] == "search"
    assert rows["leave app"]["position"] is None and rows["leave app"]["gsc_position"] == 31.0
    assert not STATE.get("calls")

    # web search down: falls back to Search Console's average position
    await _shift_checks_back(org["id"])
    STATE["no_search"] = True
    stats = await run_job(org, (await client.post("/keywords/ranks/check", headers=org["h"], json={})).json()["job_id"])
    assert stats["checked"] == 3 and stats["errors"]
    rows = {x["keyword"]: x for x in (await client.get("/keywords/ranks", headers=org["h"])).json()["rows"]}
    assert rows["payroll software"]["position"] == 7 and rows["payroll software"]["source"] == "gsc"
    assert rows["hrms software"]["position"] is None
    assert {a["keyword"]: a["kind"] for a in stats["alerts"]} == {"hrms software": "drop"}

    # nothing available at all: a friendly note, nothing stored
    bare = await new_org(client, provider=False)
    await client.post("/keywords/ranks", headers=bare["h"], json={"keywords": ["hrms"]})
    stats = await run_job(bare, (await client.post("/keywords/ranks/check", headers=bare["h"], json={})).json()["job_id"])
    assert stats["checked"] == 0 and "Search Console" in stats["note"]


async def test_daily_rank_schedule(client, org):
    await client.post("/keywords/ranks", headers=org["h"], json={"keywords": ["hrms software"]})
    early = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)  # 05:30 IST, before the 06:00 default
    assert await kw_mod.schedule_org(org["id"], early) is False
    later = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)  # 06:30 IST
    assert await kw_mod.schedule_org(org["id"], later) is True
    assert await kw_mod.schedule_org(org["id"], later + timedelta(hours=2)) is False  # once a day
    async with tenant(org["id"]) as s:
        jobs = (await s.scalars(select(Job).where(Job.kind == "keywords.rank_check"))).all()
        assert len(jobs) == 1 and jobs[0].payload["date"] == "2026-10-17"
    await client.put("/keywords/settings", headers=org["h"], json={"rank_tracking_enabled": False})
    assert await kw_mod.schedule_org(org["id"], later + timedelta(days=1)) is False


# ------------------------------------------------------------------ permissions & isolation
async def test_permissions_and_tenant_isolation(client, org):
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=org["h"])).json()}
    email = f"viewer-{uuid.uuid4().hex[:6]}@kw-test.in"
    r = await client.post("/users", headers=org["h"], json={"email": email, "full_name": "Viewer", "role_ids": [roles["Viewer"]]})
    vh = auth(await login(client, email, r.json()["temporary_password"]))
    assert (await client.get("/keywords/overview", headers=vh)).status_code == 200
    assert (await client.get("/keywords/ranks", headers=vh)).status_code == 200
    for method, path, body in (("post", "/keywords/research", {"seeds": ["x"]}), ("post", "/keywords/gap", {"competitors": ["a.test"]}),
                               ("post", "/keywords/lists", {"name": "x"}), ("post", "/keywords/ranks", {"keywords": ["x"]}),
                               ("put", "/keywords/settings", {}), ("post", "/keywords/ranks/check", {}),
                               ("post", "/keywords/write-article", {"keyword": "abc"})):
        assert (await getattr(client, method)(path, headers=vh, json=body)).status_code == 403, path

    lid = (await client.post("/keywords/lists", headers=org["h"], json={"name": "Mine", "items": ITEMS[:2]})).json()["id"]
    await client.post("/keywords/ranks", headers=org["h"], json={"keywords": ["hrms software"]})
    r = await client.post("/keywords/research", headers=org["h"], json={"seeds": ["hrms software"]})
    await run_job(org, r.json()["job_id"])
    other = await new_org(client, name="Other KW")
    assert (await client.get("/keywords/lists", headers=other["h"])).json() == []
    assert (await client.get(f"/keywords/lists/{lid}", headers=other["h"])).status_code == 404
    assert (await client.get("/keywords/ranks", headers=other["h"])).json()["rows"] == []
    assert (await client.get("/keywords/reports", headers=other["h"])).json() == []
    assert (await client.get(f"/keywords/reports/{r.json()['report']['id']}", headers=other["h"])).status_code == 404
    async with tenant(other["id"]) as s:
        assert (await s.scalars(select(KeywordCache))).all() == []
        assert (await s.scalars(select(ProviderSpend))).all() == []
        assert (await s.scalars(select(TrackedKeyword))).all() == []
