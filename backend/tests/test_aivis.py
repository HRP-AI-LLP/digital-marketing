"""AI search visibility: brand matching and analysis rules, engine adapters (Perplexity, OpenAI web search, Gemini
grounding, Claude web search, plain LLM), the full run through the API (metrics, missed prompts, recommendations,
AI usage, drop alerts), prompts CRUD and suggestions, settings, the weekly schedule, llms.txt generation and the
live-site check, permissions and tenant isolation.

No network: engines are served by an httpx.MockTransport on aivis_engines.TRANSPORT, website fetches by one on
crawler.TRANSPORT (DNS stubbed), and the LLM router's `complete` returns canned JSON chosen by the system prompt.
"""

import json
import re
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select

import app.modules.aivis as aivis_mod
from app.core.crypto import encrypt_json
from app.core.db import SessionLocal, set_tenant
from app.models import (
    AivisAnswer,
    AivisPrompt,
    AivisRun,
    CompanyProfile,
    Competitor,
    KnowledgeDocument,
    KnowledgeSource,
    LlmUsage,
    Notification,
    ProviderConfig,
)
from app.services import aivis as svc
from app.services import aivis_engines as eng
from app.services import crawler, llms_txt, search
from app.services.jobs import run_inline
from app.services.llm import router as llm_router
from app.services.llm.router import LlmResult, NoProviderError
from app.services.tenancy import provision_organization
from tests.conftest import auth, login

PW = "Str0ngPassw0rd!"
STATE: dict = {}

P1 = "best HRMS software for mid-size companies in India"
P2 = "payroll software with compliance for Indian factories"

ANSWERS = {
    ("perplexity", P1): "Top options:\n1. **Zoho People** – popular and affordable.\n2. **VisHR** – excellent AI payroll and a "
                        "strong choice for mid-size firms.\n3. **Keka** – good UI.",
    ("openai", P1): "Some leading choices are Keka, Darwinbox and VisHR (visco.test).",
    ("gemini", P1): "Keka and Darwinbox are the most common picks for mid-size Indian companies.",
    ("anthropic", P1): "VisHR is often mentioned, but it is expensive and limited for large teams. Keka is cheaper.",
    ("groq", P1): "Popular: Keka and greytHR. See https://www.capterra.in/directory/hrms for reviews.",
    ("perplexity", P2): "1. Keka Payroll\n2. Darwinbox\n3. greytHR",
    ("openai", P2): "Keka and greytHR handle PF and ESI well.",
    ("gemini", P2): "Darwinbox covers factory compliance.",
    ("anthropic", P2): "Consider Keka or Darwinbox.",
    ("groq", P2): "Keka is a solid option.",
}
KNOWN = ["Zoho People", "VisHR", "Keka", "Darwinbox", "greytHR"]


def _prompt_of(text: str) -> str:
    return P1 if "mid-size" in text else P2


def _engine_handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    body = json.loads(request.content or b"{}")
    STATE.setdefault("engine_calls", []).append((host, path, body))
    if STATE.get("engines_down"):
        return httpx.Response(500, json={"error": {"message": "server exploded"}})
    if host == "api.perplexity.ai":
        q = body["messages"][-1]["content"]
        text = ANSWERS[("perplexity", _prompt_of(q))]
        if STATE.get("perplexity_without_us"):
            text = text.replace("VisHR", "Keka Lite")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 20, "completion_tokens": 80},
            "citations": ["https://www.g2.com/categories/hrms", "https://visco.test/hrms"],
            "search_results": [{"title": "G2 HRMS", "url": "https://www.g2.com/categories/hrms"}],
        })
    if host == "api.openai.com" and path == "/v1/responses":
        assert body["tools"] == [{"type": "web_search"}] and body["max_output_tokens"] <= 800
        text = ANSWERS[("openai", _prompt_of(body["input"]))]
        return httpx.Response(200, json={
            "output": [{"type": "web_search_call", "status": "completed"},
                       {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": [
                           {"type": "url_citation", "url": "https://www.g2.com/compare/keka-vs-vishr", "title": "G2"}]}]}],
            "usage": {"input_tokens": 30, "output_tokens": 60},
        })
    if host == "generativelanguage.googleapis.com":
        assert path.endswith("/v1beta/models/gemini-2.5-flash:generateContent") and body["tools"] == [{"google_search": {}}]
        assert request.headers["x-goog-api-key"] == "gem-key"
        text = ANSWERS[("gemini", _prompt_of(body["contents"][0]["parts"][0]["text"]))]
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": text}]}, "groundingMetadata": {
                "webSearchQueries": ["hrms india"],
                "groundingChunks": [{"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                                             "title": "capterra.com"}}]}}],
            "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 40},
        })
    if host == "api.anthropic.com":
        assert body["tools"][0]["type"] == "web_search_20250305"
        text = ANSWERS[("anthropic", _prompt_of(body["messages"][0]["content"]))]
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "I'll search for that."},
                        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "hrms"}},
                        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                         "content": [{"type": "web_search_result", "url": "https://www.reddit.com/r/india/hrms", "title": "Reddit"}]},
                        {"type": "text", "text": text, "citations": [
                            {"type": "web_search_result_location", "url": "https://www.reddit.com/r/india/hrms", "title": "Reddit",
                             "cited_text": "..."}]}],
            "usage": {"input_tokens": 50, "output_tokens": 70, "server_tool_use": {"web_search_requests": 1}},
        })
    if host == "api.groq.com":
        assert "web_search_options" not in body
        text = ANSWERS[("groq", _prompt_of(body["messages"][-1]["content"]))]
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 30}})
    return httpx.Response(404, json={"error": "unknown"})


SITE = {
    "/llms.txt": (404, "text/html", "<html>not found</html>"),
    "/llms-full.txt": (404, "text/html", "<html>nope</html>"),
    "/robots.txt": (200, "text/plain", "User-agent: GPTBot\nDisallow: /\n\nUser-agent: CCBot\nDisallow: /\n\n"
                                       "User-agent: *\nDisallow: /admin\n"),
}


def _site_handler(request: httpx.Request) -> httpx.Response:
    status, ct, text = SITE.get(request.url.path, (404, "text/html", ""))
    return httpx.Response(status, text=text, headers={"content-type": ct})


def _extract_payload(prompt: str) -> dict:
    out = []
    for aid, ours, text in re.findall(r"^\[(A\d+)\] \(mentions our company: (yes|no)\)\n(.*?)(?=^\[A\d+\]|\Z)", prompt, re.M | re.S):
        brands = sorted((b for b in KNOWN if b.lower() in text.lower()), key=lambda b: text.lower().index(b.lower()))
        sent = None
        if ours == "yes":
            sent = "negative" if "expensive" in text else "positive"
        out.append({"id": aid, "brands": brands, "sentiment": sent, "reason": "quoted"})
    return {"answers": out}


def _payload(system: str, prompt: str):
    STATE.setdefault("systems", []).append(system[:40])
    if system == svc.EXTRACT_SYSTEM:
        if STATE.get("no_extract"):
            raise NoProviderError("none")
        return _extract_payload(prompt)
    if system == svc.REC_SYSTEM:
        STATE["rec_prompt"] = prompt
        return {"recommendations": [
            {"title": "Publish a payroll compliance page for factories", "type": "content", "priority": "high",
             "why": "Keka and Darwinbox are named for this prompt; you are not.", "actions": ["Write the page"],
             "prompts": [P2], "domains": []},
            {"title": "Get reviewed on G2", "type": "bogus", "priority": "urgent", "why": "g2.com is cited most",
             "domains": ["g2.com"]},
            {"title": ""},
        ]}
    if system == svc.SUGGEST_SYSTEM:
        STATE["suggest_prompt"] = prompt
        return {"prompts": [
            {"text": "Best AI HRMS for Indian SMEs?", "topic": "hrms", "reason": "Core category"},
            {"text": P1.upper(), "topic": "dup"},  # already tracked
            {"text": "Keka vs Darwinbox vs VisHR for payroll", "topic": "Comparisons"},
            {"text": "short"},
            "How to choose payroll software for a factory in India",
        ]}
    if system == eng.GROUNDED_SYSTEM:
        STATE["grounded_prompt"] = prompt
        return {"text": "From the results, VisHR [1] and Keka [2] are good options."}
    if system == llms_txt.DESCRIBE_SYSTEM:
        ids = re.findall(r"^\[(P\d+)\] ", prompt, re.M)
        return {"pages": [{"id": i, "description": f"AI line for {i}"} for i in ids]}
    raise AssertionError(f"unexpected prompt: {system[:60]}")


async def fake_complete(session, task, prompt=None, *, messages=None, system=None, json_mode=False, temperature=0.7,
                        max_tokens=1500):
    if STATE.get("no_ai"):
        raise NoProviderError("No AI text provider is set up.")
    out = _payload(system or "", prompt or "")
    text = out["text"] if isinstance(out, dict) and set(out) == {"text"} else json.dumps(out)
    return LlmResult(text, "fake", uuid.uuid4(), "fake-model")


async def fake_search_all(session, query, n=10, *, freshness=None, max_providers=3):
    STATE.setdefault("searches", []).append(query)
    if STATE.get("no_search"):
        raise search.SearchError("Web search failed. DuckDuckGo: blocked")
    return [{"title": "VisHR review", "url": "https://www.g2.com/products/vishr", "snippet": "VisHR vs Keka for payroll",
             "provider": "fake"},
            {"title": "Keka HR", "url": "https://www.keka.com/", "snippet": "Keka payroll software", "provider": "fake"}], []


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    async def public_dns(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(eng, "TRANSPORT", httpx.MockTransport(_engine_handler))
    monkeypatch.setattr(crawler, "TRANSPORT", httpx.MockTransport(_site_handler))
    monkeypatch.setattr(crawler, "resolve_host", public_dns)
    monkeypatch.setattr(llm_router, "complete", fake_complete)
    monkeypatch.setattr(search, "search_all", fake_search_all)
    STATE.clear()
    yield


class tenant:
    def __init__(self, org_id):
        self.s = SessionLocal()
        set_tenant(self.s, org_id)

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        await self.s.close()


def _cfg(oid, provider, name, model, key="k", **settings):
    return ProviderConfig(organization_id=oid, kind="llm", provider=provider, name=name,
                          secrets_enc=encrypt_json({"api_key": key}), settings={"default_model": model, **settings})


async def new_org(client, *, providers=True, name="Vis Co") -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@aivis-test.in"
    from app.core.db import platform_session

    async with platform_session() as s:
        org, _, _ = await provision_organization(s, name=f"{name} {uuid.uuid4().hex[:5]} LLP", admin_email=email,
                                                 admin_name="Owner", admin_password=PW, timezone="Asia/Kolkata",
                                                 website="https://www.visco.test")
        await s.commit()
        oid = org.id
    async with tenant(oid) as s:
        s.add(CompanyProfile(organization_id=oid, summary="Vis Co builds VisHR, an AI-powered HRMS and payroll for Indian companies.",
                             positioning="Compliant payroll with AI agents.", industries=["Manufacturing"], audiences=["HR heads"],
                             differentiators=["AI agents"], geography="India", social_links={}, data={}))
        s.add_all([
            Competitor(organization_id=oid, name="Keka Technologies Pvt Ltd", website="https://keka.com", domain="keka.com",
                       status="confirmed", overlap_score=0.9, data={}),
            Competitor(organization_id=oid, name="Darwinbox", website="https://darwinbox.com", domain="darwinbox.com",
                       status="suggested", overlap_score=0.7, data={}),
            Competitor(organization_id=oid, name="Zoho People", website="https://zoho.com", domain="zoho.com",
                       status="rejected", overlap_score=0.2, data={}),
        ])
        if providers:
            s.add_all([
                _cfg(oid, "perplexity", "Perplexity", "sonar"),
                _cfg(oid, "openai", "OpenAI", "gpt-4o-mini"),
                _cfg(oid, "gemini", "Gemini", "gemini-2.5-flash", key="gem-key"),
                _cfg(oid, "anthropic", "Claude", "claude-haiku-4-5"),
                _cfg(oid, "groq", "Groq", "llama-3.3-70b-versatile"),
            ])
        await s.commit()
    tokens = await login(client, email, PW)
    return {"id": oid, "h": auth(tokens)}


@pytest.fixture
async def org(client, admin):
    return await new_org(client)


async def add_prompts(client, org, texts=(P1, P2)):
    r = await client.post("/aivis/prompts/bulk", headers=org["h"],
                          json={"prompts": [{"text": t, "topic": "HRMS" if t == P1 else "Payroll"} for t in texts]})
    assert r.status_code == 201, r.text
    return r.json()["added"]


async def set_engines(client, org, groq=True, aliases=("VisHR",)):
    s = (await client.get("/aivis/settings", headers=org["h"])).json()
    avail = {a["provider"]: a for a in s["available_engines"]}
    engines = [{"provider_id": avail[p]["provider_id"], "model": avail[p]["suggested_model"], "web": True}
               for p in ("perplexity", "openai", "gemini", "anthropic")]
    if groq:
        engines.append({"provider_id": avail["groq"]["provider_id"], "model": "llama-3.3-70b-versatile", "web": False})
    r = await client.put("/aivis/settings", headers=org["h"], json={**{k: s[k] for k in ("enabled", "weekday", "run_time")},
                                                                     "engines": engines, "aliases": list(aliases)})
    assert r.status_code == 200, r.text
    return r.json()


async def run_check(client, org) -> dict:
    r = await client.post("/aivis/runs", headers=org["h"])
    assert r.status_code == 202, r.text
    rid = r.json()["run"]["id"]
    try:
        await run_inline("aivis.run", org["id"], {"run_id": rid})
    except Exception as e:  # noqa: BLE001
        STATE["run_error"] = e
    return (await client.get(f"/aivis/runs/{rid}", headers=org["h"])).json()


# ------------------------------------------------------------------ rules (pure)
async def test_brand_names_and_matching():
    assert svc.clean_name("HRP AI LLP") == "HRP AI"
    assert svc.clean_name("Keka Technologies Pvt. Ltd.") == "Keka Technologies"
    assert svc.clean_name("Acme Private Limited") == "Acme"
    us = svc.Brand("HRP AI", ["HRP AI", "HRP AI LLP", "hrpai.in", "HR"], is_us=True)
    assert us.first("Try HRP  AI for payroll") == 4  # extra spaces are fine
    assert us.first("Visit www.hrpai.in today") is not None
    assert us.first("THRP AIX and nothrpai.in") is None  # whole words only
    assert len(us.patterns) == 3  # terms under 3 characters are ignored


async def test_list_position_and_analysis():
    us = svc.Brand("VisHR", ["VisHR", "visco.test"], is_us=True)
    keka = svc.Brand("Keka", ["Keka", "keka.com"], tracked=True)
    text = "Unlike VisHR, many teams like these:\n1. Keka – solid\n2. Zoho People\n3. VisHR – excellent and popular"
    an = svc.analyse(text, [], us, [keka], {"visco.test"}, extra_brands=["Zoho People", "Keka", "VISHR"])
    assert an.mentioned and an.brands == ["Keka", "Zoho People", "VisHR"] and an.position == 3
    assert an.competitors == ["Keka"] and an.sentiment == "positive"
    plain = svc.analyse("Keka is good. VisHR is the best and most popular.", [], us, [keka], set())
    assert plain.position == 2 and plain.sentiment == "positive"
    none = svc.analyse("Keka https://keka.com/pricing. and https://visco.test/hrms", [], svc.Brand("Other", ["Other"], is_us=True),
                       [keka], {"visco.test"})
    assert not none.mentioned and none.position is None and none.sentiment is None
    assert [c["domain"] for c in none.citations] == ["keka.com", "visco.test"] and none.own_cited  # URLs parsed from text
    g = svc.tag_citations([{"url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x", "title": "g2.com"}], set())
    assert g[0]["domain"] == "g2.com"


async def test_metrics_share_of_voice_and_missed_prompts():
    def a(prompt, engine, brands, mentioned, pos=None, cites=(), status="ok"):
        return AivisAnswer(prompt_id=None, prompt_text=prompt, topic="T", engine_key=engine, engine_label=engine,
                           status=status, brands=brands, mentioned=mentioned, position=pos, sentiment="positive" if mentioned else None,
                           competitors=[b for b in brands if b == "Keka"], citations=[{"domain": d, "own": d == "us.test"} for d in cites],
                           own_cited="us.test" in cites)
    rows = [a("q1", "e1", ["Keka", "Us"], True, 2, ("g2.com", "us.test")), a("q1", "e2", ["Keka"], False, cites=("g2.com",)),
            a("q2", "e1", ["Keka", "Zoho"], False), a("q2", "e2", [], False), a("q3", "e1", [], False, status="failed")]
    m = svc.compute_metrics(rows, "Us", ["Keka"])
    assert m["answers"] == 4 and m["failed"] == 1 and m["visibility"] == 25.0 and m["avg_position"] == 2.0
    sov = {r["name"]: r for r in m["sov"]}
    assert sov["Keka"]["mentions"] == 3 and sov["Us"]["mentions"] == 1 and sov["Keka"]["tracked"] and not sov["Zoho"]["tracked"]
    assert m["share_of_voice"] == 20.0 and sov["Keka"]["share"] == 60.0
    assert m["missed"] == [{"prompt_id": None, "text": "q2", "topic": "T", "answers": 2, "competitors": ["Keka", "Zoho"]}]
    assert m["top_domains"][0] == {"domain": "g2.com", "answers": 2, "own": False} and m["own_cited"] == 1
    assert {e["key"]: e["visibility"] for e in m["engines"]} == {"e1": 50.0, "e2": 0.0}


# ------------------------------------------------------------------ the full run
async def test_engines_default_and_settings(client, org):
    s = (await client.get("/aivis/settings", headers=org["h"])).json()
    # Automatic choice uses only free engines: Gemini's free tier (web-grounded), Groq (plain) and free web search.
    assert s["engines_auto"] and [e["provider"] for e in s["engines"]] == ["gemini", "groq", "websearch"]
    assert [e["web"] for e in s["engines"]] == [True, False, True] and all(e["cost"] == "free" for e in s["engines"])
    avail = {a["provider"]: a for a in s["available_engines"]}
    assert avail["perplexity"]["always_web"] and not avail["groq"]["web"] and avail["perplexity"]["suggested_model"] == "sonar"
    assert avail["perplexity"]["cost"] == "paid" and avail["openai"]["cost"] == "paid" and avail["gemini"]["cost"] == "free"
    assert avail["websearch"]["cost"] == "free"
    assert [h["provider"] for h in s["paid_hints"]] == ["xai"]  # the paid engines this org hasn't added
    assert s["brand"]["domains"] == ["visco.test"] and "Keka Technologies" in s["competitors"] and "Zoho People" not in s["competitors"]
    s = await set_engines(client, org, aliases=("VisHR", "VisHR", "x"))
    assert not s["engines_auto"] and len(s["engines"]) == 5 and s["aliases"] == ["VisHR"]
    groq = next(e for e in s["engines"] if e["provider"] == "groq")
    assert not groq["web"] and "(no web)" in groq["label"]
    bad = await client.put("/aivis/settings", headers=org["h"], json={"run_time": "25:00"})
    assert bad.status_code == 422
    bad = await client.put("/aivis/settings", headers=org["h"], json={"engines": [{"provider_id": str(uuid.uuid4()), "model": "m"}]})
    assert bad.status_code == 400


async def test_full_run_metrics_answers_and_usage(client, org):
    prompts = await add_prompts(client, org)
    await set_engines(client, org)
    run = await run_check(client, org)
    assert run["status"] == "done", run
    m = run["metrics"]
    assert m["answers"] == 10 and m["failed"] == 0
    assert m["mentioned"] == 3 and m["visibility"] == 30.0  # perplexity, openai and claude on P1
    assert m["sentiment"] == {"positive": 2, "neutral": 0, "negative": 1}
    sov = {r["name"]: r for r in m["sov"]}
    assert sov["Keka Technologies"]["mentions"] == 9 and sov["Keka Technologies"]["tracked"]
    assert sov["Darwinbox"]["mentions"] == 5 and "Zoho People" in sov and not sov["Zoho People"]["tracked"]
    assert m["missed_count"] == 1 and m["missed"][0]["text"] == P2 and m["missed"][0]["competitors"][0] == "Keka Technologies"
    domains = {d["domain"]: d for d in m["top_domains"]}
    assert domains["g2.com"]["answers"] == 4 and domains["visco.test"]["own"] and "capterra.com" in domains
    assert "capterra.in" in domains and "reddit.com" in domains
    assert m["own_cited"] == 2  # perplexity cites our page on both prompts
    # recommendations: AI ones cleaned up (bad type/priority fixed, empty dropped)
    recs = run["recommendations"]
    assert [r["title"] for r in recs] == ["Publish a payroll compliance page for factories", "Get reviewed on G2"]
    assert recs[1]["type"] == "content" and recs[1]["priority"] == "medium" and recs[0]["source"] == "ai"
    assert P2 in STATE["rec_prompt"] and "g2.com" in STATE["rec_prompt"]

    # prompts table with per-engine dots
    table = (await client.get("/aivis/prompts", headers=org["h"])).json()
    assert len(table["engines"]) == 5
    p1 = next(p for p in table["prompts"] if p["text"] == P1)
    dots = {r["engine_label"].split(" ·")[0]: r for r in p1["results"]}
    assert dots["Perplexity"]["mentioned"] and dots["Perplexity"]["position"] == 2
    assert dots["ChatGPT"]["position"] == 3 and not dots["Gemini"]["mentioned"]
    assert dots["Claude"]["sentiment"] == "negative"

    # drill-down
    a = (await client.get(f"/aivis/answers/{dots['Claude']['answer_id']}", headers=org["h"])).json()
    assert a["answer"].startswith("VisHR is often mentioned") and "I'll search" not in a["answer"]
    assert a["citations"][0]["domain"] == "reddit.com" and a["analysis"]["method"] == "rules+ai"
    assert a["analysis"]["sentiment_reason"] == "quoted" and a["analysis"]["searches"] == 1
    listed = (await client.get("/aivis/answers", headers=org["h"], params={"mentioned": "true"})).json()
    assert len(listed) == 3
    hist = (await client.get(f"/aivis/prompts/{prompts[0]['id']}/history", headers=org["h"])).json()
    assert hist[0]["answers"] == 5 and hist[0]["mentioned"] == 3

    comp = (await client.get("/aivis/competitors", headers=org["h"])).json()
    keka = next(h for h in comp["head_to_head"] if h["name"] == "Keka Technologies")
    assert keka == {"name": "Keka Technologies", "tracked": True, "them": 9, "both": 3, "only_them": 6, "only_us": 0,
                    "we_rank_higher": 2}
    assert len(comp["engines"]) == 5

    # every engine call is in AI usage under the "aivis" task, with tokens and web searches
    async with tenant(org["id"]) as s:
        rows = (await s.scalars(select(LlmUsage).where(LlmUsage.task == "aivis"))).all()
    assert len(rows) == 10 and all(r.ok for r in rows)
    claude = [r for r in rows if r.provider == "anthropic"]
    assert claude[0].input_tokens == 50 and claude[0].units == 1
    assert sum(1 for c in STATE["engine_calls"] if c[0] == "api.perplexity.ai") == 2

    ov = (await client.get("/aivis/overview", headers=org["h"])).json()
    assert ov["latest"]["summary"]["visibility"] == 30.0 and len(ov["trend"]) == 1 and ov["prompts"]["enabled"] == 2
    assert ov["next_run_at"] and ov["active_run"] is None


async def test_drop_alert_partial_run_and_resume(client, org):
    await add_prompts(client, org)
    await set_engines(client, org)
    first = await run_check(client, org)
    assert first["metrics"]["visibility"] == 30.0
    # Next week Perplexity stops naming us: visibility 30% -> 20%, a 10-point drop (alert threshold set to 4).
    STATE["perplexity_without_us"] = True
    r = await client.put("/aivis/settings", headers=org["h"], json={**(await client.get("/aivis/settings", headers=org["h"])).json(),
                                                                     "drop_alert_points": 4})
    assert r.status_code == 200, r.text
    second = await run_check(client, org)
    assert second["status"] == "done" and second["metrics"]["visibility"] == 20.0
    async with tenant(org["id"]) as s:
        notes = (await s.scalars(select(Notification).where(Notification.event == "aivis_drop"))).all()
    assert notes and "10 points" in notes[0].title

    # engine failures: partial run; a retry keeps good answers and re-asks only failed ones
    STATE["engines_down"] = True
    third = await run_check(client, org)
    assert third["status"] == "failed" and "server exploded" in (third["error"] or "")
    STATE["engines_down"] = False
    async with tenant(org["id"]) as s:
        run = await s.get(AivisRun, uuid.UUID(third["id"]))
        run.status = "queued"
        await s.commit()
    STATE["engine_calls"] = []
    await run_inline("aivis.run", org["id"], {"run_id": third["id"]})
    again = (await client.get(f"/aivis/runs/{third['id']}", headers=org["h"])).json()
    assert again["status"] == "done" and len(STATE["engine_calls"]) == 10
    async with tenant(org["id"]) as s:
        n = await s.scalar(select(func.count()).select_from(AivisAnswer).where(AivisAnswer.run_id == uuid.UUID(third["id"])))
    assert n == 10


async def test_run_without_ai_extraction_and_rule_recommendations(client, org):
    await add_prompts(client, org)
    await set_engines(client, org)
    await client.post("/aivis/site-check", headers=org["h"])
    STATE["no_ai"] = True
    run = await run_check(client, org)
    assert run["status"] == "done" and run["metrics"]["visibility"] == 30.0
    sov = {r["name"] for r in run["metrics"]["sov"]}
    assert "Zoho People" not in sov  # only the LLM finds brands that aren't tracked
    titles = [r["title"] for r in run["recommendations"]]
    assert any(P2[:40] in t for t in titles) and "Let AI crawlers read your site" in titles
    assert "Publish an llms.txt file" in titles and all(r["source"] == "rules" for r in run["recommendations"])


async def test_run_needs_prompts_and_engines(client, admin):
    bare = await new_org(client, providers=False)
    r = await client.post("/aivis/runs", headers=bare["h"])
    assert r.status_code == 400 and "prompt" in r.json()["detail"]  # never blocked for a missing AI key
    r = await client.put("/aivis/settings", headers=bare["h"], json={"engines": []})
    assert r.status_code == 200 and [e["provider"] for e in r.json()["engines"]] == ["websearch"]
    org = await new_org(client)
    r = await client.post("/aivis/runs", headers=org["h"])
    assert r.status_code == 400 and "prompt" in r.json()["detail"]
    await add_prompts(client, org, [P1])
    r = await client.post("/aivis/runs", headers=org["h"])
    assert r.status_code == 202
    again = await client.post("/aivis/runs", headers=org["h"])
    assert again.status_code == 409


# ------------------------------------------------------------------ prompts
async def test_prompts_crud_cap_and_dedupe(client, org):
    r = await client.post("/aivis/prompts", headers=org["h"], json={"text": "  best   hrms in india  ", "topic": "hrms"})
    assert r.status_code == 201 and r.json()["text"] == "best hrms in india" and r.json()["topic"] == "Hrms"
    pid = r.json()["id"]
    assert (await client.post("/aivis/prompts", headers=org["h"], json={"text": "Best HRMS in India?"})).status_code == 409
    r = await client.patch(f"/aivis/prompts/{pid}", headers=org["h"], json={"enabled": False, "topic": "Category"})
    assert r.json()["enabled"] is False and r.json()["topic"] == "Category"
    bulk = await client.post("/aivis/prompts/bulk", headers=org["h"], json={"prompts": [
        {"text": f"question number {i} about payroll"} for i in range(60)][:50]})
    assert len(bulk.json()["added"]) == 49 and bulk.json()["skipped"] == 1  # cap of 50 in total
    assert (await client.post("/aivis/prompts", headers=org["h"], json={"text": "one more please"})).status_code == 400
    assert (await client.delete(f"/aivis/prompts/{pid}", headers=org["h"])).status_code == 204
    assert (await client.get("/aivis/prompts", headers=org["h"])).json()["run_id"] is None


async def test_suggest_prompts_job(client, org):
    await add_prompts(client, org, [P1])
    r = await client.post("/aivis/prompts/suggest", headers=org["h"], json={"count": 10})
    assert r.status_code == 202
    out = await run_inline("aivis.suggest", org["id"], {"count": 10})
    texts = [p["text"] for p in out["prompts"]]
    assert texts == ["Best AI HRMS for Indian SMEs?", "Keka vs Darwinbox vs VisHR for payroll",
                     "How to choose payroll software for a factory in India"]
    assert out["prompts"][0]["topic"] == "Hrms" and out["prompts"][2]["topic"] == "General"
    assert "Keka Technologies" in STATE["suggest_prompt"] and P1 in STATE["suggest_prompt"]
    assert "VisHR" in STATE["suggest_prompt"]  # company facts from the profile
    STATE["no_ai"] = True
    with pytest.raises(Exception, match="Integrations"):
        await run_inline("aivis.suggest", org["id"], {})


# ------------------------------------------------------------------ weekly schedule
async def test_weekly_schedule(client, org):
    await add_prompts(client, org, [P1])
    # Monday 09:00 IST by default. Monday 12 Oct 2026 03:31 UTC = 09:01 IST: due.
    assert await aivis_mod.schedule_org(org["id"], datetime(2026, 10, 12, 3, 0, tzinfo=UTC)) is None  # 08:30 IST
    run = await aivis_mod.schedule_org(org["id"], datetime(2026, 10, 12, 3, 31, tzinfo=UTC))
    assert run is not None and run.run_key == "weekly:2026-W42" and run.trigger == "scheduled"
    await run_inline("aivis.run", org["id"], {"run_id": str(run.id)})
    assert await aivis_mod.schedule_org(org["id"], datetime(2026, 10, 14, 3, 31, tzinfo=UTC)) is None  # once a week
    nxt = await aivis_mod.schedule_org(org["id"], datetime(2026, 10, 20, 5, 0, tzinfo=UTC))  # laptop was off Monday
    assert nxt is not None and nxt.run_key == "weekly:2026-W43"
    s = (await client.get("/aivis/settings", headers=org["h"])).json()
    await client.put("/aivis/settings", headers=org["h"], json={**s, "enabled": False})
    assert await aivis_mod.schedule_org(org["id"], datetime(2026, 10, 27, 5, 0, tzinfo=UTC)) is None


# ------------------------------------------------------------------ llms.txt and site check
async def _add_pages(oid):
    async with tenant(oid) as s:
        src = KnowledgeSource(organization_id=oid, kind="website", title="Website", url="https://www.visco.test", status="ready", meta={})
        s.add(src)
        await s.flush()
        pages = [
            ("https://www.visco.test/", "Vis Co | AI HRMS", "AI-powered HRMS and payroll for Indian companies."),
            ("https://www.visco.test/products/hrms", "VisHR HRMS - Vis Co", None),
            ("https://www.visco.test/pricing", "Pricing", "Plans for every team size."),
            ("https://www.visco.test/blog/labour-codes", "Labour codes explained", None),
            ("https://www.visco.test/about-us", "About us", "Who we are."),
            ("https://www.visco.test/privacy-policy", "Privacy policy", "How we handle data."),
            ("https://www.visco.test/brochure.pdf", "Brochure", None),
        ]
        for url, title, desc in pages:
            s.add(KnowledgeDocument(organization_id=oid, source_id=src.id, url=url, title=title, content_hash=uuid.uuid4().hex,
                                    text=f"{title}. This page explains our offering in detail for HR teams. More text here.",
                                    meta={"description": desc} if desc else {}))
        await s.commit()


async def test_llms_txt_generate_edit_and_validate(client, org):
    await _add_pages(org["id"])
    r = await client.post("/aivis/llms/generate", headers=org["h"])
    assert r.status_code == 202
    await run_inline("aivis.llms", org["id"], {"use_ai": True})
    d = (await client.get("/aivis/llms", headers=org["h"])).json()
    t = d["text"]
    assert t.startswith("# Vis Co") and "\n> Vis Co builds VisHR" in t and d["issues"] == [] and d["pages"] == 6
    assert "## Main pages\n\n- [Vis Co](https://www.visco.test/): AI-powered HRMS" in t
    assert "## Products and services\n\n- [VisHR HRMS](https://www.visco.test/products/hrms): AI line for P" in t
    assert "## Pricing" in t and "## Blog and resources" in t and "## Company" in t
    assert t.index("## Optional") > t.index("## Company") and "privacy-policy" in t.split("## Optional")[1]
    assert "brochure.pdf" not in t
    assert "Source: https://www.visco.test/pricing" in d["full_text"] and "Privacy policy" not in d["full_text"]
    assert d["site_root"] == "https://www.visco.test" and d["crawled_pages"] == 6
    # edit
    r = await client.put("/aivis/llms", headers=org["h"], json={"text": "Vis Co\n\n- not a link"})
    assert r.status_code == 200 and r.json()["edited_at"]
    issues = r.json()["issues"]
    assert any("first line" in i for i in issues) and any("summary" in i for i in issues)
    assert llms_txt.validate("# A\n\n> s\n\n## Docs\n\n- [x](https://a.test): ok\n- broken item\n")[0].startswith("This list item")
    assert llms_txt.validate("") == ["The file is empty."]
    # without AI, pages without a meta description use their first sentence
    STATE["no_ai"] = True
    await run_inline("aivis.llms", org["id"], {"use_ai": True})
    t2 = (await client.get("/aivis/llms", headers=org["h"])).json()["text"]
    assert "(https://www.visco.test/products/hrms): VisHR HRMS - Vis Co." in t2


async def test_site_check_robots_and_llms(client, org):
    r = await client.post("/aivis/site-check", headers=org["h"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["site"] == "https://www.visco.test" and not d["llms_txt"]["found"] and d["robots"]["found"]
    bots = {b["bot"]: b for b in d["bots"]}
    assert bots["GPTBot"]["status"] == "blocked" and bots["CCBot"]["status"] == "blocked"
    assert bots["ClaudeBot"]["status"] == "allowed" and bots["PerplexityBot"]["status"] == "allowed"
    assert bots["Google-Extended"]["status"] == "allowed" and bots["GPTBot"]["named"]
    SITE["/llms.txt"] = (200, "text/plain", "# Vis Co\n\n> HRMS\n\n## Docs\n\n- [Home](https://www.visco.test/)\n")
    SITE["/robots.txt"] = (404, "text/html", "")
    try:
        d = (await client.post("/aivis/site-check", headers=org["h"])).json()
    finally:
        SITE["/llms.txt"] = (404, "text/html", "<html>not found</html>")
        SITE["/robots.txt"] = (200, "text/plain", "User-agent: GPTBot\nDisallow: /\n\nUser-agent: CCBot\nDisallow: /\n\n"
                                                  "User-agent: *\nDisallow: /admin\n")
    assert d["llms_txt"]["found"] and d["llms_txt"]["issues"] == [] and not d["robots"]["found"]
    assert all(b["status"] == "allowed" for b in d["bots"])
    assert (await client.get("/aivis/llms", headers=org["h"])).json()["site_check"]["llms_txt"]["found"]
    assert llms_txt.robots_status("User-agent: *\nDisallow: /\n", "https://a.test")[0]["status"] == "blocked"


# ------------------------------------------------------------------ engines (unit)
async def test_engine_errors_are_plain_and_logged(client, org):
    async with tenant(org["id"]) as s:
        cfg = await s.scalar(select(ProviderConfig).where(ProviderConfig.provider == "perplexity"))
        e = eng.make_engine(cfg, "sonar", False)
        assert e.web and e.label == "Perplexity · sonar"  # Perplexity always searches the web
        STATE["engines_down"] = True
        res = await eng.ask_many(s, {str(cfg.id): cfg}, [e], P1)
        assert res[0][1] is None and "server exploded" in res[0][2]
        cfg.monthly_budget_usd = 0.000001
        s.add(LlmUsage(organization_id=org["id"], provider_id=cfg.id, provider="perplexity", model="sonar", task="aivis",
                       kind="chat", cost_usd=1, ok=True))
        await s.flush()
        res = await eng.ask_many(s, {str(cfg.id): cfg}, [e], P1)
        assert "monthly budget" in res[0][2]
        await s.rollback()
    assert eng.urls_in_text("See https://a.test/x), and (https://b.test/y.") == [
        {"url": "https://a.test/x", "title": None}, {"url": "https://b.test/y", "title": None}]


# ------------------------------------------------------------------ permissions and tenancy
async def test_permissions(client, org):
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=org["h"])).json()}
    email = f"viewer-{uuid.uuid4().hex[:6]}@aivis-test.in"
    r = await client.post("/users", headers=org["h"], json={"email": email, "full_name": "Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    vh = auth(await login(client, email, r.json()["temporary_password"]))
    for path in ("/aivis/overview", "/aivis/prompts", "/aivis/settings", "/aivis/llms", "/aivis/competitors", "/aivis/runs"):
        assert (await client.get(path, headers=vh)).status_code == 200, path
    for method, path, body in (("post", "/aivis/runs", None), ("post", "/aivis/prompts", {"text": "best hrms"}),
                               ("put", "/aivis/settings", {}), ("put", "/aivis/llms", {"text": "# x"}),
                               ("post", "/aivis/llms/generate", None), ("post", "/aivis/site-check", None),
                               ("post", "/aivis/prompts/suggest", {})):
        res = await getattr(client, method)(path, headers=vh, **({"json": body} if body is not None else {}))
        assert res.status_code == 403, path


async def test_tenant_isolation(client, org):
    await add_prompts(client, org)
    await set_engines(client, org)
    run = await run_check(client, org)
    other = await new_org(client, name="Other Co")
    assert (await client.get("/aivis/prompts", headers=other["h"])).json()["prompts"] == []
    assert (await client.get(f"/aivis/runs/{run['id']}", headers=other["h"])).status_code == 404
    aid = (await client.get("/aivis/answers", headers=org["h"])).json()[0]["id"]
    assert (await client.get(f"/aivis/answers/{aid}", headers=other["h"])).status_code == 404
    assert (await client.get("/aivis/overview", headers=other["h"])).json()["latest"] is None
    async with tenant(other["id"]) as s:
        assert (await s.scalars(select(AivisAnswer))).all() == []
        assert (await s.scalars(select(AivisPrompt))).all() == []


# ------------------------------------------------------------------ free-only setups
async def test_free_only_no_keys_at_all(client, admin):
    """No AI keys, no search keys: the free web-search engine (DuckDuckGo) still produces a full run."""
    org = await new_org(client, providers=False)
    STATE["no_ai"] = True  # what the router does without any text model
    await add_prompts(client, org)
    await client.put("/aivis/settings", headers=org["h"], json={"aliases": ["VisHR"]})
    run = await run_check(client, org)
    assert run["status"] == "done", run
    m = run["metrics"]
    assert m["answers"] == 2 and m["visibility"] == 100.0  # VisHR is in the search results' titles
    assert {d["domain"] for d in m["top_domains"]} == {"g2.com", "keka.com"}
    assert run["recommendations"] and all(r["source"] == "rules" for r in run["recommendations"])
    a = (await client.get("/aivis/answers", headers=org["h"])).json()[0]
    full = (await client.get(f"/aivis/answers/{a['id']}", headers=org["h"])).json()
    assert full["answer"].startswith("Top web results:") and full["engine_label"] == "Web search (free)"
    ov = (await client.get("/aivis/overview", headers=org["h"])).json()
    assert not ov["has_ai"] and len(ov["paid_hints"]) == 4 and ov["engines"][0]["cost"] == "free"
    # llms.txt and the crawler check need no keys either
    assert (await client.post("/aivis/site-check", headers=org["h"])).status_code == 200
    await run_inline("aivis.llms", org["id"], {})
    assert (await client.get("/aivis/llms", headers=org["h"])).json()["text"].startswith("# Vis Co")


async def test_free_model_answers_from_web_results(client, admin):
    org = await new_org(client, providers=False)
    async with tenant(org["id"]) as s:
        s.add(_cfg(org["id"], "groq", "Groq", "llama-3.3-70b-versatile"))
        await s.commit()
    await add_prompts(client, org, [P2])
    await client.put("/aivis/settings", headers=org["h"], json={"aliases": ["VisHR"]})
    run = await run_check(client, org)
    assert run["status"] == "done" and [e["label"] for e in run["engines"]] == ["Groq · llama-3.3-70b-versatile (no web)",
                                                                                 "Web search (free)"]
    answers = {a["engine_label"]: a for a in (await client.get("/aivis/answers", headers=org["h"])).json()}
    web = (await client.get(f"/aivis/answers/{answers['Web search (free)']['id']}", headers=org["h"])).json()
    assert web["answer"].startswith("From the results, VisHR [1]") and web["mentioned"] and web["position"] == 1
    assert [c["domain"] for c in web["citations"]] == ["g2.com", "keka.com"]
    assert "[1] VisHR review" in STATE["grounded_prompt"] and STATE["searches"] == [P2]
    # search down: that engine fails, the run is partial, nothing is blocked
    STATE["no_search"] = True
    run = await run_check(client, org)
    assert run["status"] == "partial" and run["metrics"]["failed"] == 1
