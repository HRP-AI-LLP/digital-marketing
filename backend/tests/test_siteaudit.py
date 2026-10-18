"""Site audit: crawler facts, checks firing on a deliberately broken mock website, scoring, PageSpeed
(with quota back-off), new/fixed issues across runs, compare, CSV export, schedule, permissions and tenancy.

No real network: the site, external links, images and PageSpeed Insights are httpx.MockTransport handlers
and DNS is stubbed to a public address.
"""

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

import app.modules.siteaudit as sa_mod
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import Notification, SiteAuditIssue, SiteAuditRun
from app.services import crawler
from app.services.jobs import run_inline
from app.services.siteaudit import checks, crawl, pagespeed
from app.services.tenancy import provision_organization
from tests.conftest import auth, login

PW = "Str0ngPassw0rd!"
SITE = "https://problem-site.test"
WORDS = ("Our payroll platform helps Indian companies close payroll faster with automated compliance, attendance "
         "and leave management for every team member across offices. ") * 12
STATE: dict = {}


def html(title: str | None, body: str, *, head: str = "", lang: str | None = "en", viewport: bool = True,
         desc: str | None = "A clear description of this page that is long enough to be useful in search results.") -> str:
    t = f"<title>{title}</title>" if title is not None else ""
    d = f"<meta name='description' content='{desc}'>" if desc else ""
    v = "<meta name='viewport' content='width=device-width, initial-scale=1'>" if viewport else ""
    la = f" lang='{lang}'" if lang else ""
    return (f"<!DOCTYPE html><html{la}><head><meta charset='utf-8'>{t}{d}{v}{head}</head>"
            f"<body>{body}</body></html>")


OG = ("<meta property='og:title' content='T'><meta property='og:description' content='D'>"
      "<meta property='og:image' content='https://problem-site.test/img/og.png'><meta property='og:url' content='{url}'>"
      "<meta name='twitter:card' content='summary'>")
ORG_LD = "<script type='application/ld+json'>{\"@context\":\"https://schema.org\",\"@type\":\"Organization\",\"name\":\"P\"}</script>"


def pages() -> dict[str, tuple[int, dict, str]]:
    home_title = None if STATE.get("worse") else "Problem Site – Payroll software for Indian teams"
    home_links = ("<a href='/about'>About us</a><a href='/products'>Products</a><a href='/products-copy'>Copy</a>"
                  "<a href='/old'>Old about</a><a href='/missing'>Missing</a><a href='/chain-start'>Chain</a>"
                  "<a href='/temp'>Temp</a><a href='/noindex-page'>Hidden</a><a href='/Upper_Case?id=1'>Odd URL</a>"
                  "<a href='/private/x'>Private</a><a href='/hi'>Hindi</a><a href='/brochure.pdf'>Brochure</a>"
                  "<a href='https://dead-external.test/page'>Partner</a><a href='https://ok-external.test/'>Friend</a>"
                  "<a href='/products'><img src='/img/logo.png' alt=''></a><a href='/about'>click here</a>")
    hreflang = ("<link rel='alternate' hreflang='en' href='https://problem-site.test/'>"
                "<link rel='alternate' hreflang='hi' href='https://problem-site.test/hi'>"
                "<link rel='alternate' hreflang='xx_bad' href='https://problem-site.test/'>")
    prod = ("<h1>Payroll products</h1><h3>Skipped level</h3><p>" + WORDS + "</p>"
            "<form action='http://problem-site.test/submit' method='post'><input name='email'></form>"
            "<a href='/'>Home</a><a href='/loop-a'>Loop</a><a href='/server-error'>Status</a><a href='/d1'>Deeper</a>"
            "<img src='/img/big.jpg' alt='Big'><img src='/img/missing.png' alt='Missing'>")
    canon = "<link rel='canonical' href='https://problem-site.test/products'>"
    ok = {"content-type": "text/html; charset=utf-8"}
    return {
        "/": (200, ok, html(home_title, f"<h1>Payroll for India</h1><p>{WORDS}</p>{home_links}",
                            head=hreflang + OG.format(url=SITE + "/") + ORG_LD + "<link rel='canonical' href='/'>")),
        "/about": (200, ok, html("About", "<p>Short page.</p><img src='http://cdn.problem-site.test/a.png'>"
                                          "<script type='application/ld+json'>{bad json</script><a href='/'>Home</a>",
                                 desc=None, head="<link rel='canonical' href='https://problem-site.test/gone'>", lang=None)),
        "/products": (200, ok, html("Payroll products", prod, viewport=False, head=canon + OG.format(url=SITE + "/products"))),
        "/products-copy": (200, ok, html("Payroll products", prod.replace("/d1", "/"), viewport=False,
                                         head="<link rel='canonical' href='https://problem-site.test/products-copy'>")),
        "/noindex-page": (200, ok, html("A hidden page that nobody should find in search", f"<h1>Hidden</h1><p>{WORDS}</p>",
                                        head="<meta name='robots' content='noindex, noai'><a href='/'>x</a>")),
        "/orphan": (200, ok, html("Orphan page that only the sitemap knows about", f"<h1>Orphan</h1><p>{WORDS}</p><a href='/'>Home</a>")),
        "/Upper_Case": (200, ok, html("Odd URL page with parameters and capitals here", f"<h1>Odd</h1><p>{WORDS}</p><a href='/'>Home</a>")),
        "/hi": (200, ok, html("हिंदी पेज – पेरोल सॉफ्टवेयर भारत के लिए", f"<h1>Hindi</h1><p>{WORDS}</p><a href='/'>Home</a>", lang="hi")),
        "/d1": (200, ok, html("Deep level one page title for the test", f"<h1>D1</h1><p>{WORDS}</p><a href='/d2'>next</a>")),
        "/d2": (200, ok, html("Deep level two page title for the test", f"<h1>D2</h1><p>{WORDS} two</p><a href='/d3'>next</a>")),
        "/d3": (200, ok, html("Deep level three page title for the test", f"<h1>D3</h1><p>{WORDS} three</p><a href='/'>home</a>")),
        "/private/x": (200, ok, html("Private", "<p>never fetched</p>")),
    }


REDIRECTS = {"/old": (301, "/about"), "/chain-start": (301, "/chain-mid"), "/chain-mid": (301, "/products"),
             "/temp": (302, "/products"), "/loop-a": (301, "/loop-b"), "/loop-b": (301, "/loop-a")}
ROBOTS = ("User-agent: *\nDisallow: /private/\n\nUser-agent: GPTBot\nDisallow: /\n\nUser-agent: PerplexityBot\n"
          "Disallow: /\n\nthis line is nonsense\nSitemap: https://problem-site.test/sitemap.xml\n")
SITEMAP = ("<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
           + "".join(f"<url><loc>{SITE}{p}</loc></url>" for p in ("/", "/about", "/orphan", "/gone", "/old", "/noindex-page",
                                                                  "/private/x"))
           + "<url><loc>https://problem-site.test/d1</loc><lastmod>2099-01-01</lastmod></url></urlset>")
REQUESTS: list[str] = []


def site_handler(request: httpx.Request) -> httpx.Response:
    url = request.url
    REQUESTS.append(f"{request.method} {url}")
    host, path = url.host, url.path
    if host == "problem-site.test" and url.scheme == "http":
        return httpx.Response(200, text=html("Insecure copy", "<p>http copy</p>"), headers={"content-type": "text/html"})
    if host == "www.problem-site.test":
        return httpx.Response(200, text=html("www copy", "<p>www</p>"), headers={"content-type": "text/html"})
    if host == "problem-site.test":
        if path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS, headers={"content-type": "text/plain"})
        if path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP, headers={"content-type": "application/xml"})
        if path in REDIRECTS:
            code, to = REDIRECTS[path]
            return httpx.Response(code, headers={"location": to})
        if path == "/server-error":
            return httpx.Response(500, text="boom", headers={"content-type": "text/html"})
        if path == "/missing" and STATE.get("fixed_missing"):
            return httpx.Response(200, text=html("Missing page is back again with a good title", f"<h1>Back</h1><p>{WORDS}</p>"),
                                  headers={"content-type": "text/html"})
        if path == "/img/big.jpg":
            return httpx.Response(200, headers={"content-type": "image/jpeg", "content-length": "500000"})
        if path in ("/img/logo.png", "/img/og.png"):
            return httpx.Response(200, headers={"content-type": "image/png", "content-length": "2000"})
        if path == "/brochure.pdf":
            return httpx.Response(404)
        p = pages().get(path)
        if p:
            code, headers, body = p
            return httpx.Response(code, text=body, headers=headers)
        return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
    if host == "dead-external.test":
        return httpx.Response(404)
    if host == "ok-external.test":
        return httpx.Response(200, headers={"content-type": "text/html"})
    if host == "cdn.problem-site.test":
        return httpx.Response(200, headers={"content-type": "image/png", "content-length": "100"})
    return httpx.Response(404)


def psi_payload(lcp: float, perf: float) -> dict:
    return {
        "lighthouseResult": {
            "categories": {"performance": {"score": perf}, "accessibility": {"score": 0.95}, "best-practices": {"score": 0.8},
                           "seo": {"score": 0.97}},
            "audits": {"largest-contentful-paint": {"numericValue": lcp}, "cumulative-layout-shift": {"numericValue": 0.31},
                       "total-blocking-time": {"numericValue": 750}, "first-contentful-paint": {"numericValue": 1500},
                       "render-blocking-resources": {"score": 0.3, "title": "Eliminate render-blocking resources",
                                                     "details": {"type": "opportunity", "overallSavingsMs": 900}}}},
        "loadingExperience": {"overall_category": "SLOW", "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 4300, "category": "SLOW"},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 650, "category": "SLOW"},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5, "category": "FAST"}}},
    }


def psi_handler(request: httpx.Request) -> httpx.Response:
    STATE.setdefault("psi_calls", []).append(dict(request.url.params))
    if STATE.get("psi_quota"):
        return httpx.Response(429, json={"error": {"message": "Quota exceeded"}})
    return httpx.Response(200, json=psi_payload(5200, 0.42))


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    async def public_dns(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(crawler, "resolve_host", public_dns)
    monkeypatch.setattr(crawl, "TRANSPORT", httpx.MockTransport(site_handler))
    monkeypatch.setattr(crawl, "BASE_DELAY", 0)
    monkeypatch.setattr(pagespeed, "TRANSPORT", httpx.MockTransport(psi_handler))
    monkeypatch.setattr(pagespeed, "BACKOFF_SECONDS", 0)
    STATE.clear()
    REQUESTS.clear()
    yield


class tenant:
    def __init__(self, org_id):
        self.s = SessionLocal()
        set_tenant(self.s, org_id)

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        await self.s.close()


async def new_org(client, website: str | None = SITE) -> dict:
    email = f"owner-{uuid.uuid4().hex[:10]}@audit-test.in"
    async with platform_session() as s:
        org, _, _ = await provision_organization(s, name=f"Audit Co {uuid.uuid4().hex[:5]}", admin_email=email,
                                                 admin_name="Owner", admin_password=PW, timezone="Asia/Kolkata",
                                                 website=website)
        await s.commit()
        oid = org.id
    tokens = await login(client, email, PW)
    return {"id": oid, "h": auth(tokens)}


@pytest.fixture
async def org(client, admin):
    return await new_org(client)


async def run_audit(client, org) -> dict:
    r = await client.post("/site-audit/runs", headers=org["h"])
    assert r.status_code == 202, r.text
    run_id = r.json()["run"]["id"]
    try:
        await run_inline("siteaudit.run", org["id"], {"run_id": run_id})
    except Exception as e:  # noqa: BLE001 - tests inspect the run row
        STATE["run_error"] = e
    return (await client.get(f"/site-audit/runs/{run_id}", headers=org["h"])).json()


# ------------------------------------------------------------------ pure helpers
async def test_catalog_has_enough_documented_checks():
    cat = checks.catalog()
    assert len(cat) >= 140
    assert {c["severity"] for c in cat} == {"error", "warning", "notice"}
    for c in cat:
        assert c["title"] and len(c["why"]) > 20 and len(c["fix"]) > 15, c["id"]
        assert c["category"] in checks.CATEGORIES


async def test_health_score_weights_severity_and_share():
    assert checks.health_score({}, 10) == 100
    one_error_all = checks.health_score({"http_4xx": 10}, 10)
    one_error_few = checks.health_score({"http_4xx": 1}, 10)
    notice_all = checks.health_score({"url_params": 10}, 10)
    assert one_error_all < one_error_few < 100 and notice_all > one_error_all
    assert checks.health_score({"robots_missing": 1}, 1000) < checks.health_score({"title_too_long": 1}, 1000)


async def test_robots_ai_access_and_patterns():
    acc = crawl.bot_access(ROBOTS, True, SITE, "GPTBot")
    assert acc["status"] == "blocked" and acc["named"]
    assert crawl.bot_access(ROBOTS, True, SITE, "ClaudeBot")["status"] == "allowed"  # "*" only hides /private/
    assert crawl.bot_access("User-agent: ClaudeBot\nDisallow: /docs/\n", True, SITE, "ClaudeBot")["status"] == "partial"
    assert crawl.bot_access(None, False, SITE, "CCBot")["status"] == "allowed"
    rep = crawl.robots_report(ROBOTS, 200, None, SITE, crawler.USER_AGENT)
    assert rep["invalid_lines"] == ["this line is nonsense"] and rep["sitemaps"]
    assert crawl._match("/blog/*", "https://x.test/blog/post") and not crawl._match("/blog/*", "https://x.test/news")
    assert crawl._match("tag", "https://x.test/tag/a") and not crawl._match("tag", "https://x.test/about")


async def test_parse_page_facts():
    f = crawl.parse_page(pages()["/products"][2], SITE + "/products", "problem-site.test")
    assert f["title"] == "Payroll products" and f["viewport"] is None and f["heading_skip"] == "H1 → H3"
    assert f["forms"][0]["action"].startswith("http://") and f["word_count"] > 200 and f["content_hash"]
    a = crawl.parse_page(pages()["/about"][2], SITE + "/about", "problem-site.test")
    assert a["mixed_content"] == ["http://cdn.problem-site.test/a.png"] and a["images_missing_alt"]
    assert a["jsonld"][0]["error"].startswith("Invalid JSON") and a["lang"] is None
    h = crawl.parse_page(pages()["/"][2], SITE + "/", "problem-site.test")
    assert {x["lang"] for x in h["hreflang"]} == {"en", "hi", "xx_bad"} and "Organization" in h["jsonld_types"]
    assert h["generic_anchor_links"] == 1 and h["canonical_raw"] == ["/"]
    pages_, children, err = crawl.parse_sitemap(b"<urlset><url><loc>https://a.test/</loc></urlset")
    assert err and err.startswith("Not valid XML")


# ------------------------------------------------------------------ full audit on the broken site
async def test_audit_finds_deliberate_problems(client, org):
    run = await run_audit(client, org)
    assert run["status"] == "done", (run.get("error"), STATE.get("run_error"))
    found = {i["id"]: i for i in run["issues"] if i["count"]}
    expected = {
        "http_4xx", "http_5xx", "broken_internal_link", "broken_external_link", "internal_link_to_redirect",
        "redirect_chain", "redirect_loop", "temporary_redirect", "title_too_short", "title_duplicate", "desc_missing",
        "h1_missing", "duplicate_content", "img_missing_alt", "img_large", "broken_image", "mixed_content",
        "insecure_form", "viewport_missing", "canonical_broken", "canonical_relative", "jsonld_invalid", "noindex_page",
        "sitemap_noindex", "sitemap_broken_urls", "sitemap_redirects", "sitemap_blocked", "sitemap_future_lastmod",
        "orphan_page", "deep_page", "blocked_by_robots", "robots_invalid", "ai_search_blocked", "ai_training_blocked",
        "llms_missing", "noai_meta", "url_uppercase", "url_underscore", "url_params", "http_no_https_redirect",
        "www_duplicate", "hreflang_invalid", "hreflang_no_return", "hreflang_no_xdefault", "heading_skip", "lang_missing",
        "generic_anchor", "not_compressed", "no_hsts", "cwv_lcp_poor", "cwv_cls_poor", "cwv_inp_poor", "crux_failing",
        "psi_perf_low", "tbt_high", "psi_bp_low",
    }
    missing = expected - set(found)
    assert not missing, missing
    # things that are fine on this site don't fire
    for ok in ("robots_missing", "sitemap_missing", "not_https", "org_schema_missing", "robots_blocks_all"):
        assert ok not in found, ok

    assert 0 < run["score"] < 90 and run["errors"] > 0 and run["warnings"] > 0 and run["notices"] > 0
    assert run["stats"]["previous_run_id"] is None and run["stats"]["new_issues"] == 0
    # robots.txt rules were obeyed for our own crawler
    assert not any("/private/x" in r and r.startswith("GET") for r in REQUESTS)
    ai = {b["bot"]: b["status"] for b in run["ai"]["bots"]}
    assert ai["GPTBot"] == "blocked" and ai["PerplexityBot"] == "blocked" and ai["ClaudeBot"] == "allowed"
    assert run["ai"]["llms_txt"]["found"] is False
    perf = run["performance"]["summary"]["mobile"]
    assert perf["measured"] >= 1 and perf["lab"]["lcp_ms"] == 5200 and perf["field"]["inp_ms"] == 650
    assert {c["strategy"] for c in STATE["psi_calls"]} == {"mobile", "desktop"} and "key" not in STATE["psi_calls"][0]
    cats = {c["id"] for c in run["categories"]}
    assert {"links", "redirects", "ai", "performance", "security"} <= cats

    # affected URLs drill-down
    r = await client.get(f"/site-audit/runs/{run['id']}/issues/broken_internal_link", headers=org["h"])
    body = r.json()
    assert r.status_code == 200 and body["check"]["fix"] and body["total"] >= 1
    targets = {lk["url"] for it in body["items"] for lk in it["detail"]["links"]}
    assert {SITE + "/missing", SITE + "/brochure.pdf", SITE + "/server-error"} <= targets
    chain = (await client.get(f"/site-audit/runs/{run['id']}/issues/redirect_chain", headers=org["h"])).json()
    assert chain["items"][0]["url"] == SITE + "/chain-start" and len(chain["items"][0]["detail"]["hops"]) == 3
    assert (await client.get(f"/site-audit/runs/{run['id']}/issues/nope", headers=org["h"])).status_code == 404

    # pages explorer + detail
    pg = (await client.get(f"/site-audit/runs/{run['id']}/pages", headers=org["h"], params={"status": "4xx"})).json()
    assert {p["url"] for p in pg["items"]} >= {SITE + "/missing", SITE + "/gone"}
    pg = (await client.get(f"/site-audit/runs/{run['id']}/pages", headers=org["h"],
                           params={"check": "orphan_page"})).json()
    assert [p["url"] for p in pg["items"]] == [SITE + "/orphan"]
    allp = (await client.get(f"/site-audit/runs/{run['id']}/pages", headers=org["h"], params={"sort": "depth", "limit": 200})).json()
    d3 = next(p for p in allp["items"] if p["url"] == SITE + "/d3")
    assert d3["depth"] == 4  # / → /products → /d1 → /d2 → /d3
    home = next(p for p in allp["items"] if p["url"] == SITE + "/")
    det = (await client.get(f"/site-audit/runs/{run['id']}/pages/{home['id']}", headers=org["h"])).json()
    assert det["data"]["facts"]["title"] and det["pagespeed"]["mobile"]["scores"]["performance"] == 42
    assert any(i["id"] == "hreflang_invalid" for i in det["issues"])

    # CSV export (formula-safe)
    csv_ = await client.get(f"/site-audit/runs/{run['id']}/export", headers=org["h"], params={"kind": "issues"})
    assert csv_.status_code == 200 and csv_.headers["content-type"].startswith("text/csv")
    assert "Broken internal links" in csv_.text and "How to fix" in csv_.text.splitlines()[0]
    j = (await client.get(f"/site-audit/runs/{run['id']}/export", headers=org["h"], params={"kind": "pages", "as_json": True})).json()
    assert j["filename"].endswith(".csv") and SITE + "/orphan" in j["content"]


async def test_second_run_new_and_fixed_compare_and_drop_alert(client, org):
    await client.put("/site-audit/settings", headers=org["h"], json={"drop_threshold": 1, "psi_pages": 0})
    first = await run_audit(client, org)
    assert first["status"] == "done" and first["performance"]["pages"] == []
    STATE["fixed_missing"] = True
    STATE["worse"] = True  # home page loses its title
    second = await run_audit(client, org)
    assert second["status"] == "done"
    assert second["stats"]["previous_run_id"] == first["id"]
    assert second["new_issues"] > 0 and second["fixed_issues"] > 0
    by = {i["id"]: i for i in second["issues"]}
    assert by["title_missing"]["new"] == 1
    assert by["http_4xx"]["fixed"] >= 1  # /missing works now
    assert second["score"] < first["score"]

    detail = (await client.get(f"/site-audit/runs/{second['id']}/issues/http_4xx", headers=org["h"])).json()
    assert SITE + "/missing" in detail["fixed_urls"]
    cmp_ = (await client.get("/site-audit/compare", headers=org["h"], params={"a": second["id"], "b": first["id"]})).json()
    assert cmp_["before"]["id"] == first["id"] and cmp_["detailed"]
    assert any(n["check"] == "title_missing" and n["url"] == SITE + "/" for n in cmp_["new"])
    assert any(f["check"] == "http_4xx" and f["url"] == SITE + "/missing" for f in cmp_["fixed"])

    ov = (await client.get("/site-audit/overview", headers=org["h"])).json()
    assert ov["latest"]["id"] == second["id"] and [t["id"] for t in ov["trend"]] == [first["id"], second["id"]]
    assert ov["next_run_at"] and ov["active"] is None

    async with tenant(org["id"]) as s:
        events = [n.event for n in (await s.scalars(select(Notification))).all()]
    assert events.count("siteaudit_ready") >= 2 and "siteaudit_drop" in events


async def test_pagespeed_quota_is_skipped_gracefully(client, org):
    STATE["psi_quota"] = True
    run = await run_audit(client, org)
    assert run["status"] == "done"  # the free audit never depends on PageSpeed
    assert run["performance"]["quota_reached"] is True
    mob = run["performance"]["summary"]["mobile"]
    assert mob["measured"] == 0 and "free" in mob["error"]
    # one try + one retry, then the remaining pages are skipped instead of hammering the API
    assert len(STATE["psi_calls"]) <= 2 + pagespeed.CONCURRENCY * 2
    assert not any(i["category"] == "performance" and i["id"].startswith(("psi_", "cwv_")) and i["count"]
                   for i in run["issues"])


async def test_include_exclude_and_page_limit(client, org):
    r = await client.put("/site-audit/settings", headers=org["h"],
                         json={"max_pages": 4, "exclude": ["/d*"], "psi_pages": 0, "check_external": False})
    assert r.status_code == 200 and r.json()["settings"]["max_pages"] == 4
    run = await run_audit(client, org)
    assert run["status"] == "done" and run["pages"] <= 5
    assert any(i["id"] == "crawl_truncated" and i["count"] for i in run["issues"])
    assert not any("/d1" in x for x in REQUESTS if x.startswith("GET"))
    assert not any("dead-external" in x for x in REQUESTS)
    bad = await client.put("/site-audit/settings", headers=org["h"], json={"start_url": "http://127.0.0.1/"})
    assert bad.status_code == 400
    assert (await client.put("/site-audit/settings", headers=org["h"], json={"run_time": "25:00"})).status_code == 422


async def test_unreachable_site_fails_with_friendly_message(client, admin):
    o = await new_org(client, website="https://problem-site.test/")
    await client.put("/site-audit/settings", headers=o["h"], json={"start_url": "https://down-site.test/"})
    run = await run_audit(client, o)
    assert run["status"] == "failed" and "HTTP 404" in run["error"]
    none = await new_org(client, website=None)
    r = await client.post("/site-audit/runs", headers=none["h"])
    assert r.status_code == 400 and "website" in r.json()["detail"]


async def test_weekly_schedule(client, org):
    assert await sa_mod.schedule_org(org["id"], datetime(2026, 10, 19, 1, 0, tzinfo=UTC)) is None  # untouched org
    await client.put("/site-audit/settings", headers=org["h"], json={"weekday": 0, "run_time": "06:00"})
    # Monday 2026-10-19 05:00 IST: too early; 06:30 IST: runs once that week
    assert await sa_mod.schedule_org(org["id"], datetime(2026, 10, 18, 23, 30, tzinfo=UTC)) is None
    run = await sa_mod.schedule_org(org["id"], datetime(2026, 10, 19, 1, 0, tzinfo=UTC))
    assert run is not None and run.trigger == "scheduled" and run.run_key == "weekly:2026-W43"
    async with tenant(org["id"]) as s:
        r = await s.get(SiteAuditRun, run.id)
        r.status = "done"
        await s.commit()
    assert await sa_mod.schedule_org(org["id"], datetime(2026, 10, 19, 5, 0, tzinfo=UTC)) is None  # same week
    assert await sa_mod.schedule_org(org["id"], datetime(2026, 10, 20, 1, 0, tzinfo=UTC)) is None  # Tuesday
    await client.put("/site-audit/settings", headers=org["h"], json={"enabled": False})
    assert await sa_mod.schedule_org(org["id"], datetime(2026, 10, 26, 1, 0, tzinfo=UTC)) is None


# ------------------------------------------------------------------ permissions and tenancy
async def test_permissions_and_tenancy(client, org):
    run = await run_audit(client, org)
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=org["h"])).json()}
    email = f"viewer-{uuid.uuid4().hex[:6]}@audit-test.in"
    r = await client.post("/users", headers=org["h"], json={"email": email, "full_name": "Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    vh = auth(await login(client, email, r.json()["temporary_password"]))
    assert (await client.get("/site-audit/overview", headers=vh)).status_code == 200
    assert (await client.get(f"/site-audit/runs/{run['id']}/pages", headers=vh)).status_code == 200
    assert (await client.post("/site-audit/runs", headers=vh)).status_code == 403
    assert (await client.put("/site-audit/settings", headers=vh, json={"max_pages": 10})).status_code == 403
    assert (await client.delete(f"/site-audit/runs/{run['id']}", headers=vh)).status_code == 403

    other = await new_org(client)
    assert (await client.get(f"/site-audit/runs/{run['id']}", headers=other["h"])).status_code == 404
    assert (await client.get("/site-audit/overview", headers=other["h"])).json()["latest"] is None
    async with tenant(other["id"]) as s:
        assert (await s.scalars(select(SiteAuditIssue))).all() == []

    assert (await client.delete(f"/site-audit/runs/{run['id']}", headers=org["h"])).status_code == 204
    assert (await client.get(f"/site-audit/runs/{run['id']}", headers=org["h"])).status_code == 404
    assert json.dumps((await client.get("/site-audit/checks", headers=vh)).json())
