"""Growth trends, competitor benchmarks, health score, growth tips and reports.

All platform HTTP goes through httpx.MockTransport (app.services.social.net.client and analytics.http_client);
the LLM, Google token, URL safety check and SMTP are patched. The tests run in their own organization so other
test modules' data doesn't change the numbers.
"""

import email
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, update

import app.modules.analytics_growth as mod  # noqa: F401 - registers handlers
from app.core.config import get_settings
from app.core.crypto import encrypt_json
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import (
    AccountMetric, AnalyticsReport, Competitor, CompetitorMetric, Opportunity, Organization, Post, PostMetric,
    ProviderConfig, Recommendation, ReportSchedule, Schedule, SocialAccount,
)
from app.services import analytics, analytics_competitors as comp, analytics_growth as growth
from app.services import analytics_health as health, analytics_reports as reports, media_render, svg_charts
from app.services.jobs import run_inline
from app.services.llm import router as llm_router
from app.services.social import net
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"
ST: dict = {}
SENT: list = []


# ------------------------------------------------------------------ fake platforms
def platform_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    p = request.url.params
    path = request.url.path
    if "graph.facebook.com" in url:
        fields = p.get("fields") or ""
        if fields.startswith("business_discovery"):
            assert "username(rivalhr)" in fields
            now = datetime.now(UTC)
            media = [{"like_count": 40, "comments_count": 10, "media_type": "IMAGE",
                      "timestamp": (now - timedelta(days=i * 3)).strftime("%Y-%m-%dT%H:%M:%S+0000")} for i in range(8)]
            return httpx.Response(200, json={"business_discovery": {"followers_count": 5000, "media_count": 400,
                                                                    "media": {"data": media}}, "id": "ig1"})
        if path.endswith("/page1"):
            return httpx.Response(200, json={"followers_count": 1500, "fan_count": 1490, "id": "page1"})
        if path.endswith("/page1/insights"):
            m = p.get("metric")
            if m == "page_post_engagements":
                return httpx.Response(400, json={"error": {"code": 100, "message": "(#100) The value must be a valid insights metric"}})
            return httpx.Response(200, json={"data": [{"name": m, "values": [{"value": 10}, {"value": 120 if m != "page_views_total" else 7}]}]})
        if path.endswith("/ig1"):
            return httpx.Response(200, json={"followers_count": 820, "follows_count": 50, "media_count": 120})
        if path.endswith("/ig1/insights"):
            return httpx.Response(200, json={"data": [{"name": p.get("metric"), "total_value": {"value": 33}}]})
    if "graph.threads.net" in url and path.endswith("/threads_insights"):
        return httpx.Response(200, json={"data": [
            {"name": "views", "values": [{"value": 100}, {"value": 50}]},
            {"name": "likes", "total_value": {"value": 9}}, {"name": "replies", "total_value": {"value": 2}},
            {"name": "reposts", "total_value": {"value": 1}}, {"name": "quotes", "total_value": {"value": 0}},
            {"name": "followers_count", "total_value": {"value": 210}}]})
    if "youtube/v3/channels" in url:
        assert request.headers["Authorization"] == "Bearer gtok"
        if p.get("id") == "UCours":
            return httpx.Response(200, json={"items": [{"id": "UCours", "statistics": {
                "subscriberCount": "300", "videoCount": "40", "viewCount": "9000", "hiddenSubscriberCount": False}}]})
        if p.get("forHandle") == "@rivaltube":
            return httpx.Response(200, json={"items": [{"id": "UCrival", "statistics": {"subscriberCount": "12000", "videoCount": "210"},
                                                        "contentDetails": {"relatedPlaylists": {"uploads": "UUrival"}}}]})
        return httpx.Response(200, json={"items": []})
    if "youtube/v3/playlistItems" in url:
        now = datetime.now(UTC)
        return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": f"v{i}", "videoPublishedAt":
                                                                       (now - timedelta(days=i * 12)).isoformat()}} for i in range(4)]})
    if "youtube/v3/videos" in url:
        return httpx.Response(200, json={"items": [{"id": f"v{i}", "statistics": {"likeCount": "90", "commentCount": "10"}} for i in range(4)]})
    if "api.pinterest.com/v5/user_account" in url:
        return httpx.Response(200, json={"username": "hrp", "follower_count": 75, "following_count": 3, "pin_count": 44,
                                         "monthly_views": 1800})
    if "public.api.bsky.app" in url:
        if path.endswith("getProfile"):
            if p.get("actor") == "did:plc:ours":
                return httpx.Response(200, json={"did": "did:plc:ours", "followersCount": 640, "followsCount": 20, "postsCount": 90})
            return httpx.Response(200, json={"did": "did:plc:rival", "followersCount": 900, "followsCount": 5, "postsCount": 300})
        if path.endswith("getAuthorFeed"):
            now = datetime.now(UTC)
            feed = [{"post": {"likeCount": 5, "replyCount": 1, "repostCount": 2, "quoteCount": 0,
                              "record": {"createdAt": (now - timedelta(days=i)).isoformat()}}} for i in range(5)]
            feed.append({"reason": {"$type": "repost"}, "post": {"likeCount": 999}})
            return httpx.Response(200, json={"feed": feed})
    if "mastodon.social" in url:
        if path.endswith("verify_credentials"):
            assert request.headers["Authorization"] == "Bearer mtok"
            return httpx.Response(200, json={"id": "1", "followers_count": 120, "following_count": 9, "statuses_count": 70})
        if path.endswith("/accounts/lookup"):
            return httpx.Response(200, json={"id": "77", "followers_count": 50, "statuses_count": 10})
        if path.endswith("/accounts/77/statuses"):
            return httpx.Response(200, json=[{"created_at": datetime.now(UTC).isoformat(), "favourites_count": 3,
                                              "replies_count": 0, "reblogs_count": 1}])
    if "api.linkedin.com/rest/networkSizes" in url:
        assert p.get("edgeType") == "COMPANY_FOLLOWED_BY_MEMBER"
        return httpx.Response(200, json={"firstDegreeSize": 2300})
    return httpx.Response(404, json={"error": {"message": f"unexpected {url}"}})


def pagespeed_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"id": str(request.url.params.get("url")), "lighthouseResult": {
        "finalUrl": "https://rival.example/", "categories": {"performance": {"score": 0.55}, "seo": {"score": 0.9}}, "audits": {}}})


async def fake_json(session, task, prompt, *, system=None, temperature=0.5, max_tokens=2500):
    assert task == "plan" and "Recommend how to improve" in prompt
    facts = json.loads(prompt.split("\n\n", 1)[1])
    sid = facts["schedules"][0]["id"]
    acc = next(a for a in facts["accounts"] if a["platform"] == "facebook_page")["id"]
    return {"recommendations": [
        {"title": "Post at 18:00 on weekdays", "why": "Evening posts get 6.1% engagement.", "action": "Add 18:00.",
         "impact": "high", "effort": "low", "category": "timing",
         "one_click": {"type": "add_schedule_times", "params": {"schedule_id": sid, "times": ["18:00", "bad"]}}},
        {"title": "Use more carousels", "why": "Carousels beat text.", "action": "Switch the mix.", "impact": "medium",
         "effort": "low", "category": "content_mix",
         "one_click": {"type": "set_post_mix", "params": {"schedule_id": sid, "post_types": {"carousel": 2, "hologram": 3}}}},
        {"title": "Draft a post about onboarding", "why": "Onboarding posts did best.", "action": "Draft one.",
         "impact": "medium", "effort": "low", "category": "engagement",
         "one_click": {"type": "draft_posts", "params": {"topic": "Onboarding checklists that work", "account_ids": [acc]}}},
        {"title": "Invalid action is dropped", "why": "x", "action": "y", "impact": "huge", "effort": "low",
         "one_click": {"type": "add_schedule_times", "params": {"schedule_id": str(uuid.uuid4()), "times": ["09:00"]}}},
        {"title": "Add topic ideas", "why": "Search demand.", "action": "Add them.", "impact": "low", "effort": "low",
         "one_click": {"type": "add_topic_ideas", "params": {"ideas": [{"title": "HRMS for startups", "angle": "Costs"}]}}},
    ]}


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    monkeypatch.setattr(net, "client", lambda timeout=60.0: httpx.AsyncClient(transport=httpx.MockTransport(platform_handler)))
    monkeypatch.setattr(analytics, "http_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(pagespeed_handler)))
    from app.services import crawler
    from app.services.social import google

    async def gtok(session):
        return "gtok"

    async def safe(url):
        return url

    monkeypatch.setattr(google, "google_access_token", gtok)
    monkeypatch.setattr(crawler, "check_public_url", safe)
    monkeypatch.setattr(llm_router, "complete_json", fake_json)

    async def fake_send(msg, **kw):
        SENT.append((msg, kw))

    import aiosmtplib
    monkeypatch.setattr(aiosmtplib, "send", fake_send)


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def env(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage"))
    yield
    s.storage_dir = old
    await media_render.shutdown()


async def _new_org(client, name: str, email_: str) -> dict:
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(a), json={"name": name, "admin_email": email_, "admin_name": name})
    assert r.status_code == 201, r.text
    t = await login(client, email_, r.json()["temporary_password"])
    if t["user"]["must_change_password"]:
        r2 = await client.post("/auth/change-password", headers=auth(t),
                               json={"current_password": r.json()["temporary_password"], "new_password": "GrowthPass123!"})
        t = r2.json()
    return {"tokens": t, "org_id": uuid.UUID(t["organization"]["id"])}


def _acc(org_id, platform, ext, name, tokens=None, **kw) -> SocialAccount:
    return SocialAccount(organization_id=org_id, platform=platform, external_id=ext, name=name, status="connected",
                         tokens_enc=encrypt_json(tokens) if tokens else None, settings=kw.pop("settings", {}), **kw)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def org(client, admin):
    if "org" in ST:
        return ST["org"]
    o = await _new_org(client, "Growth Test Co", "owner@growth-test.in")
    oid = o["org_id"]
    async with platform_session() as s:
        await s.execute(update(Organization).where(Organization.id == oid).values(website="https://growth-test.in"))
        await s.commit()
    today = datetime.now(growth.org_zone(None)).date()
    now = datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, oid)
        accs = {
            "fb": _acc(oid, "facebook_page", "page1", "Growth FB", {"access_token": "fbtok"}),
            "ig": _acc(oid, "instagram", "ig1", "Growth IG", {"access_token": "igtok"}),
            "th": _acc(oid, "threads", "th1", "Growth Threads", {"access_token": "thtok"}),
            "yt": _acc(oid, "youtube", "UCours", "Growth YT"),
            "pin": _acc(oid, "pinterest", "pin1", "Growth Pins", {"access_token": "ptok"}),
            "bs": _acc(oid, "bluesky", "did:plc:ours", "Growth Bsky", {"access_jwt": "x"}, handle="growth.bsky.social"),
            "ma": _acc(oid, "mastodon", "mastodon.social:1", "Growth Masto", {"access_token": "mtok", "instance": "https://mastodon.social"}),
            "li": _acc(oid, "linkedin_org", "urn:li:organization:5", "Growth LI", {"access_token": "ltok"}),
            "lp": _acc(oid, "linkedin_person", "urn:li:person:9", "Growth Person", {"access_token": "ltok"}),
        }
        s.add_all(accs.values())
        await s.flush()
        # 40 days of Facebook follower history: 1000 → 1390 (10 a day), up to yesterday
        for i in range(40, 0, -1):
            s.add(AccountMetric(organization_id=oid, social_account_id=accs["fb"].id, platform="facebook_page",
                                day=today - timedelta(days=i), followers=1400 - i * 10 + 0, raw={}, notes=[]))
        sched = Schedule(organization_id=oid, social_account_id=accs["fb"].id, name="FB weekdays", days_of_week=[0, 1, 2, 3, 4, 5, 6],
                         times=["10:00"], post_types={"text": 1, "image": 1}, mode="approve_first")
        s.add(sched)
        await s.flush()
        sched.created_at = now - timedelta(days=40)
        for i in range(6):
            p = Post(organization_id=oid, social_account_id=accs["fb"].id, platform="facebook_page",
                     post_type="image" if i % 2 else "text", status="published", title=f"Growth post {i}",
                     published_at=now - timedelta(days=2 + i * 3), hashtags=[], media_ids=[], qa={}, meta={})
            s.add(p)
            await s.flush()
            s.add(PostMetric(organization_id=oid, post_id=p.id, impressions=1000, likes=30 + i, comments=5, shares=2, clicks=3))
        rival = Competitor(organization_id=oid, name="Rival HR", website="https://rival.example/", domain="rival.example",
                           status="confirmed", data={})
        s.add(rival)
        await s.commit()
        ST.update(org=o, oid=oid, accs={k: v.id for k, v in accs.items()}, schedule_id=sched.id, rival_id=rival.id)
    return o


# ------------------------------------------------------------------ pure helpers
async def test_insight_value_and_rates():
    assert growth.insight_value({"data": [{"values": [{"value": 1}, {"value": 5}]}]}) == 5
    assert growth.insight_value({"data": [{"total_value": {"value": 7}}]}) == 7
    assert growth.insight_value({"data": []}) is None
    assert growth.growth_rate(110, 100) == 10.0 and growth.growth_rate(5, 0) is None
    assert health.combine([{"score": 80, "weight": 20}, {"score": None, "weight": 50}, {"score": 40, "weight": 20}]) == 60
    assert health.grade(85) == "Strong" and health.grade(None) == "Not enough data yet"


async def test_clean_handles():
    assert comp.clean_handle("instagram", "https://www.instagram.com/rival.hr/") == "rival.hr"
    assert comp.clean_handle("youtube", "https://youtube.com/@RivalTube") == "@RivalTube"
    assert comp.clean_handle("youtube", "youtube.com/channel/UC1234567890123456789012") == "UC1234567890123456789012"
    assert comp.clean_handle("bluesky", "https://bsky.app/profile/Rival.bsky.social") == "rival.bsky.social"
    assert comp.clean_handle("mastodon", "mastodon.social/@rival") == "@rival@mastodon.social"
    assert comp.clean_handle("mastodon", "") is None
    with pytest.raises(comp.HandleError):
        comp.clean_handle("instagram", "not a name!")
    with pytest.raises(comp.HandleError):
        comp.clean_handle("mastodon", "@rival")


async def test_next_run_and_periods():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Kolkata")
    s = ReportSchedule(frequency="weekly", weekday=0, month_day=1, time="09:00")
    after = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)  # a Wednesday
    nxt = reports.next_run(s, tz, after)
    assert nxt.astimezone(tz).weekday() == 0 and nxt.astimezone(tz).strftime("%H:%M") == "09:00" and nxt > after
    s.frequency, s.month_day = "monthly", 3
    assert reports.next_run(s, tz, after).astimezone(tz).date() == date(2026, 11, 3)
    assert reports.default_period("weekly", date(2026, 10, 16)) == (date(2026, 10, 9), date(2026, 10, 15))
    assert reports.default_period("monthly", date(2026, 10, 16)) == (date(2026, 9, 1), date(2026, 9, 30))


async def test_svg_charts_render():
    out = svg_charts.line_chart(["a", "b", "c"], [{"name": "x", "values": [1, None, 3]}])
    assert out.startswith("<svg") and "circle" in out  # the lone points around a gap become dots
    assert "No posts" in svg_charts.bar_chart([], [])
    assert "out of 100" in svg_charts.score_ring(72)


# ------------------------------------------------------------------ collectors
async def test_collect_accounts_per_platform(client, org):
    r = await run_inline("analytics.collect", ST["oid"], {"scope": "accounts"})
    acc = r["accounts"]
    assert not acc["failed"], acc["failed"]
    assert {x["account"] for x in acc["skipped"]} == {"Growth Person"}
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        rows = {m.platform: m for m in (await s.scalars(select(AccountMetric).where(AccountMetric.day == growth.local_today(None)))).all()}
    assert rows["facebook_page"].followers == 1500 and rows["facebook_page"].reach == 120
    assert rows["facebook_page"].profile_views == 7 and rows["facebook_page"].engagement is None
    assert any("page_post_engagements" in n for n in rows["facebook_page"].notes)
    assert (rows["instagram"].followers, rows["instagram"].posts_count, rows["instagram"].reach) == (820, 120, 33)
    assert (rows["threads"].followers, rows["threads"].impressions, rows["threads"].engagement) == (210, 150, 12)
    assert (rows["youtube"].followers, rows["youtube"].views) == (300, 9000)
    assert (rows["pinterest"].followers, rows["pinterest"].views) == (75, 1800)
    assert (rows["bluesky"].followers, rows["bluesky"].posts_count) == (640, 90)
    assert (rows["mastodon"].followers, rows["mastodon"].posts_count) == (120, 70)
    assert rows["linkedin_org"].followers == 2300
    assert "linkedin_person" not in rows
    assert r["score"] is not None
    # a second run on the same day updates instead of duplicating
    await run_inline("analytics.collect", ST["oid"], {"scope": "accounts"})
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        n = len((await s.scalars(select(AccountMetric).where(AccountMetric.platform == "facebook_page",
                                                            AccountMetric.day == growth.local_today(None)))).all())
    assert n == 1


async def test_growth_view(client, org):
    t = ST["org"]["tokens"]
    r = await client.get("/analytics/growth?days=30", headers=auth(t))
    assert r.status_code == 200, r.text
    d = r.json()
    fb = next(a for a in d["accounts"] if a["platform"] == "facebook_page")
    # today 1500 (collected), 30 days ago 1100 (seeded 1400 - 30*10)
    assert fb["deltas"]["30"] == {"net": 400, "rate": 36.36}
    assert fb["deltas"]["7"]["net"] == 1500 - 1330
    person = next(a for a in d["accounts"] if a["platform"] == "linkedin_person")
    assert person["supported"] is False and "personal profiles" in person["unsupported_reason"]
    assert d["windows"]["30"]["current"]["posts"] == 6
    assert d["windows"]["30"]["current"]["engagement_rate"] == pytest.approx(sum(30 + i + 10 for i in range(6)) / 6000, abs=1e-4)
    assert sum(w["posts"] for w in d["weeks"]) == 6
    assert d["followers_series"][-1]["followers"] == 1500 + 820 + 210 + 300 + 75 + 640 + 120 + 2300
    fbp = next(p for p in d["platforms"] if p["platform"] == "facebook_page")
    assert fbp["posts"] == 6 and fbp["followers"] == 1500


# ------------------------------------------------------------------ competitors
async def test_competitor_handles_and_benchmark(client, org):
    t = ST["org"]["tokens"]
    rid = ST["rival_id"]
    r = await client.put(f"/analytics/competitors/{rid}/handles", headers=auth(t), json={"instagram": "rival"})
    assert r.status_code == 200
    r = await client.put(f"/analytics/competitors/{rid}/handles", headers=auth(t),
                         json={"instagram": "https://instagram.com/rivalhr", "youtube": "@rivaltube",
                               "bluesky": "rival.bsky.social", "mastodon": "@rival@mastodon.social"})
    assert r.status_code == 200, r.text
    r = await client.put(f"/analytics/competitors/{rid}/handles", headers=auth(t), json={"mastodon": "nope"})
    assert r.status_code == 400 and "Mastodon" in r.json()["detail"]
    r = await client.put(f"/analytics/competitors/{rid}/handles", headers=auth(t),
                         json={"instagram": "rivalhr", "youtube": "@rivaltube", "bluesky": "rival.bsky.social",
                               "mastodon": "@rival@mastodon.social"})
    out = await run_inline("analytics.collect", ST["oid"], {"scope": "competitors", "websites": True})
    assert out["competitors"]["collected"] == 4 and not out["competitors"]["failed"], out
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        ms = {m.channel: m for m in (await s.scalars(select(CompetitorMetric))).all()}
    assert (ms["instagram"].followers, ms["instagram"].avg_engagement, ms["instagram"].posts_30d) == (5000, 50.0, 8)
    assert (ms["youtube"].followers, ms["youtube"].avg_engagement, ms["youtube"].posts_30d) == (12000, 100.0, 3)
    assert (ms["bluesky"].followers, ms["bluesky"].avg_engagement, ms["bluesky"].posts_30d) == (900, 8.0, 5)
    assert ms["mastodon"].followers == 50 and ms["website"].score == 55

    d = (await client.get("/analytics/competitors", headers=auth(t))).json()
    ig = d["channels"]["instagram"]
    names = [r["name"] for r in ig["rows"]]
    assert names[0] == "Growth Test Co" and "Rival HR" in names
    assert any(x["metric"] == "followers" for x in ig["insights"]["lag"])  # 820 vs 5000
    masto = d["channels"]["mastodon"]["insights"]
    assert any(x["metric"] == "followers" for x in masto["lead"])  # 120 vs 50
    sov = {x["name"]: x for x in d["channels"]["instagram"]["share_of_voice"]}
    assert sov["Rival HR"]["engagement_share"] == 1.0  # we have no Instagram posts with metrics
    assert d["can_instagram"] is True


async def test_business_discovery_needs_our_instagram(client, org):
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        await s.execute(update(SocialAccount).where(SocialAccount.platform == "instagram").values(status="expired"))
        await s.commit()
    try:
        async with SessionLocal() as s:
            set_tenant(s, ST["oid"])
            from app.services.social.common import PublishError

            async with net.client() as c:
                with pytest.raises(PublishError, match="Connect your Instagram"):
                    await comp.ig_discovery(s, c, "rivalhr")
    finally:
        async with SessionLocal() as s:
            set_tenant(s, ST["oid"])
            await s.execute(update(SocialAccount).where(SocialAccount.platform == "instagram").values(status="connected"))
            await s.commit()


# ------------------------------------------------------------------ health score
async def test_health_score_parts(client, org):
    d = (await client.get("/analytics/health", headers=auth(ST["org"]["tokens"]))).json()
    parts = {p["key"]: p for p in d["parts"]}
    assert set(parts) == set(health.WEIGHTS)
    # 6 posts vs 28 expected schedule slots
    assert parts["consistency"]["value"] == "6 of 28 posts" and parts["consistency"]["score"] == round(6 / 28 * 100)
    er = sum(30 + i + 10 for i in range(6)) / 6000
    assert parts["engagement"]["score"] == min(100, round(er / health.TARGET_ER * 100))
    assert parts["content_mix"]["score"] == round(2 / 3 * 60 + 0.5 / 0.6 * 40)
    assert parts["growth"]["score"] is not None and parts["competitors"]["score"] is not None
    assert parts["website"]["score"] is None  # no PageSpeed snapshot or audit for this org
    assert all(p["how"] for p in d["parts"])
    assert 0 <= d["score"] <= 100 and d["grade"]
    assert d["history"] and d["history"][-1]["score"] is not None  # saved by the collect job
    s = (await client.get("/analytics/health/summary", headers=auth(ST["org"]["tokens"]))).json()
    assert s["score"] is not None


# ------------------------------------------------------------------ recommendations
async def test_ai_recommendations_and_one_click(client, org):
    t = ST["org"]["tokens"]
    r = await run_inline("analytics.advisor", ST["oid"], {})
    assert r["source"] == "ai" and r["count"] == 5
    items = (await client.get("/analytics/recommendations", headers=auth(t))).json()["items"]
    by = {i["title"]: i for i in items}
    assert by["Post at 18:00 on weekdays"]["one_click"]["params"]["times"] == ["18:00"]
    assert by["Use more carousels"]["one_click"]["params"]["post_types"] == {"carousel": 2.0}
    assert by["Invalid action is dropped"]["one_click"] is None and by["Invalid action is dropped"]["impact"] == "medium"

    r = await client.post(f"/analytics/recommendations/{by['Post at 18:00 on weekdays']['id']}/apply", headers=auth(t))
    assert r.status_code == 200, r.text
    assert r.json()["recommendation"]["status"] == "done"
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        sched = await s.get(Schedule, ST["schedule_id"])
        assert sched.times == ["10:00", "18:00"]
    r = await client.post(f"/analytics/recommendations/{by['Post at 18:00 on weekdays']['id']}/apply", headers=auth(t))
    assert r.status_code == 409

    r = await client.post(f"/analytics/recommendations/{by['Use more carousels']['id']}/apply", headers=auth(t))
    assert r.status_code == 200, r.text
    r = await client.post(f"/analytics/recommendations/{by['Draft a post about onboarding']['id']}/apply", headers=auth(t))
    assert r.status_code == 200, r.text and r.json()["job_ids"]
    r = await client.post(f"/analytics/recommendations/{by['Add topic ideas']['id']}/apply", headers=auth(t))
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        assert (await s.get(Schedule, ST["schedule_id"])).post_types == {"carousel": 2.0}
        assert await s.scalar(select(Opportunity).where(Opportunity.title == "HRMS for startups"))
        drafts = (await s.scalars(select(Post).where(Post.topic == "Onboarding checklists that work"))).all()
        assert len(drafts) == 1 and drafts[0].status == "planned"
    r = await client.post(f"/analytics/recommendations/{by['Invalid action is dropped']['id']}/apply", headers=auth(t))
    assert r.status_code == 400
    r = await client.patch(f"/analytics/recommendations/{by['Invalid action is dropped']['id']}", headers=auth(t),
                           json={"status": "dismissed"})
    assert r.json()["status"] == "dismissed"


async def test_rules_fallback_without_ai(client, org, monkeypatch):
    async def none(*a, **k):
        raise llm_router.NoProviderError("No AI text provider is set up.")

    monkeypatch.setattr(llm_router, "complete_json", none)
    r = await run_inline("analytics.advisor", ST["oid"], {})
    assert r["source"] == "rules" and "No AI model is set up" in r["message"] and r["count"] >= 1
    d = (await client.get("/analytics/recommendations?status=new", headers=auth(ST["org"]["tokens"]))).json()
    assert d["last_run"]["source"] == "rules" and all(i["source"] == "rules" for i in d["items"])
    titles = [i["title"] for i in d["items"]]
    assert any("schedule" in t.lower() for t in titles)  # accounts without a schedule get a create_schedule tip
    tip = next(i for i in d["items"] if i["one_click"] and i["one_click"]["type"] == "create_schedule")
    assert tip["one_click"]["params"]["times"] == ["10:00"]


# ------------------------------------------------------------------ reports
_RENDER: dict = {}


async def renderer_ok() -> None:
    if "ok" not in _RENDER:
        try:
            await media_render.render_png("<html><body>ok</body></html>", 64, 64)
            _RENDER["ok"], _RENDER["why"] = True, ""
        except Exception as e:  # noqa: BLE001
            _RENDER["ok"], _RENDER["why"] = False, str(e)
    if not _RENDER["ok"]:
        pytest.skip(f"Chromium/Playwright unavailable: {_RENDER['why'][:300]}")


async def test_report_html_has_sections(client, org):
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        o = await s.get(Organization, ST["oid"])
        today = growth.local_today(o)
        d = await reports.build_data(s, o, today - timedelta(days=29), today)
        html = reports.render_html(d, await reports.logo_data_uri(s))
    assert d["kpis"][2]["value"] == 6
    for text in ("Marketing report", "Audience growth", "Platform breakdown", "Top posts", "Marketing health",
                 "Competitor benchmark", "How to improve", "Rival HR"):
        assert text in html
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")  # nothing to fetch: offline render


async def test_report_pdf_email_and_download(client, org):
    await renderer_ok()
    t = ST["org"]["tokens"]
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        s.add(ProviderConfig(organization_id=ST["oid"], kind="smtp", provider="smtp", name="Mail",
                             secrets_enc=encrypt_json({"host": "smtp.test", "port": 587, "username": "u", "password": "p",
                                                       "from_email": "studio@growth-test.in"})))
        await s.commit()
    r = await client.post("/analytics/reports", headers=auth(t), json={"period": "30", "recipients": ["boss@growth-test.in"]})
    assert r.status_code == 202, r.text
    rid = r.json()["report"]["id"]
    SENT.clear()
    out = await run_inline("analytics.report", ST["oid"], {"report_id": rid})
    assert out["status"] == "ready" and out["emailed"], out
    msg, kw = SENT[0]
    assert msg["To"] == "boss@growth-test.in" and kw["hostname"] == "smtp.test"
    parsed = email.message_from_bytes(msg.as_bytes())
    atts = [p for p in parsed.walk() if p.get_content_disposition() == "attachment"]
    assert atts and atts[0].get_filename().endswith(".pdf") and atts[0].get_payload(decode=True)[:4] == b"%PDF"
    rep = (await client.get(f"/analytics/reports/{rid}", headers=auth(t))).json()
    assert rep["status"] == "ready" and rep["sent_at"] and rep["download_url"]
    f = await client.get("http://test" + rep["download_url"])
    assert f.status_code == 200 and f.content[:4] == b"%PDF" and f.headers["content-type"] == "application/pdf"
    bad = rep["download_url"].replace("&s=", "&s=0")
    assert (await client.get("http://test" + bad)).status_code == 403
    ST["report_id"] = rid
    ST["pdf"] = f.content


async def test_report_email_without_smtp_is_explained(client, org):
    await renderer_ok()
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        await s.execute(update(ProviderConfig).where(ProviderConfig.kind == "smtp").values(enabled=False))
        await s.commit()
    try:
        r = await client.post("/analytics/reports", headers=auth(ST["org"]["tokens"]),
                              json={"period": "7", "recipients": ["a@growth-test.in"]})
        rid = r.json()["report"]["id"]
        out = await run_inline("analytics.report", ST["oid"], {"report_id": rid})
        assert out["status"] == "ready" and "SMTP" in out["email_error"]
    finally:
        async with SessionLocal() as s:
            set_tenant(s, ST["oid"])
            await s.execute(update(ProviderConfig).where(ProviderConfig.kind == "smtp").values(enabled=True))
            await s.commit()


async def test_report_schedules(client, org):
    t = ST["org"]["tokens"]
    r = await client.post("/analytics/report-schedules", headers=auth(t),
                          json={"name": "Weekly", "frequency": "weekly", "weekday": 0, "time": "9:00", "recipients": ["x@y.in"]})
    assert r.status_code == 422
    r = await client.post("/analytics/report-schedules", headers=auth(t),
                          json={"name": "Weekly", "frequency": "weekly", "weekday": 0, "time": "09:00", "recipients": ["not-an-email"]})
    assert r.status_code == 422
    r = await client.post("/analytics/report-schedules", headers=auth(t),
                          json={"name": "Weekly", "frequency": "weekly", "weekday": 0, "time": "09:00",
                                "recipients": ["Team@Growth-Test.in", "team@growth-test.in"]})
    assert r.status_code == 201, r.text
    sch = r.json()
    assert sch["recipients"] == ["team@growth-test.in"] and sch["next_run_at"]
    r = await client.patch(f"/analytics/report-schedules/{sch['id']}", headers=auth(t), json={"frequency": "monthly", "month_day": 2})
    assert r.json()["frequency"] == "monthly"
    # make it due and let the periodic task pick it up
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        await s.execute(update(ReportSchedule).where(ReportSchedule.id == uuid.UUID(sch["id"]))
                        .values(next_run_at=datetime.now(UTC) - timedelta(minutes=1)))
        await s.commit()
    ids = await mod.due_schedules()
    assert len(ids) == 1
    async with SessionLocal() as s:
        set_tenant(s, ST["oid"])
        rep = await s.get(AnalyticsReport, ids[0])
        sc = await s.get(ReportSchedule, uuid.UUID(sch["id"]))
        assert rep.trigger == "schedule" and rep.recipients == ["team@growth-test.in"]
        assert rep.period_start.day == 1 and (rep.period_end + timedelta(days=1)).day == 1  # previous calendar month
        assert sc.next_run_at > datetime.now(UTC)
    assert await mod.due_schedules() == []  # not due again
    r = await client.post(f"/analytics/report-schedules/{sch['id']}/send-now", headers=auth(t))
    assert r.status_code == 202 and r.json()["report"]["trigger"] == "send_now"
    assert (await client.delete(f"/analytics/report-schedules/{sch['id']}", headers=auth(t))).status_code == 204


# ------------------------------------------------------------------ permissions & isolation
async def test_viewer_permissions(client, org):
    t = ST["org"]["tokens"]
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(t))).json()}
    r = await client.post("/users", headers=auth(t), json={"email": "viewer@growth-test.in", "full_name": "Growth Viewer",
                                                          "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    v = await login(client, "viewer@growth-test.in", r.json()["temporary_password"])
    for path in ("/analytics/growth", "/analytics/competitors", "/analytics/health", "/analytics/recommendations",
                 "/analytics/reports", "/analytics/report-schedules"):
        assert (await client.get(path, headers=auth(v))).status_code == 200, path
    assert (await client.post("/analytics/growth/refresh", headers=auth(v))).status_code == 403
    assert (await client.post("/analytics/recommendations/generate", headers=auth(v))).status_code == 403
    assert (await client.post("/analytics/reports", headers=auth(v), json={"period": "7"})).status_code == 403
    assert (await client.put(f"/analytics/competitors/{ST['rival_id']}/handles", headers=auth(v), json={})).status_code == 403
    d = (await client.get("/analytics/recommendations", headers=auth(v))).json()
    assert d["can_manage"] is False


async def test_tenant_isolation(client, org):
    other = await _new_org(client, "Growth Other Co", "owner@growth-other.in")
    h = auth(other["tokens"])
    g = (await client.get("/analytics/growth", headers=h)).json()
    assert g["accounts"] == [] and g["totals"]["followers"] is None
    assert (await client.get("/analytics/reports", headers=h)).json()["items"] == []
    assert (await client.get("/analytics/recommendations", headers=h)).json()["items"] == []
    assert (await client.get(f"/analytics/reports/{ST['report_id']}", headers=h)).status_code == 404
    assert (await client.put(f"/analytics/competitors/{ST['rival_id']}/handles", headers=h, json={})).status_code == 404
    bench = (await client.get("/analytics/competitors", headers=h)).json()
    assert bench["competitors"] == []
    # a signed link for one org can't be replayed with another org id
    url = (await client.get(f"/analytics/reports/{ST['report_id']}", headers=auth(ST["org"]["tokens"]))).json()["download_url"]
    forged = url.replace(f"o={ST['oid']}", f"o={other['org_id']}")
    assert (await client.get("http://test" + forged)).status_code == 403
    async with SessionLocal() as s:
        set_tenant(s, other["org_id"])
        assert (await s.scalars(select(AccountMetric))).all() == []
        assert (await s.scalars(select(Recommendation))).all() == []
