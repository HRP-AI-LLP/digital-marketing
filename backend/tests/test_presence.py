"""Social presence audit: profile discovery, collectors (mocked platforms), scores, suggestions, history and API.

All HTTP goes through httpx.MockTransport (app.services.social.net.client); the URL safety check and the LLM are patched.
The tests run in their own organization.
"""

import io
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select, update

import app.modules.presence as mod  # noqa: F401 - registers the handler
from app.core.crypto import encrypt_json
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import (
    CompanyEntity, CompanyProfile, KnowledgeSource, Organization, PresenceRun, PresenceSuggestion, ProviderConfig,
    SocialAccount,
)
from app.services import presence, presence_collect as pc, presence_profiles as pp, presence_score as ps
from app.services.jobs import run_inline
from app.services.llm import router as llm_router
from app.services.social import net
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"
ST: dict = {}
NOW = datetime.now(UTC)
LLM: dict = {"calls": 0}


def _png(color: tuple[int, int, int], half: bool = False) -> bytes:
    img = Image.new("RGB", (64, 64), color)
    if half:
        for x in range(32):
            for y in range(64):
                img.putpixel((x, y), (255, 255, 255))
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


LOGO = _png((10, 10, 10), half=True)
OTHER = _png((200, 30, 30))


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def handler(request: httpx.Request) -> httpx.Response:
    url, path, p = str(request.url), request.url.path, request.url.params
    if "cdn.example" in url:
        return httpx.Response(200, content=OTHER if "odd" in path else LOGO, headers={"content-type": "image/png"})
    if "graph.facebook.com" in url:
        fields = p.get("fields") or ""
        assert p.get("access_token") == "igtok"
        if fields.startswith("business_discovery"):
            assert "username(hrpai.careers)" in fields
            media = [{"timestamp": (NOW - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S+0000"), "like_count": 4,
                      "comments_count": 1, "media_type": "IMAGE"} for d in (2, 9, 16)]
            return httpx.Response(200, json={"business_discovery": {
                "username": "hrpai.careers", "name": "HRP AI Careers", "biography": "Jobs at HRP AI", "website": None,
                "profile_picture_url": "https://cdn.example/logo.png", "followers_count": 150, "media_count": 40,
                "media": {"data": media}}, "id": "ig1"})
        if path.endswith("/ig1"):
            media = []
            for i in range(10):
                kind = [("VIDEO", "REELS"), ("CAROUSEL_ALBUM", "FEED"), ("IMAGE", "FEED")][i % 3]
                media.append({"timestamp": (NOW - timedelta(days=1 + i * 3)).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                              "like_count": 30, "comments_count": 5, "media_type": kind[0], "media_product_type": kind[1]})
            return httpx.Response(200, json={
                "id": "ig1", "username": "hrpai", "name": "HRP AI", "biography": "HRMS and payroll software for Indian teams.",
                "website": "https://hrpai.in", "profile_picture_url": "https://cdn.example/logo.png", "followers_count": 900,
                "follows_count": 10, "media_count": 120, "media": {"data": media}})
    if "googleapis.com/youtube/v3" in url:
        assert p.get("key") == "ytkey"
        if path.endswith("/channels"):
            if p.get("forHandle") != "@hrpai":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(200, json={"items": [{
                "id": "UChrpai000000000000000000", "snippet": {"title": "HRP AI", "customUrl": "@hrpai",
                                                               "description": "HRMS walkthroughs. Visit https://hrpai.in",
                                                               "thumbnails": {"high": {"url": "https://cdn.example/logo.png"}}},
                "statistics": {"subscriberCount": "800", "videoCount": "30", "viewCount": "50000", "hiddenSubscriberCount": False},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUhrpai"}},
                "brandingSettings": {"image": {"bannerExternalUrl": "https://yt.example/banner"}}}]})
        if path.endswith("/playlistItems"):
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": f"v{i}", "videoPublishedAt": ago(d)}}
                                                       for i, d in enumerate((3, 10, 20, 40))]})
        if path.endswith("/videos"):
            durs = ["PT45S", "PT12M", "PT58S", "PT8M3S"]
            return httpx.Response(200, json={"items": [{"id": f"v{i}", "statistics": {"likeCount": "20", "commentCount": "4"},
                                                        "contentDetails": {"duration": durs[i]}} for i in range(4)]})
    if "api.github.com" in url:
        if path == "/users/hrpai":
            return httpx.Response(200, json={"login": "hrpai", "id": 77, "type": "Organization", "name": "HRP AI",
                                             "blog": "hrpai.in", "avatar_url": "https://cdn.example/logo.png",
                                             "followers": 40, "public_repos": 12})
        if path == "/orgs/hrpai":
            return httpx.Response(200, json={"description": "Open-source RAG and HRMS tooling", "email": "dev@hrpai.in"})
        if path == "/users/hrpai/repos":
            return httpx.Response(200, json=[{"pushed_at": ago(d), "stargazers_count": 5, "forks_count": 1, "fork": False}
                                             for d in (1, 12, 25)] + [{"pushed_at": ago(1), "fork": True}])
    if "public.api.bsky.app" in url:
        if path.endswith("getProfile"):
            return httpx.Response(200, json={"did": "did:plc:hrp", "handle": "hrpai.bsky.social", "displayName": "HRP AI",
                                             "description": "AI for HR teams", "avatar": "https://cdn.example/odd.png",
                                             "followersCount": 60, "followsCount": 5, "postsCount": 30})
        if path.endswith("getAuthorFeed"):
            return httpx.Response(200, json={"feed": [
                {"post": {"likeCount": 2, "replyCount": 0, "repostCount": 1, "record": {"createdAt": ago(41)},
                          "embed": {"$type": "app.bsky.embed.images#view", "images": [{}]}}},
                {"reason": {"$type": "repost"}, "post": {"likeCount": 999, "record": {"createdAt": ago(1)}}}]})
    if "mastodon.social" in url:
        if path.endswith("/accounts/lookup"):
            return httpx.Response(200, json={"id": "9", "username": "hrpai", "display_name": "HRP AI",
                                             "note": "<p>HRMS for India</p>", "avatar": "https://mastodon.social/avatars/original/missing.png",
                                             "header": "https://mastodon.social/headers/original/missing.png",
                                             "fields": [{"name": "Web", "value": '<a href="https://hrpai.in">hrpai.in</a>'}],
                                             "followers_count": 12, "statuses_count": 4})
        if path.endswith("/accounts/9/statuses"):
            return httpx.Response(200, json=[{"created_at": ago(100), "favourites_count": 1, "media_attachments": []}])
    if "facebook.com" in url and path == "/hrpaipage":
        return httpx.Response(200, headers={"content-type": "text/html"}, content=(
            b'<html><head><title>HRP AI | Noida | Facebook</title><meta property="og:title" content="HRP AI">'
            b'<meta property="og:description" content="HRP AI. 1.2K likes. HRMS software for growing teams.">'
            b'<meta property="og:image" content="https://cdn.example/logo.png"></head><body></body></html>'))
    if "linkedin.com" in url:
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=b"<html><head><title>Sign Up | LinkedIn</title></head></html>")
    if "x.com" in url:
        return httpx.Response(403, text="blocked")
    return httpx.Response(404, json={"error": {"message": f"unexpected {url}"}})


async def fake_json(session, task, prompt, *, system=None, temperature=0.5, max_tokens=2500):
    LLM["calls"] += 1
    assert task == "plan" and "Never invent numbers" in system
    assert "HRP AI" in prompt and '"followers": 900' in prompt
    return {"narrative": "HRP AI is strongest on Instagram with 900 followers. YouTube needs more uploads.",
            "tips": [
                {"title": "Turn your YouTube Shorts into Instagram Reels", "detail": "You already make Shorts.",
                 "platform": "instagram", "priority": "medium"},
                {"title": "Reach 50,000 followers by March", "detail": "Invented goal", "priority": "high"},
            ]}


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    monkeypatch.setattr(net, "client", lambda timeout=60.0: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    from app.services import crawler

    async def safe(url):
        return url

    monkeypatch.setattr(crawler, "check_public_url", safe)
    monkeypatch.setattr(llm_router, "complete_json", fake_json)


async def _new_org(client, name: str, email_: str) -> dict:
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(a), json={"name": name, "admin_email": email_, "admin_name": name})
    assert r.status_code == 201, r.text
    t = await login(client, email_, r.json()["temporary_password"])
    if t["user"]["must_change_password"]:
        r2 = await client.post("/auth/change-password", headers=auth(t),
                               json={"current_password": r.json()["temporary_password"], "new_password": "PresencePass123!"})
        t = r2.json()
    return {"tokens": t, "org_id": uuid.UUID(t["organization"]["id"])}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def org(client, admin):
    if "org" in ST:
        return ST["org"]
    o = await _new_org(client, "HRP AI LLP", "owner@presence-test.in")
    oid = o["org_id"]
    async with platform_session() as s:
        await s.execute(update(Organization).where(Organization.id == oid).values(
            website="https://hrpai.in", tagline="AI-powered HRMS",
            social_links={"linkedin": "https://www.linkedin.com/company/hrpai/", "instagram": "instagram.com/hrpai",
                          "youtube": "https://youtube.com/@hrpai", "github": "https://github.com/hrpai",
                          "x": "https://x.com/hrpai", "facebook": "https://www.facebook.com/hrpaipage",
                          "other": "https://bsky.app/profile/hrpai.bsky.social"}))
        await s.commit()
    async with SessionLocal() as s:
        set_tenant(s, oid)
        s.add(CompanyProfile(organization_id=oid, industries=["HR technology"], audiences=[], differentiators=[],
                             social_links={"mastodon": "https://mastodon.social/@hrpai",
                                           "instagram": "https://www.instagram.com/hrpai.careers/"}, data={}))
        s.add(CompanyEntity(organization_id=oid, type="product", name="Payroll", data={}))
        s.add(KnowledgeSource(organization_id=oid, kind="website", title="hrpai.in", url="https://hrpai.in", status="ready",
                              last_crawled_at=NOW, meta={"social_links": {"instagram": "https://www.instagram.com/hrpai/",
                                                                          "youtube": "https://www.youtube.com/@hrpai"}}))
        s.add(SocialAccount(organization_id=oid, platform="instagram", external_id="ig1", name="HRP AI", handle="hrpai",
                            profile_url="https://www.instagram.com/hrpai/", status="connected",
                            tokens_enc=encrypt_json({"access_token": "igtok"}), settings={}))
        s.add(ProviderConfig(organization_id=oid, kind="social_data", provider="youtube_data", name="YouTube",
                             secrets_enc=encrypt_json({"api_key": "ytkey"}), settings={}, enabled=True))
        await s.commit()
    ST["org"] = o
    return o


# ------------------------------------------------------------------ pure helpers
async def test_parse_profile_urls():
    r = pp.parse_profile_url("https://in.linkedin.com/company/HRPAI/about/")
    assert (r.platform, r.handle, r.kind) == ("linkedin", "hrpai", "company")
    assert pp.parse_profile_url("linkedin.com/in/prashant").kind == "person"
    assert pp.parse_profile_url("https://twitter.com/HRPAI").key == "x:hrpai"
    assert pp.parse_profile_url("https://x.com/intent/tweet?text=hi") is None
    assert pp.parse_profile_url("https://www.instagram.com/p/abc123/") is None
    assert pp.parse_profile_url("https://m.facebook.com/profile.php?id=123").handle == "123"
    assert pp.parse_profile_url("https://www.youtube.com/channel/UC1234567890123456789012").kind == "channel"
    assert pp.parse_profile_url("youtube.com/@HrpAI").handle == "@HrpAI"
    assert pp.parse_profile_url("https://www.youtube.com/watch?v=x") is None
    assert pp.parse_profile_url("https://www.threads.net/@hrpai").key == "threads:hrpai"
    assert pp.parse_profile_url("https://fosstodon.org/@hrpai").handle == "@hrpai@fosstodon.org"
    assert pp.parse_profile_url("https://bsky.app/profile/HRPAI.bsky.social").key == "bluesky:hrpai.bsky.social"
    assert pp.parse_profile_url("https://in.pinterest.com/hrpai/").platform == "pinterest"
    assert pp.parse_profile_url("https://www.crunchbase.com/organization/hrp").platform == "other"
    assert pp.parse_profile_url("not a url") is None


async def test_helpers():
    assert pc.parse_count("1.2K") == 1200 and pc.parse_count("12,345") == 12345 and pc.parse_count("2L") == 200000
    assert pc._duration("PT1M5S") == 65 and pc._duration("PT12M") == 720
    assert ps.brand_stems("HRP AI LLP", "hrpai.in") == ["hrpai", "hrpaillp"]
    assert ps.base_handle("mastodon", "@hrpai@mastodon.social") == "hrpai"
    assert ps.base_handle("youtube", "UChrpai00000000000000000") == ""
    assert ps.mentions_domain(["Visit https://www.hrpai.in/demo"], "hrpai.in")
    assert not ps.mentions_domain(["https://nothrpai.in"], "hrpai.in")
    assert presence.grounded("Post 3 times a week; you have 900 followers", {"900"})
    assert not presence.grounded("Reach 50,000 followers", {"900"})


def _ctx(**kw):
    base = dict(org_name="HRP AI LLP", brand=["hrpai"], domain="hrpai.in", keywords=["hrms", "payroll"],
                website_links=set(), website_crawled=True)
    base.update(kw)
    return ps.Context(**base)


async def test_scoring_rules_and_suggestions():
    ref = pp.ProfileRef("linkedin", "hrpai", "https://www.linkedin.com/company/hrpai/", "company", ["organization"])
    data = {**pc.blank(ref), "name": "HRP AI", "bio": "We build things for companies.", "website": "https://example.com",
            "avatar_url": "https://cdn.example/a.png", "has_cover": False, "followers": 400,
            "posts": [{"at": ago(41), "eng": 2, "format": "text"}], "source": "connected", "source_label": "LinkedIn API"}
    row = ps.score_profile(ref, data, _ctx())
    checks = {c["id"]: c["status"] for c in row["checks"]}
    assert checks["name_brand"] == "pass" and checks["bio_offerings"] == "fail" and checks["website_link"] == "fail"
    assert checks["cover"] == "fail" and checks["frequency"] == "fail" and checks["recency"] == "fail"
    assert row["days_since_last_post"] == 41 and row["posts_30d"] == 0
    assert row["parts"]["engagement"] == round(70 * (2 / 400) / 0.02)
    assert 0 < row["score"] < 60
    res = ps.overall([row], _ctx())
    cov = {c["platform"]: c["present"] for c in res["coverage"]}
    assert cov["linkedin"] and not cov["x"]
    assert next(p for p in res["parts"] if p["key"] == "coverage")["score"] == 30
    sugs = ps.suggestions([row], res, _ctx())
    titles = [s["title"] for s in sugs]
    assert "Add hrpai.in link to LinkedIn profile" in titles
    assert "Post 3 times a week on LinkedIn — last post 41 days ago" in titles
    assert "Claim the X handle @hrpai" in titles
    assert "Link your LinkedIn profile from hrpai.in" in titles
    assert all(s["priority"] in ("high", "medium", "low") for s in sugs)
    act = next(s for s in sugs if s["key"].endswith(":activity"))
    assert act["action"]["to"] == "/settings/accounts"  # not connected as an account → connect first

    # Limited data never lowers the score: unknown checks are left out.
    lim = {**pc.blank(ref), "source": "limited", "source_label": "No data (the profile couldn't be read)"}
    row2 = ps.score_profile(ref, lim, _ctx())
    assert row2["score"] is None or row2["score"] >= 50
    assert all(c["status"] == "unknown" for c in row2["checks"] if c["id"] != "name_brand")


# ------------------------------------------------------------------ full audit
async def test_audit_end_to_end(client, org):
    oid = org["org_id"]
    res = await run_inline("presence.audit", oid, {"trigger": "manual"})
    assert res["score"] is not None and res["profiles"] >= 8
    async with SessionLocal() as s:
        set_tenant(s, oid)
        run = await s.scalar(select(PresenceRun).order_by(PresenceRun.created_at.desc()))
        sugs = (await s.scalars(select(PresenceSuggestion).where(PresenceSuggestion.run_id == run.id))).all()
    by = {p["key"]: p for p in run.profiles}
    assert set(by) >= {"instagram:hrpai", "instagram:hrpai.careers", "youtube:@hrpai", "github:hrpai", "bluesky:hrpai.bsky.social",
                       "mastodon:@hrpai@mastodon.social", "facebook:hrpaipage", "x:hrpai", "linkedin:hrpai"}
    ig = by["instagram:hrpai"]
    assert ig["source"] == "connected" and set(ig["sources"]) >= {"organization", "website", "connected"}
    assert ig["followers"] == 900 and ig["posts_30d"] == 10 and ig["linked_from_website"] is True
    assert ig["formats"] == {"reel": 4, "carousel": 3, "image": 3}
    assert {c["id"]: c["status"] for c in ig["checks"]}["website_link"] == "pass"
    assert {c["id"]: c["status"] for c in ig["checks"]}["bio_offerings"] == "pass"
    assert ig["parts"]["content_mix"] == 100

    disc = by["instagram:hrpai.careers"]
    assert disc["source"] == "public" and "Business Discovery" in disc["source_label"] and disc["followers"] == 150

    yt = by["youtube:@hrpai"]
    assert yt["source"] == "public" and yt["followers"] == 800 and yt["formats"] == {"short": 2, "video": 2}
    assert yt["posts_30d"] == 3 and yt["linked_from_website"] is True

    gh = by["github:hrpai"]
    assert gh["source"] == "public" and gh["posts_30d"] == 3  # forks are ignored
    assert {c["id"]: c["status"] for c in gh["checks"]}["contact"] == "pass"
    assert {c["id"]: c["status"] for c in gh["checks"]}["website_link"] == "pass"

    bs = by["bluesky:hrpai.bsky.social"]
    assert bs["source"] == "public" and bs["days_since_last_post"] == 41 and bs["posts_30d"] == 0

    ma = by["mastodon:@hrpai@mastodon.social"]
    assert ma["source"] == "public" and ma["has_avatar"] is False
    assert {c["id"]: c["status"] for c in ma["checks"]}["website_link"] == "pass"

    fb = by["facebook:hrpaipage"]
    assert fb["source"] == "limited" and fb["followers"] == 1200 and "Public page" in fb["source_label"]

    x = by["x:hrpai"]
    assert x["source"] == "limited" and x["score"] is None or x["source"] == "limited"
    assert any("optional" in n.lower() or "paid" in n.lower() for n in x["notes"])
    li = by["linkedin:hrpai"]
    assert li["source"] == "limited" and any("connect your LinkedIn company page" in n for n in li["notes"])

    parts = {p["key"]: p for p in run.parts}
    assert parts["coverage"]["score"] == 100
    assert parts["cross_linking"]["score"] is not None and parts["brand"]["score"] is not None
    assert "profile pictures" in parts["brand"]["detail"]

    titles = {s.title: s for s in sugs}
    assert "Add hrpai.in link to Bluesky bio" in titles
    assert any(t.startswith("Post 4 times a week on Bluesky — last post 41 days ago") for t in titles)
    assert "Connect LinkedIn for full analysis" in titles
    assert titles["Connect LinkedIn for full analysis"].action == {"to": "/settings/accounts", "label": "Connect the account"}
    assert "Use the same logo on every profile" in titles
    # two Instagram profiles → suggestions name the handle; the careers one came from Company intelligence only
    assert "Add your Instagram (hrpai.careers) link in Settings → Organization" in titles
    assert "Add hrpai.in link to Instagram (hrpai.careers) bio" in titles
    assert by["linkedin:hrpai"]["score"] is None  # a sign-in wall gives no fair score
    assert "Turn your YouTube Shorts into Instagram Reels" in titles and titles["Turn your YouTube Shorts into Instagram Reels"].source == "ai"
    assert not any("50,000" in t for t in titles)  # invented number dropped
    assert run.narrative_source == "ai" and "900 followers" in run.narrative
    assert not any(p["source"] == "limited" and p["platform"] == "x" and p["score"] == 0 for p in run.profiles)
    ST["run1"] = run.id


async def test_api_done_toggle_history_and_rerun(client, org):
    h = auth(org["tokens"])
    r = await client.get("/presence", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["run"]["id"] == str(ST["run1"]) and d["can_manage"] and d["inputs"]["optional_sources"]["youtube_data"]
    assert "pinterest" in d["inputs"]["missing_org_links"]
    rule = next(s for s in d["suggestions"] if s["title"] == "Add hrpai.in link to Bluesky bio")
    tip = next(s for s in d["suggestions"] if s["source"] == "ai")
    for sid in (rule["id"], tip["id"]):
        r = await client.patch(f"/presence/suggestions/{sid}", headers=h, json={"status": "done"})
        assert r.status_code == 200 and r.json()["status"] == "done"
    assert (await client.patch(f"/presence/suggestions/{rule['id']}", headers=h, json={"status": "maybe"})).status_code == 422

    r = await client.post("/presence/run", headers=h)
    assert r.status_code == 202
    await run_inline("presence.audit", org["org_id"], {"trigger": "manual"})
    d = (await client.get("/presence", headers=h)).json()
    assert d["run"]["id"] != str(ST["run1"]) and len(d["history"]) >= 2
    assert d["run"]["summary"]["change"] == 0
    again = next(s for s in d["suggestions"] if s["title"] == "Add hrpai.in link to Bluesky bio")
    assert again["status"] == "open" and again["reopened"] is True  # still failing → back on the list
    tip2 = next(s for s in d["suggestions"] if s["key"] == tip["key"])
    assert tip2["status"] == "done"
    r = await client.get(f"/presence/runs/{ST['run1']}", headers=h)
    assert r.status_code == 200 and r.json()["run"]["score"] == d["run"]["score"]
    dash = (await client.get("/dashboard", headers=h)).json()
    assert dash["social_presence"]["score"] == d["run"]["score"] and dash["social_presence"]["change"] == 0


async def test_permissions(client, org):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    assert (await client.get("/presence", headers=auth(a))).status_code == 200  # Administrator gets presence.* automatically
    from app.core.permissions import DEFAULT_ROLES

    assert "presence.view" in DEFAULT_ROLES["Viewer"][1] and "presence.manage" not in DEFAULT_ROLES["Viewer"][1]


async def test_empty_org_still_scores(client, admin):
    o = await _new_org(client, "Nobody Social Co", "owner@nobody-social.in")
    res = await run_inline("presence.audit", o["org_id"], {})
    assert res["profiles"] == 0 and res["score"] == 0
    d = (await client.get("/presence", headers=auth(o["tokens"]))).json()
    titles = [s["title"] for s in d["suggestions"]]
    assert "Create a LinkedIn company page for Nobody Social Co" in titles
    assert "couldn't find any social profiles" in d["run"]["narrative"]
