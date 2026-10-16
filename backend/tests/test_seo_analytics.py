"""SEO articles and analytics. LLM, knowledge retrieval, Google OAuth and Google HTTP are mocked."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, update

import app.modules.analytics  # noqa: F401 - registers handlers
import app.modules.seo  # noqa: F401
from app.core import storage
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import (
    AnalyticsSnapshot,
    ContentPillar,
    KnowledgeDocument,
    KnowledgeSource,
    Organization,
    Post,
    PostMetric,
    SocialAccount,
)
from app.services import analytics, seo
from app.services.jobs import PermanentError, run_inline
from app.services.llm import router as llm_router
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"
KW = "employee onboarding software"
STATE: dict = {}

SENTENCES = [
    "Good employee onboarding software helps new people feel welcome from the first day.",
    "It keeps forms, tasks and training in one clear place.",
    "Managers can see what is done and what still needs attention.",
    "New hires know who to ask and what to read next.",
    "HR teams spend less time chasing papers and more time with people.",
    "A simple checklist makes the first week calm and focused.",
    "Clear steps reduce mistakes and repeated questions.",
    "When the plan is shared, everyone moves at the same pace.",
]


def _section_text(words: int, n: int) -> str:
    out, count, i = [], 0, n
    para = []
    while count < words:
        s = SENTENCES[i % len(SENTENCES)]
        para.append(s)
        count += len(s.split())
        i += 1
        if len(para) == 4:
            out.append(" ".join(para))
            para = []
    if para:
        out.append(" ".join(para))
    out.insert(1, f"Teams that compare {KW} should look at the [HRMS product](https://hrpai.in/products/hrms) and "
                  "at how tasks are assigned.")
    if n == 0:
        out.append("For general guidance on onboarding, see [SHRM](https://www.shrm.org).")
    return "\n\n".join(out)


async def fake_complete(session, task, prompt=None, *, messages=None, system=None, json_mode=False,
                        temperature=0.7, max_tokens=1500):
    p = prompt or ""
    if p.startswith("Write the section") or p.startswith("Rewrite the section"):
        n = sum(ord(c) for c in p[:60]) % 8
        text = _section_text(240, n)
        if p.startswith("Rewrite"):
            text = "REWRITTEN. " + text
        return llm_router.LlmResult(text, "fake", uuid.uuid4(), "fake-model")
    return llm_router.LlmResult("{}", "fake", uuid.uuid4(), "fake-model")


async def fake_complete_json(session, task, prompt, *, system=None, temperature=0.5, max_tokens=2500):
    if task == "write":
        return {"text": "We wrote a practical guide to choosing onboarding tools for growing teams. Read it here.",
                "hashtags": ["#HR", "onboarding", "#HRTech", "#People", "#Work", "#Extra"], "title": "Onboarding guide"}
    if "content brief" in prompt:
        return {"title": "Employee onboarding software: a practical guide", "search_intent": "commercial",
                "angle": "practical", "reader_problem": "slow onboarding", "key_points": ["a", "b"],
                "cta": "Book a demo of HRP HRMS"}
    if "Plan the outline" in prompt:
        return {"sections": [
            {"heading": "What employee onboarding software does", "intent": "explain", "target_words": 260,
             "subsections": [{"heading": "Core features", "target_words": 100}]},
            {"heading": "Benefits for HR teams", "intent": "explain", "target_words": 260},
            {"heading": "How to choose a tool", "intent": "how-to", "target_words": 260},
            {"heading": "Rolling it out", "intent": "how-to", "target_words": 260},
            {"heading": "Common mistakes to avoid", "intent": "warn", "target_words": 260},
            {"heading": "Benefits for HR teams", "intent": "dup", "target_words": 200},
        ]}
    if "introduction and conclusion" in prompt:
        return {"intro": f"Choosing {KW} is easier when you know what to look for. This guide walks through the "
                         "features, the benefits and the steps to roll it out.",
                "conclusion": "Start small, measure what matters and improve each month. "
                              "[Talk to us](https://hrpai.in/contact) to see it in action.",
                "conclusion_heading": "Next steps"}
    if "frequently asked questions" in prompt:
        return {"faq": [{"q": f"What is {KW}?", "a": "A tool that organises tasks, forms and training for new hires."},
                        {"q": "How long does setup take?", "a": "It depends on your processes; most teams start with a checklist."},
                        {"q": "Does it work for remote teams?", "a": "Yes, everything is online and shared."},
                        {"q": "Can it connect to payroll?", "a": "Many tools offer integrations; check the vendor docs."}]}
    if "meta title and meta description" in prompt:
        return {"meta_title": "Employee onboarding software: how to choose the right tool for your team",
                "meta_description": f"Learn how {KW} helps HR teams welcome new hires faster, what features matter and "
                                    "how to roll it out step by step."}
    if "Suggest 12 SEO article keywords" in prompt:
        return {"ideas": [{"keyword": "hrms for small business", "intent": "commercial", "difficulty_guess": "medium",
                           "rationale": "Matches the product.", "source": "company"}]}
    return {}


@pytest.fixture(autouse=True)
def mocks(monkeypatch, tmp_path_factory):
    root = STATE.setdefault("root", tmp_path_factory.mktemp("storage"))
    monkeypatch.setattr(storage, "root", lambda: root)
    monkeypatch.setattr(llm_router, "complete", fake_complete)
    monkeypatch.setattr(llm_router, "complete_json", fake_complete_json)
    monkeypatch.setattr(seo, "COVER_WAIT_SECONDS", 0)

    from app.services import knowledge

    async def retrieve(session, query, k=6):
        return [{"text": "HRP HRMS covers onboarding checklists and documents.", "url": "https://hrpai.in/products/hrms",
                 "title": "HRP HRMS"}]

    async def company_context(session, max_chars=3500):
        return "HRP AI LLP builds HRMS and AI products in India."

    monkeypatch.setattr(knowledge, "retrieve", retrieve)
    monkeypatch.setattr(knowledge, "company_context", company_context)


async def _setup(client, admin) -> dict:
    if "org_id" in STATE:
        return STATE
    org_id = uuid.UUID(admin["organization"]["id"])
    async with platform_session() as s:
        await s.execute(update(Organization).where(Organization.id == org_id).values(website="https://hrpai.in"))
        await s.commit()
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        src = KnowledgeSource(organization_id=org_id, kind="website", title="hrpai.in", url="https://hrpai.in", status="ready")
        s.add(src)
        await s.flush()
        for url, title in [("https://hrpai.in/products/hrms", "HRP HRMS"), ("https://hrpai.in/blog/onboarding-checklist",
                                                                          "Onboarding checklist"),
                           ("https://hrpai.in/about", "About us")]:
            s.add(KnowledgeDocument(organization_id=org_id, source_id=src.id, url=url, title=title, content_hash=url[-20:],
                                    text=title))
        await s.commit()
    STATE.update(org_id=org_id, admin=await login(client, "admin@hrpai.in", ADMIN_PW))
    return STATE


# ------------------------------------------------------------------ pure checks
GOOD_BODY = (
    f"# Employee onboarding software guide\n\n"
    f"Choosing {KW} is easier with a plan. " + " ".join(SENTENCES) + "\n\n"
    f"## Why {KW} matters\n\n" + " ".join(SENTENCES * 20) + f" {KW}. " * 8 +
    "\n\nSee [HRMS](https://hrpai.in/products/hrms) and [checklist](/blog/onboarding-checklist) and "
    "[SHRM](https://www.shrm.org).\n\n## Getting started\n\n### First steps\n\n" + " ".join(SENTENCES * 5)
)


async def test_seo_checks_good_vs_bad():
    good = {
        "title": "Employee onboarding software guide", "keyword": KW, "secondary_keywords": [],
        "body_md": GOOD_BODY, "meta_title": "Employee onboarding software: a practical guide",
        "meta_description": seo.fit_meta_description(f"Learn how {KW} helps HR teams welcome new hires faster.", GOOD_BODY),
        "slug": "employee-onboarding-software", "faq": [{"q": "Q?", "a": "A."}], "cover_media_id": None, "outline": [],
    }
    score, checks = seo.seo_checks(good, "https://hrpai.in")
    by = {c["id"]: c for c in checks}
    assert score >= 90, [c for c in checks if not c["ok"]]
    assert 150 <= len(good["meta_description"]) <= 160
    assert by["internal_links"]["ok"] and by["external_links"]["ok"] and by["dup_h2"]["ok"]
    assert abs(sum(c["weight"] for c in checks) - 100) <= 2

    bad = {"title": "Some thoughts", "keyword": KW, "body_md": "# A\n\n# B\n\n## Same\n\nShort text.\n\n## Same\n\n#### Deep\n",
           "meta_title": "x", "meta_description": "", "slug": "thoughts", "faq": []}
    bscore, bchecks = seo.seo_checks(bad, "https://hrpai.in")
    bby = {c["id"]: c for c in bchecks}
    assert bscore < 30
    for cid in ("kw_title", "kw_slug", "word_count", "headings", "dup_h2", "faq", "meta_desc_len", "internal_links"):
        assert not bby[cid]["ok"], cid
    assert bby["external_links"]["severity"] == "warn"


async def test_markdown_to_html_is_safe():
    out = seo.md_to_html("# Hi <b>\n\nA [x](javascript:alert(1)) and [ok](https://a.com) **b** `c<d>`\n\n- one\n- two\n\n"
                         "| a | b |\n|---|---|\n| 1 | 2 |\n\n> quote")
    assert "<b>" not in out and "&lt;b&gt;" in out
    assert "javascript:" not in out and 'href="https://a.com"' in out
    assert "<ul><li>one</li>" in out and "<table>" in out and "<blockquote>" in out and "<code>c&lt;d&gt;</code>" in out


# ------------------------------------------------------------------ article pipeline via API + jobs
async def test_article_job_end_to_end(client, admin):
    st = await _setup(client, admin)
    r = await client.post("/seo/articles", headers=auth(st["admin"]), json={
        "keyword": KW, "secondary_keywords": ["onboarding tool"], "audience": "HR managers", "word_target": 1200,
    })
    assert r.status_code == 201, r.text
    aid, job_id = r.json()["article"]["id"], r.json()["job_id"]
    assert r.json()["article"]["status"] == "queued" and job_id
    result = await run_inline("seo.write", st["org_id"], {"article_id": aid})
    assert result["article_id"] == aid

    a = (await client.get(f"/seo/articles/{aid}", headers=auth(st["admin"]))).json()
    assert a["status"] == "draft"
    assert a["slug"] == "employee-onboarding-software"
    assert a["body_md"].startswith("# Employee onboarding software")
    assert "## Frequently asked questions" in a["body_md"]
    assert len(a["faq"]) == 4
    assert len(a["meta_title"]) <= 60
    assert 150 <= len(a["meta_description"]) <= 160
    assert a["word_count"] >= 1200
    assert a["search_intent"] == "commercial"
    types = [n["@type"] for n in a["schema_json"]["@graph"]]
    assert types == ["Article", "FAQPage", "Organization"]
    assert a["schema_json"]["@graph"][0]["url"] == "https://hrpai.in/blog/employee-onboarding-software"
    h2s = [h["text"] for h in a["headings"] if h["level"] == 2]
    assert len(h2s) == len(set(h2s))  # duplicate outline heading removed
    assert a["seo_score"] >= 80, [c for c in a["seo_checks"] if not c["ok"]]
    STATE["article_id"] = aid


async def test_no_text_model_fails_permanently(client, admin, monkeypatch):
    st = await _setup(client, admin)

    async def none(*a, **k):
        raise llm_router.NoProviderError("none")

    monkeypatch.setattr(llm_router, "complete_json", none)
    r = await client.post("/seo/articles", headers=auth(st["admin"]), json={"keyword": "hr analytics"})
    aid = r.json()["article"]["id"]
    with pytest.raises(PermanentError, match="Add an AI text model in Settings → Integrations"):
        await run_inline("seo.write", st["org_id"], {"article_id": aid})
    a = (await client.get(f"/seo/articles/{aid}", headers=auth(st["admin"]))).json()
    assert a["status"] == "error" and "Integrations" in a["error"]
    assert (await client.delete(f"/seo/articles/{aid}", headers=auth(st["admin"]))).status_code == 204


async def test_edit_rewrite_and_score(client, admin):
    st = await _setup(client, admin)
    aid, h = STATE["article_id"], auth(st["admin"])
    r = await client.patch(f"/seo/articles/{aid}", headers=h, json={"meta_title": "Short", "slug": "Onboarding Software Guide"})
    assert r.status_code == 200
    assert r.json()["slug"] == "onboarding-software-guide"
    by = {c["id"]: c for c in r.json()["seo_checks"]}
    assert not by["meta_title_len"]["ok"] and not by["kw_slug"]["ok"]
    r = await client.patch(f"/seo/articles/{aid}", headers=h, json={
        "meta_title": "Employee onboarding software: a practical guide", "slug": "employee-onboarding-software"})
    assert r.json()["slug"] == "employee-onboarding-software"

    r = await client.post(f"/seo/articles/{aid}/rewrite-section", headers=h,
                          json={"heading": "How to choose a tool", "instruction": "Make it more concrete"})
    assert r.status_code == 200, r.text
    body = r.json()["body_md"]
    assert "## How to choose a tool\n\nREWRITTEN." in body
    assert body.count("## How to choose a tool") == 1
    bad = await client.post(f"/seo/articles/{aid}/rewrite-section", headers=h, json={"heading": "Nope", "instruction": ""})
    assert bad.status_code == 400
    s = await client.post(f"/seo/articles/{aid}/score", headers=h)
    assert s.status_code == 200 and 0 <= s.json()["seo_score"] <= 100


async def test_export_writes_files(client, admin):
    st = await _setup(client, admin)
    aid, h = STATE["article_id"], auth(st["admin"])
    r = await client.post(f"/seo/articles/{aid}/export", headers=h)
    assert r.status_code == 200 and r.json()["job_id"]
    res = await run_inline("seo.export", st["org_id"], {"article_id": aid})
    assert set(res["files"]) >= {"article.mdx", "article.html", "meta.json"}
    folder = STATE["root"] / "seo" / "hrp-ai" / "employee-onboarding-software"
    mdx = (folder / "article.mdx").read_text()
    assert mdx.startswith("---\ntitle: ") and 'slug: "employee-onboarding-software"' in mdx and "canonical:" in mdx
    html = (folder / "article.html").read_text()
    assert "application/ld+json" in html and "<h1" in html and "#086fb8" in html
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["schema"]["@graph"][0]["@type"] == "Article" and meta["faq"]
    a = (await client.get(f"/seo/articles/{aid}", headers=h)).json()
    assert a["status"] == "exported" and a["export_path"] == "seo/hrp-ai/employee-onboarding-software"
    d = await client.get(f"/seo/articles/{aid}/download?format=html", headers=h)
    assert d.status_code == 200 and "<!doctype html>" in d.text


async def test_repurpose_creates_in_review_posts(client, admin):
    st = await _setup(client, admin)
    aid, h = STATE["article_id"], auth(st["admin"])
    async with SessionLocal() as s:
        set_tenant(s, st["org_id"])
        li = SocialAccount(organization_id=st["org_id"], platform="linkedin_org", name="HRP AI", status="connected")
        ig = SocialAccount(organization_id=st["org_id"], platform="instagram", name="hrpai", status="connected")
        s.add_all([li, ig])
        await s.commit()
    r = await client.post(f"/seo/articles/{aid}/repurpose", headers=h, json={"account_ids": [str(li.id), str(ig.id)]})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    async with SessionLocal() as s:
        set_tenant(s, st["org_id"])
        posts = (await s.scalars(select(Post).where(Post.seo_article_id == uuid.UUID(aid)))).all()
    assert len(posts) == 2
    by = {p.platform: p for p in posts}
    url = "https://hrpai.in/blog/employee-onboarding-software"
    for p in posts:
        assert p.status == "in_review" and p.mode == "approve_first" and p.scheduled_at is None and p.link_url == url
    assert by["linkedin_org"].post_type == "link"
    assert by["instagram"].post_type == "image" and url in by["instagram"].text
    assert len(by["linkedin_org"].hashtags) <= 5 and all(t.startswith("#") for t in by["linkedin_org"].hashtags)


async def test_keyword_ideas_use_search_console(client, admin):
    st = await _setup(client, admin)
    async with SessionLocal() as s:
        set_tenant(s, st["org_id"])
        s.add(AnalyticsSnapshot(organization_id=st["org_id"], source="gsc", key="sc-domain:hrpai.in", data={"queries": [
            {"query": "hrms software india", "clicks": 3, "impressions": 400, "ctr": 0.0075, "position": 8.2},
            {"query": "hrp ai", "clicks": 50, "impressions": 60, "ctr": 0.83, "position": 1.0},
        ]}))
        await s.commit()
        ideas = await seo.keyword_ideas(s, "hrms")
    kws = {i["keyword"]: i for i in ideas}
    assert "hrms for small business" in kws
    assert kws["hrms software india"]["source"] == "search_console"
    assert "hrp ai" not in kws
    r = await client.post("/seo/keywords", headers=auth(st["admin"]), json={"seed": "hrms", "background": False})
    assert r.status_code == 200 and r.json()["ideas"]


async def test_viewer_cannot_create_articles(client, admin):
    st = await _setup(client, admin)
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(st["admin"]))).json()}
    r = await client.post("/users", headers=auth(st["admin"]),
                          json={"email": "seo-viewer@hrpai.in", "full_name": "Seo Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    v = await login(client, "seo-viewer@hrpai.in", r.json()["temporary_password"])
    assert (await client.get("/seo/articles", headers=auth(v))).status_code == 200
    r = await client.post("/seo/articles", headers=auth(v), json={"keyword": "anything here"})
    assert r.status_code == 403 and "seo.manage" in r.json()["detail"]
    assert (await client.get("/analytics/overview", headers=auth(v))).status_code == 200
    assert (await client.put("/analytics/settings", headers=auth(v), json={"ga4_property": "1"})).status_code == 403


async def test_tenant_isolation(client, admin):
    st = await _setup(client, admin)
    r = await client.post("/platform/organizations", headers=auth(st["admin"]),
                          json={"name": "Other SEO Co", "admin_email": "owner@other-seo.in", "admin_name": "Other"})
    assert r.status_code == 201, r.text
    other = await login(client, "owner@other-seo.in", r.json()["temporary_password"])
    assert (await client.get("/seo/articles", headers=auth(other))).json() == []
    aid = STATE["article_id"]
    assert (await client.get(f"/seo/articles/{aid}", headers=auth(other))).status_code == 404
    assert (await client.delete(f"/seo/articles/{aid}", headers=auth(other))).status_code == 404
    ov = (await client.get("/analytics/overview", headers=auth(other))).json()
    assert ov["gsc"] is None and ov["social"]["totals"]["posts"] == 0


# ------------------------------------------------------------------ Google analytics parsing
def _ga_handler(calls: list):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "pagespeedonline" in url:
            strategy = request.url.params.get("strategy")
            return httpx.Response(200, json={
                "id": "https://hrpai.in/",
                "lighthouseResult": {"finalUrl": "https://hrpai.in/", "categories": {
                    "performance": {"score": 0.91 if strategy == "desktop" else 0.62}, "seo": {"score": 1.0}},
                    "audits": {"largest-contentful-paint": {"numericValue": 2400.5, "displayValue": "2.4 s", "score": 0.8},
                               "cumulative-layout-shift": {"numericValue": 0.02, "displayValue": "0.02", "score": 1}}},
                "loadingExperience": {"overall_category": "AVERAGE", "metrics": {
                    "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2600, "category": "AVERAGE"},
                    "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5, "category": "FAST"}}}})
        body = json.loads(request.content or b"{}")
        assert request.headers["Authorization"] == "Bearer tok"
        if "analyticsdata" in url:
            assert "/properties/123456:runReport" in url
            dims = [d["name"] for d in body.get("dimensions", [])]
            mets = [m["name"] for m in body["metrics"]]
            if not dims:
                if "keyEvents" in mets:
                    return httpx.Response(400, json={"error": {"message": "Field keyEvents is not a valid metric."}})
                rows = []
                for name, mult in (("current", 2), ("previous", 1)):
                    rows.append({"dimensionValues": [{"value": name}],
                                 "metricValues": [{"value": str(100 * mult)} for _ in mets]})
                return httpx.Response(200, json={"dimensionHeaders": [{"name": "dateRange"}],
                                                 "metricHeaders": [{"name": m} for m in mets], "rows": rows})
            if dims == ["date"]:
                rows = [{"dimensionValues": [{"value": d}], "metricValues": [{"value": v} for v in ("10", "8")[:len(mets)]]}
                        for d in ("20261002", "20261001")]
                return httpx.Response(200, json={"dimensionHeaders": [{"name": "date"}],
                                                 "metricHeaders": [{"name": m} for m in mets], "rows": rows})
            rows = [{"dimensionValues": [{"value": "/blog/x" if dims == ["pagePath"] else "Organic Search"}],
                     "metricValues": [{"value": "42"} for _ in mets]}]
            return httpx.Response(200, json={"dimensionHeaders": [{"name": d} for d in dims],
                                             "metricHeaders": [{"name": m} for m in mets], "rows": rows})
        if "webmasters" in url:
            assert "/sites/sc-domain%3Ahrpai.in/searchAnalytics/query" in url
            dims = body.get("dimensions") or []
            if dims == ["date"]:
                return httpx.Response(200, json={"rows": [
                    {"keys": ["2026-10-01"], "clicks": 10, "impressions": 200, "ctr": 0.05, "position": 9},
                    {"keys": ["2026-10-02"], "clicks": 20, "impressions": 200, "ctr": 0.1, "position": 7}]})
            if dims == ["query"]:
                return httpx.Response(200, json={"rows": [
                    {"keys": ["hrms software"], "clicks": 2, "impressions": 300, "ctr": 0.0067, "position": 11.4},
                    {"keys": ["hrp ai"], "clicks": 40, "impressions": 50, "ctr": 0.8, "position": 1.1}]})
            return httpx.Response(200, json={"rows": [
                {"keys": ["https://hrpai.in/"], "clicks": 30, "impressions": 400, "ctr": 0.075, "position": 4}]})
        return httpx.Response(404, json={"error": {"message": "unexpected"}})
    return handle


async def test_ga4_gsc_pagespeed_parsing_into_snapshots(client, admin, monkeypatch):
    st = await _setup(client, admin)
    calls: list = []
    transport = httpx.MockTransport(_ga_handler(calls))
    monkeypatch.setattr(analytics, "http_client", lambda **kw: httpx.AsyncClient(transport=transport))

    from app.services.social import google

    fake_acc = SimpleNamespace(id=uuid.uuid4(), name="Google", status="connected",
                               settings={"ga4_property": "properties/123456", "gsc_site": "sc-domain:hrpai.in"})

    async def acc(session):
        return fake_acc

    async def tok(session):
        return "tok"

    monkeypatch.setattr(google, "google_account", acc)
    monkeypatch.setattr(google, "google_access_token", tok)

    result = await run_inline("intel.analytics", st["org_id"], {})
    assert set(result["updated"]) == {"ga4", "gsc", "pagespeed"}, result

    async with SessionLocal() as s:
        set_tenant(s, st["org_id"])
        ga = await analytics.latest(s, "ga4")
        gsc = await analytics.latest(s, "gsc")
        ps = await analytics.latest(s, "pagespeed")
    t = ga.data["totals"]
    assert ga.key == "123456" and t["conversion_metric"] == "conversions"
    assert t["current"]["sessions"] == 200 and t["previous"]["sessions"] == 100 and t["deltas"]["sessions"] == 100.0
    assert [d["date"] for d in ga.data["by_day"]] == ["2026-10-01", "2026-10-02"]
    assert ga.data["pages"][0] == {"path": "/blog/x", "views": 42, "sessions": 42}
    assert ga.data["channels"][0]["name"] == "Organic Search"

    g = gsc.data
    assert g["totals"]["current"]["clicks"] == 30 and g["totals"]["current"]["impressions"] == 400
    assert g["totals"]["current"]["position"] == 8.0 and g["totals"]["current"]["ctr"] == 0.075
    q = {r["query"]: r for r in g["queries"]}
    assert q["hrms software"]["opportunity"] is True and q["hrp ai"]["opportunity"] is False
    assert [r["query"] for r in g["opportunities"]] == ["hrms software"]

    assert ps.data["mobile"]["scores"]["performance"] == 62 and ps.data["desktop"]["scores"]["performance"] == 91
    assert ps.data["mobile"]["field"]["cls"]["p75"] == 0.05 and ps.data["mobile"]["lab"]["lcp"]["display"] == "2.4 s"

    h = auth(st["admin"])
    s = (await client.get("/analytics/search", headers=h)).json()
    assert s["snapshot"]["data"]["site"] == "sc-domain:hrpai.in" and s["connection"]["connected"] is True
    ov = (await client.get("/analytics/overview", headers=h)).json()
    assert ov["ga4"]["current"]["sessions"] == 200 and ov["pagespeed"]["mobile"]["scores"]["performance"] == 62
    assert ov["last_refresh"]["updated"]


async def test_refresh_skips_without_google(client, admin, monkeypatch):
    st = await _setup(client, admin)
    from app.services.social import google

    async def none(session):
        return None

    monkeypatch.setattr(google, "google_account", none)
    monkeypatch.setattr(analytics, "http_client", lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"error": {"message": "down"}}))))
    res = await run_inline("intel.analytics", st["org_id"], {})
    assert res["updated"] == [] and "Google isn't connected" in res["message"]
    r = await client.put("/analytics/settings", headers=auth(st["admin"]), json={"ga4_property": "1"})
    assert r.status_code == 400 and "Connect Google" in r.json()["detail"]
    jr = await client.post("/analytics/refresh", headers=auth(st["admin"]))
    assert jr.status_code == 200 and jr.json()["job_id"]


# ------------------------------------------------------------------ social performance
async def test_social_performance_and_best_times(client, admin):
    st = await _setup(client, admin)
    org_id = st["org_id"]
    base = (datetime.now(UTC) - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        pillar = ContentPillar(organization_id=org_id, name="Product tips")
        acc = SocialAccount(organization_id=org_id, platform="linkedin_org", name="HRP Page", status="connected")
        s.add_all([pillar, acc])
        await s.flush()
        posts = []
        for i in range(5):  # same weekday and hour, weekly
            posts.append(Post(organization_id=org_id, platform="linkedin_org", social_account_id=acc.id, pillar_id=pillar.id,
                              post_type="image", status="published", published_at=base - timedelta(days=7 * i),
                              text=f"post {i}"))
        posts.append(Post(organization_id=org_id, platform="facebook_page", post_type="text", status="published",
                          published_at=base - timedelta(days=2, hours=3), text="fb post"))
        posts.append(Post(organization_id=org_id, platform="facebook_page", post_type="text", status="published",
                          published_at=base - timedelta(days=60), text="too old"))
        s.add_all(posts)
        await s.flush()
        for p in posts[:5]:
            s.add(PostMetric(organization_id=org_id, post_id=p.id, impressions=100, likes=1,
                             fetched_at=datetime.now(UTC) - timedelta(days=1)))
            s.add(PostMetric(organization_id=org_id, post_id=p.id, impressions=200, likes=10, comments=2, shares=1,
                             saves=1, clicks=6, fetched_at=datetime.now(UTC)))  # latest wins
        s.add(PostMetric(organization_id=org_id, post_id=posts[5].id, impressions=0, likes=3))
        await s.commit()

    r = await client.get("/analytics/social?days=30", headers=auth(st["admin"]))
    assert r.status_code == 200, r.text
    d = r.json()
    t = d["totals"]
    assert t["posts"] == 6 and t["impressions"] == 1000 and t["engagements"] == 5 * 20 + 3
    assert t["engagement_rate"] == round(103 / 1000, 4)
    plats = {p["key"]: p for p in d["by_platform"]}
    assert plats["linkedin_org"]["engagement_rate"] == 0.1 and plats["linkedin_org"]["label"] == "LinkedIn company page"
    assert plats["facebook_page"]["engagement_rate"] == 3.0  # impressions 0 -> divided by 1
    assert {p["label"] for p in d["by_pillar"]} == {"Product tips", "No pillar"}
    assert {p["key"] for p in d["by_post_type"]} == {"image", "text"}
    assert any(a["label"].startswith("HRP Page") for a in d["by_account"])
    assert len(d["best_times"]) == 1 and d["best_times_message"] is None
    bt = d["best_times"][0]
    assert bt["posts"] == 5 and bt["avg_engagement_rate"] == 0.1
    assert any(not c["enough"] for c in d["heatmap"])
    assert d["top_posts"][0]["engagements"] == 20 and d["timezone"] == "Asia/Kolkata"
