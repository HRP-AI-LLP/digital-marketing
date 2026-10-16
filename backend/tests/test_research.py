"""Research & ideas: occasions calendar, research agenda/queries/dedupe/scoring, the full pipeline (resume, no
search, no AI), daily scheduling, occasion greeting posts, content integrations, brand suggestions, permissions
and tenant isolation.

Network and AI are mocked: web search (`search.search_all`) returns canned results, pages are served by an
httpx.MockTransport through the crawler's SSRF-safe client, DNS is stubbed, and the LLM router's `complete`
returns canned JSON chosen by the system prompt.
"""

import json
import re
import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

import app.modules.research as research_mod
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import (
    CompanyEntity,
    CompanyProfile,
    IdeaBrief,
    Opportunity,
    Organization,
    Post,
    ProviderConfig,
    ResearchRun,
    ResearchSource,
    SeoArticle,
    SocialAccount,
)
from app.services import brand_suggest, crawler, occasions, search
from app.services import content as content_svc
from app.services import research as svc
from app.services.jobs import run_inline
from app.services.llm import router as llm_router
from app.services.llm.router import LlmResult, NoProviderError
from app.services.tenancy import provision_organization
from tests.conftest import auth, login

PW = "Str0ngPassw0rd!"
REAL_SEARCH_ALL = search.search_all  # kept before the autouse fixture replaces it
IST = ZoneInfo("Asia/Kolkata")
STATE: dict = {}


# ------------------------------------------------------------------ fakes
NEWS = {
    "news1.test": {"/ai-agents-india": ("AI agents adoption doubles in India", "2026-10-10"),
                   "/payroll-survey": ("Payroll errors cost firms 2% of wages, survey finds", "2026-09-30")},
    "news2.test": {"/labour-codes": ("Labour codes: what employers must change in 2026", "2026-10-01")},
    "blocked-news.test": {"/story": ("Story behind a robots block", "2026-10-02")},
}


def _page(title: str, day: str) -> str:
    body = f"{title}. " + "Detailed reporting with numbers and quotes from HR leaders across India. " * 12
    return (f"<html><head><title>{title}</title><meta property='og:site_name' content='{title.split()[0]} Daily'>"
            f"<meta property='article:published_time' content='{day}T08:00:00Z'></head>"
            f"<body><article><h1>{title}</h1><p>{body}</p></article></body></html>")


def _handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    STATE.setdefault("fetched", []).append(f"{host}{path}")
    if path == "/robots.txt":
        if host == "blocked-news.test":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n", headers={"content-type": "text/plain"})
        return httpx.Response(404)
    if host in NEWS and path in NEWS[host]:
        t, d = NEWS[host][path]
        return httpx.Response(200, text=_page(t, d), headers={"content-type": "text/html; charset=utf-8"})
    return httpx.Response(404, headers={"content-type": "text/html"})


SEARCH_ROWS = [
    {"title": "AI agents adoption doubles", "url": "https://news1.test/ai-agents-india", "snippet": "AI agents"},
    {"title": "AI agents (tracking copy)", "url": "https://www.news1.test/ai-agents-india/?utm_source=x", "snippet": "dup"},
    {"title": "Payroll survey", "url": "https://news1.test/payroll-survey", "snippet": "Payroll errors"},
    {"title": "Labour codes", "url": "https://news2.test/labour-codes", "snippet": "Labour codes explained"},
    {"title": "Robots story", "url": "https://blocked-news.test/story", "snippet": "Snippet only: robots block"},
    {"title": "LinkedIn post", "url": "https://www.linkedin.com/posts/xyz", "snippet": "social"},
    {"title": "Our own site", "url": "https://research-co.test/blog", "snippet": "own"},
]


async def fake_search_all(session, query, n=10, *, freshness=None, max_providers=3):
    STATE.setdefault("queries", []).append((query, freshness))
    if STATE.get("no_search"):
        raise search.SearchError("Web search failed. DuckDuckGo: blocked")
    if "date India" in query:
        return [{"title": "Eid al-Fitr 2027 date", "url": "https://calendar.test/eid-2027", "snippet": "Eid on 10 March 2027",
                 "provider": "fake"}], []
    return [{**r, "provider": "fake"} for r in SEARCH_ROWS[:n]], []


def _payload(system: str, prompt: str):
    STATE.setdefault("systems", []).append(system[:30])
    if system == svc.QUERIES_SYSTEM:
        themes = re.findall(r"^- (\w+): ", prompt, re.M)
        rows = [{"theme": t, "q": f"{t} trends India 2026", "recency": "week"} for t in themes]
        rows += [{"theme": themes[0], "q": f"{themes[0]} trends India 2026"}, {"theme": "nonsense", "q": "ignored query"}]
        return {"queries": rows}
    if system == svc.FACTS_SYSTEM:
        ids = re.findall(r"^\[(S\d+)\] (.+?) — ", prompt, re.M)
        return {"facts": [{"source": sid, "fact": f"Reported: {title} with concrete numbers.", "date": "2026-10-05"}
                          for sid, title in ids] + [{"source": "S99", "fact": "no such source here at all"}]}
    if system == svc.IDEAS_SYSTEM:
        if STATE.get("fail_ideas"):
            STATE["fail_ideas"] -= 1
            raise RuntimeError("model crashed")
        return {"ideas": [
            {"title": "How AI agents cut payroll errors for Indian SMEs", "angle": "Numbers-led", "why_now": "New survey",
             "theme": "tech_ai", "fact_ids": ["F1", "F2"], "audience": "HR heads", "formats": ["linkedin", {"format": "blog", "note": "long"}],
             "article": {"keyword": "ai payroll software india", "secondary_keywords": ["payroll errors"], "outline": ["Why errors happen"]},
             "cta": {"text": "Book a demo", "url": "https://research-co.test/hrms"}, "hashtags": ["HRTech", "#AI"], "pillar": "Product"},
            {"title": "Labour codes checklist for employers in 2026", "angle": "Compliance", "theme": "policy", "fact_ids": ["F3"],
             "formats": [{"format": "instagram_carousel"}], "cta": {"url": "https://evil.test/x"}},
            {"title": "What the AI agents boom means for HR teams", "angle": "Opinion", "theme": "tech_ai", "fact_ids": ["F1"]},
            {"title": "Crypto payroll is the future", "angle": "Excluded topic", "theme": "market_news", "fact_ids": []},
            {"title": "", "theme": "tech_ai"},
            "not a dict",
        ]}
    if system == svc.OCCASIONS_SYSTEM:
        ids = re.findall(r"^- id (\S+):", prompt, re.M)
        return {"ideas": [{"occasion_id": i, "title": f"Warm wishes for {i.split(':')[0].replace('_', ' ')} from our team",
                           "angle": "Greeting", "formats": ["linkedin_post"], "greeting": "Wishing you joy"} for i in ids]}
    if system == svc.CRITIQUE_SYSTEM:
        n = len(re.findall(r"^\d+\. ", prompt, re.M))
        return {"scores": [{"i": i + 1, "relevance": 0.9 - i * 0.1, "timeliness": 0.8, "business_value": 0.8, "evidence": 0.7,
                            "reason": "Good fit"} for i in range(n)]}
    if system.startswith("From the search results"):
        return {"date": "2027-03-10", "source": "S1"}
    if system == brand_suggest.VOICE_SYSTEM:
        return {"voice": "Confident, practical and warm.", "audience": "HR heads at Indian SMEs.",
                "do_list": ["Lead with a number", "Lead with a number", "Use Indian examples"], "dont_list": ["Overpromise"],
                "banned_words": ["synergy"], "hashtags": ["#HRTech", "AIforHR"], "ctas": ["Book a demo at research-co.test"],
                "emoji_policy": "loads", "sample_posts": ["Payroll closes in hours, not days."],
                "notes": {"voice": "Matches the buyers"}}
    if system == brand_suggest.PILLARS_SYSTEM:
        return {"pillars": [{"name": "Product", "description": "Features", "weight": 30, "reason": "Core"},
                            {"name": "AI insights", "description": "Trends", "weight": 30},
                            {"name": "HR compliance", "weight": 20}, {"name": "product", "weight": 5}],
                "notes": "Balanced"}
    raise AssertionError(f"unexpected prompt: {system[:60]}")


async def fake_complete(session, task, prompt=None, *, messages=None, system=None, json_mode=False, temperature=0.7,
                        max_tokens=1500):
    if STATE.get("no_ai"):
        raise NoProviderError("No AI text provider is set up.")
    return LlmResult(json.dumps(_payload(system or "", prompt or "")), "fake", uuid.uuid4(), "fake-model")


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    async def public_dns(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(crawler, "TRANSPORT", httpx.MockTransport(_handler))
    monkeypatch.setattr(crawler, "resolve_host", public_dns)
    monkeypatch.setattr(llm_router, "complete", fake_complete)
    monkeypatch.setattr(search, "search_all", fake_search_all)
    STATE.clear()
    yield


# ------------------------------------------------------------------ helpers
async def new_org(client, *, with_ai=True, name="Research Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@research-test.in"
    async with platform_session() as s:
        org, user, _ = await provision_organization(s, name=f"{name} {uuid.uuid4().hex[:5]}", admin_email=email,
                                                    admin_name="Owner", admin_password=PW, timezone="Asia/Kolkata",
                                                    website="https://research-co.test")
        await s.commit()
        oid = org.id
    async with tenant(oid) as s:
        s.add(CompanyProfile(organization_id=oid, summary="Research Co builds an AI-powered HRMS with payroll for Indian companies.",
                             positioning="Compliant payroll with AI agents.", industries=["Manufacturing", "IT services"],
                             audiences=["HR heads"], differentiators=["AI agents for HR"], geography="India",
                             social_links={}, data={}))
        s.add_all([
            CompanyEntity(organization_id=oid, type="product", name="Research HRMS", description="Payroll and attendance",
                          url="https://research-co.test/hrms", confirmed=True, data={}),
            CompanyEntity(organization_id=oid, type="studio", name="AI Studio", description="Custom AI agents", confirmed=True, data={}),
            CompanyEntity(organization_id=oid, type="use_case", name="Payroll compliance", confirmed=True, data={}),
        ])
        if with_ai:
            s.add(ProviderConfig(organization_id=oid, kind="llm", provider="ollama", name="Local", settings={"default_model": "m"}))
        await s.commit()
    tokens = await login(client, email, PW)
    return {"id": oid, "h": auth(tokens), "tokens": tokens}


class tenant:
    def __init__(self, org_id):
        self.s = SessionLocal()
        set_tenant(self.s, org_id)

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        await self.s.close()


async def run_research(client, org) -> dict:
    r = await client.post("/research/runs", headers=org["h"], json={"depth": "standard"})
    assert r.status_code == 202, r.text
    run_id = r.json()["run"]["id"]
    try:
        await run_inline("research.run", org["id"], {"run_id": run_id})
    except Exception as e:  # noqa: BLE001 - tests inspect the run row
        STATE["run_error"] = e
    return (await client.get(f"/research/runs/{run_id}", headers=org["h"])).json()


@pytest.fixture
async def org(client, admin):
    return await new_org(client)


# ------------------------------------------------------------------ occasions calendar
async def test_variable_festival_dates_come_from_the_data_file():
    def get(key, year):
        return occasions.find(key, year)

    assert get("diwali", 2026).date == date(2026, 11, 8) and get("diwali", 2026).confidence == "official"
    assert get("diwali", 2027).date == date(2027, 10, 29)
    assert get("holi", 2026).date == date(2026, 3, 4)  # not Holika Dahan (3 Mar)
    assert get("holi", 2027).date == date(2027, 3, 22)
    assert get("dussehra", 2026).date == date(2026, 10, 20)
    assert get("ganesh_chaturthi", 2027).date == date(2027, 9, 4)
    assert get("onam", 2026).date == date(2026, 8, 26)
    eid = get("eid_al_fitr", 2027)
    assert eid.date == date(2027, 3, 10) and eid.confidence == "estimated"
    assert get("navratri", 2026).date == date(2026, 10, 11) and get("navratri", 2026).confidence == "derived"
    assert get("mothers_day", 2026).date == date(2026, 5, 10) and get("mothers_day", 2026).confidence == "rule"
    assert get("republic_day", 2031).date == date(2031, 1, 26)  # fixed dates work for any year
    wedding = get("wedding_season", 2026)
    assert wedding.end_date == date(2027, 2, 15) and wedding.category == "season"
    assert get("diwali", 2035) is None  # beyond the data: left out, never guessed
    d = get("diwali", 2026).to_dict()
    assert d["id"] == "diwali:2026" and d["category_label"] == "Festivals" and d["tone_guidance"]


async def test_lead_time_and_settings_filtering():
    today = date(2026, 10, 16)
    near = {o.key for o in occasions.upcoming(today, {"lead_days": 10})}
    assert {"durga_puja", "dussehra"} <= near and "diwali" not in near
    assert "diwali" in {o.key for o in occasions.upcoming(today, {"lead_days": 25})}
    no_regional = {o.key for o in occasions.upcoming(today, {"lead_days": 10, "categories": ["festival", "national"]})}
    assert "dussehra" in no_regional and "durga_puja" not in no_regional
    kerala = {o.key for o in occasions.upcoming(today, {"lead_days": 30, "regions": ["KL"]})}
    assert "durga_puja" not in kerala and "diwali" in kerala and "kannada_rajyotsava" not in kerala
    assert occasions.upcoming(today, {"enabled": False}) == []
    # a web-verified date overrides the calendar and is labelled
    v = occasions.find("eid_al_fitr", 2027, {"eid_al_fitr:2027": {"date": "2027-03-11", "source_url": "https://x.test/eid"}})
    assert v.date == date(2027, 3, 11) and v.confidence == "verified" and "2027-03-10" in v.note


# ------------------------------------------------------------------ agenda, queries, dedupe, scoring (pure)
def _ctx() -> svc.Context:
    return svc.Context(org_name="Research Co", website="https://research-co.test/", facts="Research Co builds HRMS",
                       products=["Research HRMS"], studios=["AI Studio"], use_cases=["Payroll compliance"],
                       industries=["Manufacturing"], geography="India", own_domains={"research-co.test"},
                       pillars=[{"id": str(uuid.uuid4()), "name": "Product"}])


async def test_agenda_uses_company_knowledge_settings_and_rotates():
    cfg = svc.effective_config(None)
    cfg["themes"]["policy"] = False
    cfg["custom_themes"] = [{"label": "Gig workers", "focus": "gig economy payroll"}]
    cfg["keywords"] = ["HRMS"]
    agenda = svc.build_agenda(_ctx(), cfg, svc.DEPTHS["deep"], date(2026, 10, 16), occasions.upcoming(date(2026, 10, 16), None))
    keys = [a.theme for a in agenda]
    assert "policy" not in keys and "custom_1" in keys and "company" in keys
    company = next(a for a in agenda if a.theme == "company")
    assert "Research HRMS" in company.focus and company.keywords == ["HRMS"]
    events = next(a for a in agenda if a.theme == "events")
    assert "Dussehra" in events.focus
    quick = svc.DEPTHS["quick"]
    day1 = [a.theme for a in svc.build_agenda(_ctx(), cfg, quick, date(2026, 10, 16))]
    day2 = [a.theme for a in svc.build_agenda(_ctx(), cfg, quick, date(2026, 10, 17))]
    assert len(day1) == quick.themes and day1 != day2  # themes rotate across days


async def test_query_planning_normalizes_dedupes_and_falls_back():
    agenda = [svc.AgendaItem("tech_ai", "Tech", "AI agents, RAG", [], "week"),
              svc.AgendaItem("policy", "Policy", "labour codes, DPDP Act", [], "month")]
    raw = {"queries": [{"theme": "tech_ai", "q": "AI agents India 2026", "recency": "day"},
                       {"theme": "tech_ai", "q": "AI  agents India 2026!", "recency": "any"},
                       {"theme": "tech_ai", "q": "RAG chatbots enterprise", "recency": "bogus"},
                       {"theme": "tech_ai", "q": "third query cut by the cap"},
                       {"theme": "unknown", "q": "dropped"}, "plain string query"]}
    qs = svc.normalize_queries(raw, agenda, per_theme=2, year=2026)
    tech = [q for q in qs if q.theme == "tech_ai"]
    assert [q.q for q in tech] == ["AI agents India 2026", "RAG chatbots enterprise"]
    assert tech[0].recency == "day" and tech[1].recency == "week"  # invalid recency → theme default
    policy = [q for q in qs if q.theme == "policy"]
    assert policy and all("2026" in q.q for q in policy)  # model skipped it: fallback queries


async def test_url_dedupe_blocking_and_ranking():
    assert svc.url_key("https://www.News1.test/a/?utm_source=x#top") == svc.url_key("http://news1.test/a")
    cfg = {"blocked_domains": ["spam.test"], "trusted_domains": ["gov.in"]}
    own = {"research-co.test"}
    assert svc.is_blocked("https://sub.spam.test/x", cfg, own)
    assert svc.is_blocked("https://www.linkedin.com/posts/1", cfg, own)
    assert svc.is_blocked("https://research-co.test/blog", cfg, own)
    assert not svc.is_blocked("https://labour.gov.in/codes", cfg, own)
    hits = {
        "a.test/1": {"key": "a.test/1", "url": "https://a.test/1", "rank": 0, "queries": ["q1", "q2"]},
        "b.test/1": {"key": "b.test/1", "url": "https://b.test/1", "rank": 0, "queries": ["q1", "q2"]},
        "labour.gov.in/c": {"key": "labour.gov.in/c", "url": "https://labour.gov.in/c", "rank": 5, "queries": ["q3"]},
    }
    ranked = [h["key"] for h in svc.rank_hits(hits, cfg, seen_keys={"a.test/1"})]
    assert ranked[0] == "b.test/1" and ranked.index("a.test/1") > ranked.index("b.test/1")


async def test_scoring_selection_and_output_normalization():
    today = date(2026, 10, 16)
    facts = [{"source_url": f"https://s{i}.test", "date": "2026-10-10"} for i in range(3)]
    assert svc.evidence_score(facts, today) == 1.0
    assert svc.evidence_score([{"source_url": "https://a.test", "date": "2025-01-01"}], today) < 0.5
    assert svc.combine({"relevance": 1, "timeliness": 1, "novelty": 1, "business_value": 1, "evidence": 1}) == 1.0
    ideas = [{"title": f"AI payroll idea number {i}", "theme": "tech_ai", "score": 0.9 - i / 100} for i in range(5)]
    ideas.append({"title": "Labour codes explained for employers", "theme": "policy", "score": 0.5})
    top = svc.select_top(ideas, 4, per_theme=3)
    assert len(top) == 2  # near-duplicate titles collapse; theme cap respected
    assert {i["theme"] for i in top} == {"tech_ai", "policy"}
    fm = svc.normalize_formats(["linkedin", {"format": "reel", "note": "30s"}, "blog", "unknown", "linkedin_post"])
    assert [f["format"] for f in fm] == ["linkedin_post", "short_video", "seo_article"]
    assert svc.excluded({"title": "Crypto payroll", "angle": ""}, ["crypto"])
    nov, _, _ = await svc.novelty_scores(None, ["AI payroll for SMEs"], ["AI payroll for SMEs"], [])
    assert nov == [0.0]


# ------------------------------------------------------------------ the pipeline
async def test_full_research_run_produces_grounded_briefs(client, org):
    # observe occasions a month ahead so occasion briefs are part of the run
    st = (await client.get("/research/settings", headers=org["h"])).json()["settings"]
    st["occasions"]["lead_days"] = 30
    st["excluded_topics"] = ["crypto"]
    st["blocked_domains"] = ["news2.test"]
    r = await client.put("/research/settings", headers=org["h"], json=st)
    assert r.status_code == 200, r.text

    run = await run_research(client, org)
    assert run["status"] == "done", (run, STATE.get("run_error"))
    stages = [s["stage"] for s in run["steps"]]
    for st_ in ("agenda", "queries", "search", "read", "facts", "ideas", "critique", "save", "done"):
        assert st_ in stages, st_
    assert run["stats"]["llm_calls"] >= 4 and run["stats"]["pages_read"] >= 2
    assert all(f in {"week", "month", "year", None, "day"} for _, f in STATE["queries"])

    briefs = (await client.get("/research/briefs", headers=org["h"])).json()
    regular = [b for b in briefs if not b["occasion"]]
    titles = [b["title"] for b in regular]
    assert "How AI agents cut payroll errors for Indian SMEs" in titles
    assert not any("Crypto" in t for t in titles)  # excluded topic
    b = next(b for b in regular if b["title"].startswith("How AI agents"))
    assert {"fact", "source_url", "publisher", "date"} <= set(b["facts"][0])
    assert all(f["source_url"].startswith("https://news1.test/") for f in b["facts"])  # blocked + dedupe
    assert [f["format"] for f in b["formats"]] == ["linkedin_post", "seo_article"]
    assert b["article"]["keyword"] == "ai payroll software india" and b["hashtags"] == ["#HRTech", "#AI"]
    assert b["cta"]["url"] == "https://research-co.test/hrms"
    assert set(b["scores"]) == {"relevance", "timeliness", "novelty", "business_value", "evidence"} and 0 < b["score"] <= 1
    lab = next(x for x in briefs if x["title"].startswith("Labour codes"))
    assert lab["cta"]["url"] == "https://research-co.test/"  # off-site CTA replaced by the website
    assert not any("news2.test" in (f["source_url"] or "") for b_ in briefs for f in b_["facts"])  # blocked domain

    async with tenant(org["id"]) as s:
        sources = (await s.scalars(select(ResearchSource))).all()
        keys = [x.url_key for x in sources]
        assert len(keys) == len(set(keys))  # tracking-param duplicate merged
        robots = next(x for x in sources if "blocked-news" in x.url)
        assert robots.fetched is False and robots.excerpt.startswith("Snippet only")  # robots.txt respected
        assert not any("linkedin.com" in x.url or "research-co.test" in x.url for x in sources)
        # high scorers go to the planner's topics, with the grounded brief attached
        opps = (await s.scalars(select(Opportunity).where(Opportunity.source == "trend"))).all()
        assert opps and all("Facts to use" in (o.data or {}).get("brief", "") for o in opps if o.data.get("brief"))
    assert "blocked-news.test/story" not in STATE["fetched"]

    expected = occasions.upcoming(date.fromisoformat(run["run_date"]), {"lead_days": 30})
    occ_briefs = [x for x in briefs if x["occasion"]]
    if expected:
        assert occ_briefs and occ_briefs[0]["occasion"]["id"] in {o.id for o in expected}
        assert occ_briefs[0]["theme"] == "events" and occ_briefs[0]["greeting"] == "Wishing you joy"
        # a second run never repeats an occasion (each run covers up to four new ones)
        await run_research(client, org)
        again = [x["occasion"]["id"] for x in (await client.get("/research/briefs", headers=org["h"])).json() if x["occasion"]]
        assert len(again) == len(set(again)) and set(again) >= {x["occasion"]["id"] for x in occ_briefs}

    ov = (await client.get("/research/overview", headers=org["h"])).json()
    assert ov["counts"]["today"] >= 3 and ov["latest_run"]["status"] == "done" and ov["providers"]["ai"] is True
    days = (await client.get("/research/briefs/days", headers=org["h"])).json()
    assert days[0]["count"] >= 3
    only_carousels = (await client.get("/research/briefs?format=instagram_carousel", headers=org["h"])).json()
    assert [x["title"] for x in only_carousels] == ["Labour codes checklist for employers in 2026"]
    assert (await client.get("/research/briefs?theme=policy", headers=org["h"])).json()[0]["theme"] == "policy"


async def test_run_resumes_after_a_failure(client, org):
    STATE["fail_ideas"] = 1
    run = await run_research(client, org)
    assert run["status"] == "queued" and "Retrying" in run["steps"][-1]["message"]
    planned = STATE["systems"].count(svc.QUERIES_SYSTEM[:30])
    async with tenant(org["id"]) as s:
        r = await s.get(ResearchRun, uuid.UUID(run["id"]))
        assert r.data.get("facts") and "candidates" not in r.data
    await run_inline("research.run", org["id"], {"run_id": run["id"]})
    done = (await client.get(f"/research/runs/{run['id']}", headers=org["h"])).json()
    assert done["status"] == "done"
    assert STATE["systems"].count(svc.QUERIES_SYSTEM[:30]) == planned  # searches were not planned again
    assert any(s["message"].startswith("Resuming") for s in done["steps"])


async def test_run_without_web_search_is_partial(client, org):
    STATE["no_search"] = True
    run = await run_research(client, org)
    assert run["status"] == "partial"
    assert any(s["level"] == "warn" and "Brave Search or Tavily" in s["message"] for s in run["steps"])
    assert (await client.get("/research/briefs", headers=org["h"])).json()


async def test_no_ai_model(client, admin):
    o = await new_org(client, with_ai=False)
    r = await client.post("/research/runs", headers=o["h"], json={})
    assert r.status_code == 400 and "AI text model" in r.json()["detail"]
    o2 = await new_org(client)
    STATE["no_ai"] = True
    run = await run_research(client, o2)
    assert run["status"] == "failed" and "AI text model" in run["error"]


async def test_run_now_returns_the_active_run(client, org):
    first = (await client.post("/research/runs", headers=org["h"], json={})).json()
    second = (await client.post("/research/runs", headers=org["h"], json={"depth": "deep"})).json()
    assert second["already_running"] and second["run"]["id"] == first["run"]["id"]


async def test_occasion_dates_are_verified_online(client, org):
    async with tenant(org["id"]) as s:
        row = await svc.get_settings_row(s, org["id"], create=True)
        run = ResearchRun(organization_id=org["id"], run_date="2027-02-20", trigger="manual", depth="quick", steps=[],
                          stats={}, data={})
        s.add(run)
        await s.flush()
        eid = occasions.find("eid_al_fitr", 2027)
        n = await svc.verify_occasions(s, row, [eid], svc.RunLog(s, run), limit=5)
        await s.commit()
        assert n == 1
        v = svc.verified_dates(row)["eid_al_fitr:2027"]
        assert v["date"] == "2027-03-10" and v["source_url"] == "https://calendar.test/eid-2027"
        assert occasions.find("eid_al_fitr", 2027, svc.verified_dates(row)).confidence == "verified"


# ------------------------------------------------------------------ scheduling
async def test_daily_run_starts_once_at_local_time(client, org):
    def ist(y, m, d, h, mi):
        return datetime(y, m, d, h, mi, tzinfo=IST).astimezone(UTC)

    assert await research_mod.schedule_org(org["id"], ist(2030, 1, 5, 6, 59)) is None
    run = await research_mod.schedule_org(org["id"], ist(2030, 1, 5, 7, 1))
    assert run is not None and run.run_date == "2030-01-05" and run.trigger == "scheduled"
    assert await research_mod.schedule_org(org["id"], ist(2030, 1, 5, 18, 0)) is None  # once per day
    nxt = await research_mod.schedule_org(org["id"], ist(2030, 1, 6, 7, 5))
    assert nxt is not None and nxt.run_date == "2030-01-06"
    # 07:00 IST is 01:30 UTC: the local date is what counts
    assert (await research_mod.schedule_org(org["id"], datetime(2030, 1, 7, 1, 31, tzinfo=UTC))).run_date == "2030-01-07"

    st = (await client.get("/research/settings", headers=org["h"])).json()["settings"]
    st.update(enabled=False)
    assert (await client.put("/research/settings", headers=org["h"], json=st)).status_code == 200
    assert await research_mod.schedule_org(org["id"], ist(2030, 1, 8, 9, 0)) is None
    st.update(enabled=True, run_time="06:30")
    await client.put("/research/settings", headers=org["h"], json=st)
    assert (await research_mod.schedule_org(org["id"], ist(2030, 1, 8, 6, 31))).run_date == "2030-01-08"
    bad = await client.put("/research/settings", headers=org["h"], json={**st, "run_time": "25:00"})
    assert bad.status_code == 422


async def test_greeting_posts_are_planned_once_per_occasion(client, org):
    async with tenant(org["id"]) as s:
        acc = SocialAccount(id=uuid.uuid4(), organization_id=org["id"], platform="linkedin_org", name="Page", status="connected",
                            settings={})
        s.add(acc)
        await s.commit()
    st = (await client.get("/research/settings", headers=org["h"])).json()["settings"]
    st["occasions"]["greeting_posts"] = {"enabled": True, "account_ids": [str(acc.id)], "time": "09:00",
                                         "mode": "approve_first", "post_type": "image", "categories": ["festival", "national"]}
    r = await client.put("/research/settings", headers=org["h"], json=st)
    assert r.status_code == 200, r.text

    at = datetime(2026, 11, 7, 6, 0, tzinfo=IST).astimezone(UTC)  # 27 h before Diwali 09:00
    made = await research_mod.schedule_greetings(org["id"], at)
    assert [p.meta["occasion"]["key"] for p in made] == ["diwali"]
    assert await research_mod.schedule_greetings(org["id"], at) == []  # once per occasion
    async with tenant(org["id"]) as s:
        post = (await s.scalars(select(Post).where(Post.social_account_id == acc.id))).one()
        assert post.post_type == "image" and post.status == "planned" and post.mode == "approve_first"
        assert post.scheduled_at == datetime(2026, 11, 8, 9, 0, tzinfo=IST)
        assert post.meta["occasion"]["category"] == "festival" and "Diwali" in post.meta["brief"]
    # regional occasions are not greeted when only festivals/national days are chosen
    assert await research_mod.schedule_greetings(org["id"], datetime(2026, 10, 17, 9, 0, tzinfo=IST)) == []

    occ = (await client.get("/research/occasions?from=2026-11-01&to=2026-11-30", headers=org["h"])).json()
    diwali = next(x for x in occ["items"] if x["key"] == "diwali")
    assert diwali["greeting_planned"] and diwali["observed"] and diwali["confidence"] == "official"


# ------------------------------------------------------------------ content integrations
async def _one_brief(client, org) -> dict:
    await run_research(client, org)
    return next(b for b in (await client.get("/research/briefs", headers=org["h"])).json()
                if b["title"].startswith("How AI agents"))


async def test_create_post_and_article_from_brief(client, org):
    b = await _one_brief(client, org)
    async with tenant(org["id"]) as s:
        acc = SocialAccount(id=uuid.uuid4(), organization_id=org["id"], platform="linkedin_org", name="Page", status="connected",
                            settings={})
        s.add(acc)
        await s.commit()
    pre = (await client.get(f"/research/briefs/{b['id']}/composer", headers=org["h"])).json()
    assert "Facts to use" in pre["brief"] and "https://news1.test/" in pre["brief"] and pre["link_url"]

    r = await client.post(f"/research/briefs/{b['id']}/compose", headers=org["h"],
                          json={"account_ids": [str(acc.id)], "post_type": "text", "then": "review"})
    assert r.status_code == 200, r.text
    assert r.json()["brief"]["status"] == "used" and r.json()["brief"]["used"][-1]["kind"] == "post"
    async with tenant(org["id"]) as s:
        post = await s.get(Post, uuid.UUID(r.json()["posts"][0]["post_id"]))
        assert post.topic == b["title"] and post.meta["research_brief_id"] == b["id"]
        assert "https://news1.test/" in post.meta["brief"] and post.mode == "approve_first"

    r = await client.post(f"/research/briefs/{b['id']}/article", headers=org["h"], json={})
    assert r.status_code == 201, r.text
    async with tenant(org["id"]) as s:
        art = await s.get(SeoArticle, uuid.UUID(r.json()["article"]["id"]))
        assert art.keyword == "ai payroll software india" and art.secondary_keywords == ["payroll errors"]
        from app.services.seo import article_meta

        assert "news1.test" in (article_meta(art).get("notes") or "")
    assert r.json()["brief"]["used"][-1]["kind"] == "article"


async def test_topics_feed_the_planner_with_grounded_brief(client, org):
    b = await _one_brief(client, org)
    async with tenant(org["id"]) as s:  # clear auto-added topics so ours is the one picked
        for o in (await s.scalars(select(Opportunity))).all():
            o.status = "dismissed"
        await s.commit()
    r = await client.post(f"/research/briefs/{b['id']}/topic", headers=org["h"])
    assert r.status_code == 200 and r.json()["brief"]["opportunity_id"]
    async with tenant(org["id"]) as s:
        acc = SocialAccount(id=uuid.uuid4(), organization_id=org["id"], platform="linkedin_org", name="Page",
                            status="connected", settings={})
        s.add(acc)
        await s.flush()
        post = Post(organization_id=org["id"], social_account_id=acc.id, platform="linkedin_org", status="planned",
                    mode="manual", post_type="text", hashtags=[], media_ids=[], qa={}, meta={})
        s.add(post)
        await s.flush()
        await content_svc.plan_post(s, post, None, content_svc.GenSettings(), IST)
        assert post.topic == b["title"] and "Facts to use" in post.meta["brief"]
        assert post.meta["research_brief_id"] == b["id"]
        await s.commit()
    ov = await client.get("/research/overview", headers=org["h"])  # syncs "used" from the planner
    assert ov.status_code == 200
    assert (await client.get(f"/research/briefs/{b['id']}", headers=org["h"])).json()["status"] == "used"


async def test_save_and_dismiss(client, org):
    b = await _one_brief(client, org)
    assert (await client.patch(f"/research/briefs/{b['id']}", headers=org["h"], json={"status": "saved"})).json()["status"] == "saved"
    await client.patch(f"/research/briefs/{b['id']}", headers=org["h"], json={"status": "dismissed"})
    assert b["id"] not in [x["id"] for x in (await client.get("/research/briefs", headers=org["h"])).json()]
    assert b["id"] in [x["id"] for x in (await client.get("/research/briefs?status=dismissed", headers=org["h"])).json()]


# ------------------------------------------------------------------ brand suggestions
async def test_brand_voice_and_pillar_suggestions(client, org):
    r = await client.post("/brand-ai/suggest", headers=org["h"], json={"kind": "voice"})
    assert r.status_code == 202
    res = await run_inline("brand.suggest", org["id"], {"kind": "voice"})
    assert res["current"]["voice"] is None
    sug = res["suggested"]
    assert sug["voice"].startswith("Confident") and sug["do_list"] == ["Lead with a number", "Use Indian examples"]
    assert sug["hashtags"] == ["HRTech", "AIforHR"] and sug["emoji_policy"] == "light"  # invalid value → current
    assert res["samples"] and res["notes"]["voice"]
    async with tenant(org["id"]) as s:  # nothing is saved by suggesting
        from app.models import BrandProfile

        assert (await s.scalar(select(BrandProfile))) is None

    await client.post("/content/pillars", headers=org["h"], json={"name": "Product", "weight": 50})
    res = await run_inline("brand.suggest", org["id"], {"kind": "pillars"})
    names = [p["name"] for p in res["suggested"]]
    assert names == ["Product", "AI insights", "HR compliance"]  # duplicate dropped
    assert res["suggested"][0]["existing_id"] and sum(p["weight"] for p in res["suggested"]) == 100


# ------------------------------------------------------------------ permissions and tenancy
async def test_permissions(client, org):
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=org["h"])).json()}
    email = f"viewer-{uuid.uuid4().hex[:6]}@research-test.in"
    r = await client.post("/users", headers=org["h"], json={"email": email, "full_name": "Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    v = await login(client, email, r.json()["temporary_password"])
    vh = auth(v)
    assert (await client.get("/research/overview", headers=vh)).status_code == 200
    assert (await client.get("/research/occasions", headers=vh)).status_code == 200
    for method, path, body in (("post", "/research/runs", {}), ("put", "/research/settings", {}),
                               ("post", "/brand-ai/suggest", {"kind": "voice"})):
        res = await getattr(client, method)(path, headers=vh, json=body)
        assert res.status_code == 403, path
    bid = uuid.uuid4()
    assert (await client.patch(f"/research/briefs/{bid}", headers=vh, json={"status": "saved"})).status_code == 403
    assert (await client.post(f"/research/briefs/{bid}/compose", headers=vh, json={"account_ids": [str(bid)]})).status_code == 403
    assert (await client.post(f"/research/briefs/{bid}/article", headers=vh, json={})).status_code == 403


async def test_tenant_isolation(client, org):
    b = await _one_brief(client, org)
    other = await new_org(client, name="Other Co")
    assert (await client.get("/research/briefs", headers=other["h"])).json() == []
    assert (await client.get(f"/research/briefs/{b['id']}", headers=other["h"])).status_code == 404
    assert (await client.patch(f"/research/briefs/{b['id']}", headers=other["h"], json={"status": "dismissed"})).status_code == 404
    assert (await client.get(f"/research/runs/{b['run_id']}", headers=other["h"])).status_code == 404
    assert (await client.get("/research/overview", headers=other["h"])).json()["latest_run"] is None
    async with tenant(other["id"]) as s:
        assert (await s.scalars(select(IdeaBrief))).all() == []
        assert (await s.scalars(select(ResearchSource))).all() == []


# ------------------------------------------------------------------ multi-provider search
async def test_search_all_merges_providers(monkeypatch):
    calls = []

    async def providers(session):
        return [("Brave", "brave", None, {}), ("Tavily", "tavily", None, {}), ("Broken", "serper", None, {})]

    async def run(provider, base, secrets, query, n, freshness=None):
        calls.append((provider, freshness))
        if provider == "serper":
            raise RuntimeError("bad key")
        return [{"title": provider, "url": "https://same.test/a", "snippet": ""},
                {"title": provider, "url": f"https://{provider}.test/b", "snippet": ""}]

    monkeypatch.setattr(search, "_providers", providers)
    monkeypatch.setattr(search, "_run", run)
    rows, errors = await REAL_SEARCH_ALL(None, "ai agents", 5, freshness="week")
    assert [r["url"] for r in rows] == ["https://same.test/a", "https://brave.test/b", "https://tavily.test/b"]
    assert rows[0]["provider"] == "Brave" and errors and "bad key" in errors[0]
    assert all(f == "week" for _, f in calls)


async def test_deep_run_follows_up_on_top_ideas(client, org):
    r = await client.post("/research/runs", headers=org["h"], json={"depth": "deep"})
    run_id = r.json()["run"]["id"]
    await run_inline("research.run", org["id"], {"run_id": run_id})
    run = (await client.get(f"/research/runs/{run_id}", headers=org["h"])).json()
    assert run["status"] == "done" and run["depth"] == "deep"
    assert any(s["stage"] == "follow_up" for s in run["steps"])
    follow_queries = [q for q, f in STATE["queries"] if f == "year" and "statistics" in q]
    assert follow_queries  # second-round searches on the most promising ideas
