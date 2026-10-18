"""Web and social listening: matching and dedupe helpers, every source collector (mocked RSS XML, Reddit, Hacker News,
Bluesky, Mastodon, YouTube and X JSON; web/news search faked), AI enrichment with a fake LLM and the keyword fallback,
spikes and digests, insights and the grounded summary, reply drafts, post ideas, API permissions and tenant isolation.
No real network: app.services.social.net.client is replaced with an httpx.MockTransport."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.crypto import encrypt_json
from app.core.db import SessionLocal, set_tenant
from app.models import CompanyEntity, CompanyProfile, Competitor, IdeaBrief, Organization, ProviderConfig
from app.models.listening import ListeningMention, ListeningQuery, ListeningSettings
from app.services import notify, search
from app.services.inbox import listening as inbox_listening
import app.modules.listening  # noqa: F401  (registers the job handlers)
from app.services.jobs import run_inline
from app.services.listening import engine, insights, sources, text
from app.services.llm import router as llm_router
from app.services.social import net
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"
NOW = datetime.now(UTC)


# ------------------------------------------------------------------ HTTP mock
class Mock:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, object]] = []
        self.calls: list[httpx.Request] = []

    def on(self, method: str, pattern: str, reply):
        self.routes.insert(0, (method, re.compile(pattern), reply))
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url.copy_with(query=None))
        for method, pat, reply in self.routes:
            if method == request.method and pat.search(url):
                if callable(reply):
                    return reply(request)
                status, body = reply
                if isinstance(body, str):
                    return httpx.Response(status, text=body, headers={"content-type": "application/rss+xml"})
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": f"no mock for {request.method} {url}"})

    def find(self, pattern: str) -> list[httpx.Request]:
        return [r for r in self.calls if re.search(pattern, str(r.url))]


def qs(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(req.url)).query).items()}


@pytest.fixture
def mock(monkeypatch):
    m = Mock()
    monkeypatch.setattr(net, "client", lambda timeout=60.0: httpx.AsyncClient(transport=httpx.MockTransport(m.handler)))
    return m


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    async def none(*a, **kw):
        raise llm_router.NoProviderError("No AI text provider is set up.")

    monkeypatch.setattr(llm_router, "complete_json", none)


@pytest.fixture(autouse=True)
def sent(monkeypatch):
    """Capture notifications instead of delivering them."""
    box: list[dict] = []

    async def fake_notify(org_id, event, title, body=None, *, link=None, permission=None, actions=None):
        box.append({"org": org_id, "event": event, "title": title, "body": body, "link": link, "permission": permission})

    monkeypatch.setattr(notify, "notify_org", fake_notify)
    return box


@pytest.fixture(autouse=True)
def fake_search(monkeypatch):
    calls = {"web": [], "news": []}

    async def search_all(session, query, n=10, *, freshness=None, max_providers=3):
        calls["web"].append((query, freshness))
        return [{"title": "Listen One review 2026", "url": "https://www.blog.example.com/listen-one-review?utm_source=x",
                 "snippet": "An honest Listen One review after six months.", "provider": "DuckDuckGo"}], []

    async def news_search(session, query, n=10):
        calls["news"].append(query)
        return [{"title": "Listen One launches AI payroll for Indian SMEs",
                 "url": "https://economictimes.indiatimes.com/tech/listen-one-ai-payroll/articleshow/1.cms",
                 "snippet": "The Noida company said...", "date": (NOW - timedelta(hours=2)).isoformat(),
                 "source": "Economic Times"}]

    monkeypatch.setattr(search, "search_all", search_all)
    monkeypatch.setattr(inbox_listening, "news_search", news_search)
    return calls


# ------------------------------------------------------------------ source payloads
def rss() -> str:
    d = format_datetime(NOW - timedelta(hours=1))
    old = format_datetime(NOW - timedelta(days=60))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>"Listen One" - Google News</title>
<item><title>Listen One launches AI payroll for Indian SMEs - Economic Times</title>
<link>https://news.google.com/rss/articles/CBMiAAA?oc=5</link><guid isPermaLink="false">CBMiAAA</guid>
<pubDate>{d}</pubDate>
<description>&lt;a href="https://news.google.com/rss/articles/CBMiAAA"&gt;Listen One launches AI payroll for Indian SMEs&lt;/a&gt;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Economic Times&lt;/font&gt;</description>
<source url="https://economictimes.indiatimes.com">Economic Times</source></item>
<item><title>Listen One Hospital opens a new cardiac wing in Pune - Pune Mirror</title>
<link>https://news.google.com/rss/articles/CBMiBBB?oc=5</link><guid isPermaLink="false">CBMiBBB</guid>
<pubDate>{d}</pubDate><description>Hospital news</description>
<source url="https://punemirror.com">Pune Mirror</source></item>
<item><title>Payroll compliance changes for 2027 - Mint</title>
<link>https://news.google.com/rss/articles/CBMiCCC?oc=5</link><guid isPermaLink="false">CBMiCCC</guid>
<pubDate>{d}</pubDate><description>Labour codes and payroll</description>
<source url="https://livemint.com">Mint</source></item>
<item><title>Listen One casino bonus codes - Spam Site</title>
<link>https://news.google.com/rss/articles/CBMiDDD?oc=5</link><guid isPermaLink="false">CBMiDDD</guid>
<pubDate>{d}</pubDate><description>casino</description><source url="https://spam.example">Spam Site</source></item>
<item><title>Listen One old story from last year - Old News</title>
<link>https://news.google.com/rss/articles/CBMiEEE?oc=5</link><guid isPermaLink="false">CBMiEEE</guid>
<pubDate>{old}</pubDate><description>old</description><source url="https://old.example">Old News</source></item>
</channel></rss>"""


def reddit_json() -> dict:
    t = (NOW - timedelta(hours=3)).timestamp()

    def post(i, title, body="", score=1, comments=0, nsfw=False):
        return {"kind": "t3", "data": {"id": f"r{i}", "name": f"t3_r{i}", "title": title, "selftext": body,
                                       "permalink": f"/r/IndiaStartups/comments/r{i}/post_{i}/", "author": f"user{i}",
                                       "subreddit_name_prefixed": "r/IndiaStartups", "score": score,
                                       "num_comments": comments, "created_utc": t, "over_18": nsfw}}
    return {"data": {"children": [
        post(1, "Can anyone recommend a payroll tool for a 40 person startup?",
             "Looking at Listen One vs Rival HR. Thoughts?", score=12, comments=5),
        post(2, "Listen One support is terrible", "No response for a week, this is not working for us.", score=3),
        post(3, "Rival HR is great for attendance", "Loved it", score=8),
        post(4, "Listen One nsfw thing", nsfw=True),
        post(5, "I was listening to one song all day", "unrelated"),
    ]}}


def hn_json() -> dict:
    t = int((NOW - timedelta(hours=5)).timestamp())
    return {"hits": [
        {"objectID": "4001", "title": "Show HN: Listen One – open-source HR analytics", "url": "https://listenone.in/oss",
         "author": "pg_fan", "points": 40, "num_comments": 12, "created_at_i": t, "story_text": None},
        {"objectID": "4002", "comment_text": "<p>You have to listen oneself first, it's a sharp idea</p>",
         "story_title": "Ask HN: Burnout", "author": "x", "points": None, "created_at_i": t},
    ]}


def bsky_json() -> dict:
    return {"posts": [{
        "uri": "at://did:plc:abc/app.bsky.feed.post/3kxyz", "cid": "c1",
        "author": {"did": "did:plc:abc", "handle": "asha.bsky.social", "displayName": "Asha"},
        "record": {"text": "Loving the new #listenone dashboard, great work!", "createdAt": (NOW - timedelta(hours=4)).isoformat(),
                   "langs": ["en"]},
        "likeCount": 7, "repostCount": 2, "replyCount": 1,
    }]}


def masto_json() -> list:
    return [{"id": "9001", "uri": "https://mastodon.example/users/ravi/statuses/9001",
             "url": "https://mastodon.example/@ravi/9001", "content": "<p>Trying <a href='#'>#listenone</a> for payroll today</p>",
             "created_at": (NOW - timedelta(hours=6)).isoformat(), "language": "en", "favourites_count": 2,
             "reblogs_count": 0, "replies_count": 0,
             "account": {"acct": "ravi@mastodon.example", "display_name": "Ravi", "url": "https://mastodon.example/@ravi",
                         "followers_count": 150}}]


def wire_free_sources(mock: Mock) -> None:
    mock.on("GET", r"news\.google\.com/rss/search", (200, rss()))
    mock.on("GET", r"www\.reddit\.com/search\.json", (200, reddit_json()))
    mock.on("GET", r"hn\.algolia\.com/api/v1/search_by_date", (200, hn_json()))
    mock.on("GET", r"public\.api\.bsky\.app/xrpc/app\.bsky\.feed\.searchPosts", (200, bsky_json()))
    mock.on("GET", r"mastodon\.example/api/v1/timelines/tag/", (200, []))
    mock.on("GET", r"mastodon\.example/api/v1/timelines/tag/listenone$", (200, masto_json()))


# ------------------------------------------------------------------ fixtures
async def _new_org(client, admin_tokens, name: str, email: str) -> dict:
    r = await client.post("/platform/organizations", headers=auth(admin_tokens),
                          json={"name": name, "admin_email": email, "admin_name": f"{name} Owner"})
    assert r.status_code == 201, r.text
    return await login(client, email, r.json()["temporary_password"])


def org_of(t: dict) -> uuid.UUID:
    return uuid.UUID(t["organization"]["id"])


@pytest.fixture(scope="module")
async def hrp(client, admin):
    return await login(client, "admin@hrpai.in", ADMIN_PW)


@pytest.fixture(scope="module")
async def org1(client, hrp):
    t = await _new_org(client, hrp, "Listen One Pvt Ltd", "owner@listen-one.in")
    oid = org_of(t)
    async with SessionLocal() as s:
        set_tenant(s, oid)
        org = await s.get(Organization, oid)
        org.website = "https://www.listenone.in"
        org.country = "India"
        org.social_links = {"linkedin": "https://www.linkedin.com/company/listenone-hr", "x": "https://x.com/listenone"}
        s.add(CompanyProfile(organization_id=oid, summary="Listen One makes HRMS and payroll software for Indian SMEs.",
                             industries=["HR technology"], social_links={}))
        s.add(CompanyEntity(organization_id=oid, type="product", name="HRMS", confirmed=True))
        s.add(CompanyEntity(organization_id=oid, type="product", name="Listen One Payroll", confirmed=True))
        s.add(Competitor(organization_id=oid, name="Rival HR Pvt Ltd", website="https://rivalhr.com", domain="rivalhr.com",
                         status="confirmed", overlap_score=0.8))
        s.add(Competitor(organization_id=oid, name="Gone Co", website="https://gone.example", domain="gone.example",
                         status="rejected"))
        await s.commit()
    return t


@pytest.fixture(scope="module")
async def org2(client, hrp):
    return await _new_org(client, hrp, "Listen Two", "owner@listen-two.in")


async def _settings(oid, **values):
    async with SessionLocal() as s:
        set_tenant(s, oid)
        st = await engine.get_settings(s, oid)
        for k, v in values.items():
            setattr(st, k, v)
        await s.commit()


async def _mentions(oid, **where) -> list[ListeningMention]:
    async with SessionLocal() as s:
        set_tenant(s, oid)
        stmt = select(ListeningMention)
        for k, v in where.items():
            stmt = stmt.where(getattr(ListeningMention, k) == v)
        return list((await s.scalars(stmt)).all())


# ------------------------------------------------------------------ pure helpers
async def test_term_matching_is_strict():
    assert text.find_terms("Big news from HRP today", ["HRP"]) == ["HRP"]
    assert text.find_terms("a sharp drop", ["HRP"]) == []
    assert text.find_terms("Try hrp-ai now", ["HRP AI"]) == ["HRP AI"]
    assert text.find_terms("We love HRPAI", ["HRP AI"]) == ["HRP AI"]
    assert text.find_terms("Tag #hrpai", ["#hrpai"]) == ["#hrpai"]
    assert text.find_terms("listen oneself", ["Listen One"]) == []
    ok, matched, why = text.prefilter("HRP launches payroll", ["HRP"], ["payroll", "HRMS"], ["casino"])
    assert ok and matched == ["HRP"] and why is None
    assert text.prefilter("HRP hospital", ["HRP"], ["payroll"], [])[2] == "no_context"
    assert text.prefilter("HRP payroll casino", ["HRP"], ["payroll"], ["casino"])[2] == "excluded"


async def test_canonical_url_and_hashes():
    assert text.canonical_url("http://www.Example.com/a/?utm_source=x&id=2#top") == "https://example.com/a?id=2"
    assert text.canonical_url("https://old.reddit.com/r/x/comments/1/") == "https://reddit.com/r/x/comments/1"
    assert text.canonical_url("https://youtu.be/abc123") == text.canonical_url("https://www.youtube.com/watch?v=abc123&t=3")
    a = text.content_hash("Listen One launches AI payroll for Indian SMEs - Economic Times", None, "Economic Times")
    b = text.content_hash("Listen One launches AI payroll for Indian SMEs", "different body")
    assert a and a == b
    assert text.content_hash("short", None) is None
    assert text.dedupe_key("web", "https://a.com/x?utm_medium=y", None) == text.dedupe_key("news", "https://www.a.com/x", None)


async def test_top_terms_skip_stop_words_and_tracked():
    rows = ["Payroll compliance is hard for SMEs", "payroll compliance tips", "Listen One payroll compliance update"]
    terms = {t["term"]: t["count"] for t in text.top_terms(rows, exclude={"Listen One"})}
    assert terms.get("payroll compliance") == 3
    assert "listen" not in terms and "for" not in terms


async def test_parse_google_news_rss():
    rows = sources.parse_rss(rss())
    assert rows[0].title == "Listen One launches AI payroll for Indian SMEs"
    assert rows[0].publisher == "Economic Times" and rows[0].author_url == "https://economictimes.indiatimes.com"
    assert rows[0].published_at and rows[0].published_at.tzinfo
    assert rows[0].text is None  # description only repeated the headline
    with pytest.raises(sources.SourceError):
        sources.parse_rss("<rss><channel><item>")


# ------------------------------------------------------------------ starter topics
async def test_starter_suggestions_from_profile(client, org1):
    r = await client.get("/listening/suggestions", headers=auth(org1))
    assert r.status_code == 200, r.text
    by = {d["name"]: d for d in r.json()}
    brand = by["Listen One"]
    assert brand["kind"] == "brand"
    assert {"Listen One", "listenone", "listenone.in", "#listenone", "listenone-hr"} <= set(brand["terms"])
    products = by["Listen One products"]["terms"]
    assert "Listen One HRMS" in products and "Listen One Payroll" in products and "HRMS" not in products
    assert by["Rival HR"]["kind"] == "competitor" and "rivalhr.com" in by["Rival HR"]["terms"]
    assert "Gone Co" not in by  # rejected competitors are skipped
    assert by["Industry conversations"]["countries"] == ["IN"]

    r = await client.post("/listening/queries/starter", headers=auth(org1), json={"names": ["Listen One", "Rival HR"]})
    assert r.status_code == 201, r.text
    assert sorted(q["name"] for q in r.json()) == ["Listen One", "Rival HR"]
    again = (await client.get("/listening/suggestions", headers=auth(org1))).json()
    assert {d["name"]: d["exists"] for d in again}["Listen One"] is True


async def test_query_validation(client, org1):
    bad = await client.post("/listening/queries", headers=auth(org1), json={"name": "x", "terms": [" "]})
    assert bad.status_code == 422
    bad = await client.post("/listening/queries", headers=auth(org1), json={"name": "x", "terms": ["ok"], "languages": ["eng"]})
    assert bad.status_code == 422


# ------------------------------------------------------------------ a full run
async def test_run_collects_filters_and_dedupes(client, org1, mock, fake_search, monkeypatch):
    oid = org_of(org1)
    wire_free_sources(mock)
    r = await client.put("/listening/settings", headers=auth(org1), json={
        "enabled": True, "interval_hours": 3, "digest": "weekly", "spike_alerts": True, "use_ai": True,
        "mastodon_instance": "https://mastodon.example/", "sources": {}})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["settings"]["mastodon_instance"] == "mastodon.example"
    avail = {s["id"]: s["available"] for s in st["sources"]}
    assert avail["mastodon"] and not avail["youtube"] and not avail["x"]
    # exclude casino on the brand topic
    qs_ = {q["name"]: q for q in (await client.get("/listening/queries", headers=auth(org1))).json()}
    brand = qs_["Listen One"]
    r = await client.put(f"/listening/queries/{brand['id']}", headers=auth(org1), json={
        **{k: brand[k] for k in ("name", "kind", "terms", "context_terms", "languages", "countries", "sources", "enabled")},
        "exclude": ["casino"]})
    assert r.status_code == 200, r.text

    res = await run_inline("listening.run", oid, {"force": True})
    assert res["sources"]["google_news"]["ok"] and res["sources"]["reddit"]["ok"], res
    assert res["enrichment"]["heuristic"] > 0 and res["enrichment"]["ai"] == 0

    rows = await _mentions(oid)
    titles = {m.title for m in rows}
    assert "Listen One launches AI payroll for Indian SMEs" in titles
    assert not any("casino" in (t or "") for t in titles)  # excluded
    assert not any("old story" in (t or "") for t in titles)  # older than 30 days
    assert not any("nsfw" in (t or "") for t in titles) and not any("listening to one" in (t or "") for t in titles)
    assert not any(m.source == "hackernews" and m.external_id == "4002" for m in rows)  # "listen oneself"
    assert "Payroll compliance changes for 2027" not in titles  # no tracked term visible: dropped
    # same story from Google News and the news search is stored once
    assert len([m for m in rows if (m.title or "").startswith("Listen One launches AI payroll")]) == 1
    gn = next(m for m in rows if m.source == "google_news" and m.title.startswith("Listen One launches"))
    assert gn.domain == "economictimes.indiatimes.com" and gn.author_name == "Economic Times"

    # the reddit post mentioning both brand and competitor counts for both topics
    both = next(m for m in rows if m.external_id == "t3_r1")
    assert set(both.query_ids) == {brand["id"], qs_["Rival HR"]["id"]}
    assert both.needs_response and both.response_reason == "recommendation_request"
    assert both.score == 12 and both.comments == 5 and both.url.startswith("https://www.reddit.com/r/IndiaStartups/")
    complaint = next(m for m in rows if m.external_id == "t3_r2")
    assert complaint.sentiment == "negative" and complaint.response_reason == "complaint"
    rival = next(m for m in rows if m.external_id == "t3_r3")
    assert rival.is_competitor and rival.competitor_id is not None and rival.query_ids == [qs_["Rival HR"]["id"]]
    bsky = [m for m in rows if m.source == "bluesky"]
    assert len(bsky) == 1 and bsky[0].sentiment == "positive" and bsky[0].url.endswith("/post/3kxyz")
    masto = next(m for m in rows if m.source == "mastodon")
    assert masto.author_followers == 150 and "#listenone" in masto.snippet
    hn = next(m for m in rows if m.source == "hackernews")
    assert hn.score == 40 and hn.url == "https://news.ycombinator.com/item?id=4001"
    web = next(m for m in rows if m.source == "web")
    assert web.canonical_url == "https://blog.example.com/listen-one-review"

    # requests: Reddit gets a descriptive User-Agent; search freshness is a week; HN only asks since the last run
    reddit_req = mock.find(r"reddit\.com/search\.json")[0]
    assert "listening" in reddit_req.headers["user-agent"] and qs(reddit_req)["sort"] == "new"
    assert all(f == "week" for _q, f in fake_search["web"])
    assert "created_at_i>" in qs(mock.find(r"hn\.algolia\.com")[0])["numericFilters"]
    g = qs(mock.find(r"news\.google\.com")[0])
    assert g["hl"] == "en-IN" and g["gl"] == "IN" and g["ceid"] == "IN:en" and "-casino" in g["q"] and "when:" in g["q"]
    assert not mock.find(r"googleapis\.com|api\.x\.com")  # unavailable sources aren't called

    # a second run finds the same things again: nothing new, nothing duplicated
    count = len(rows)
    monkeypatch.setattr(engine, "FORCE_MIN", timedelta(0))
    res2 = await run_inline("listening.run", oid, {"force": True})
    assert res2["new"] == 0
    assert len(await _mentions(oid)) == count

    # per-topic source status is kept for the UI
    qs2 = {q["name"]: q for q in (await client.get("/listening/queries", headers=auth(org1))).json()}
    assert qs2["Listen One"]["source_status"]["reddit"]["ok"] is True
    assert qs2["Listen One"]["mentions_30d"] >= 5


async def test_source_failures_are_isolated(client, org1, mock, monkeypatch):
    oid = org_of(org1)
    monkeypatch.setattr(engine, "FORCE_MIN", timedelta(0))
    wire_free_sources(mock)
    mock.on("GET", r"www\.reddit\.com/search\.json", (429, {"message": "Too Many Requests"}))
    mock.on("GET", r"public\.api\.bsky\.app", (403, {"error": "Forbidden"}))
    res = await run_inline("listening.run", oid, {"force": True, "sources": ["reddit", "bluesky", "hackernews"]})
    assert res["sources"]["reddit"]["ok"] is False and "rate-limiting" in res["sources"]["reddit"]["message"]
    assert res["sources"]["bluesky"]["ok"] is False and "Connect a Bluesky account" in res["sources"]["bluesky"]["message"]
    assert res["sources"]["hackernews"]["ok"] is True
    st = (await client.get("/listening/status", headers=auth(org1))).json()
    assert {s["id"]: s for s in st["sources"]}["reddit"]["last"]["ok"] is False


async def test_youtube_and_x_with_keys(client, org1, mock, monkeypatch):
    oid = org_of(org1)
    monkeypatch.setattr(engine, "FORCE_MIN", timedelta(0))
    async with SessionLocal() as s:
        set_tenant(s, oid)
        s.add(ProviderConfig(organization_id=oid, kind="social_data", provider="youtube_data", name="YouTube key",
                             secrets_enc=encrypt_json({"api_key": "yt-secret"})))
        s.add(ProviderConfig(organization_id=oid, kind="social_data", provider="x_api", name="X token",
                             secrets_enc=encrypt_json({"api_key": "x-bearer"})))
        await s.commit()
    pub = (NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mock.on("GET", r"youtube/v3/search$", (200, {"items": [{"id": {"videoId": "vid1"}, "snippet": {
        "title": "Listen One payroll demo", "description": "Walkthrough of Listen One", "channelTitle": "HR Tube",
        "channelId": "UC1", "publishedAt": pub}}]}))
    mock.on("GET", r"youtube/v3/videos$", (200, {"items": [{"id": "vid1", "statistics": {
        "viewCount": "1500", "likeCount": "30", "commentCount": "4"}}]}))
    mock.on("GET", r"api\.x\.com/2/tweets/search/recent$", (200, {
        "data": [{"id": "1777", "text": "Is Listen One any good for payroll?", "author_id": "u1", "created_at": pub,
                  "lang": "en", "public_metrics": {"like_count": 3, "retweet_count": 1, "reply_count": 2,
                                                   "impression_count": 900}}],
        "includes": {"users": [{"id": "u1", "username": "meera", "name": "Meera",
                                "public_metrics": {"followers_count": 2200}}]}}))
    st = (await client.get("/listening/status", headers=auth(org1))).json()
    assert {s["id"]: s["available"] for s in st["sources"]}["youtube"] is True
    res = await run_inline("listening.run", oid, {"force": True, "sources": ["youtube", "x"]})
    assert res["sources"]["youtube"]["ok"] and res["sources"]["x"]["ok"], res
    yreq = mock.find(r"youtube/v3/search")[0]
    assert qs(yreq)["key"] == "yt-secret" and qs(yreq)["order"] == "date"
    xreq = mock.find(r"api\.x\.com")[0]
    assert xreq.headers["authorization"] == "Bearer x-bearer" and "-is:retweet" in qs(xreq)["query"]
    vid = (await _mentions(oid, source="youtube"))[0]
    assert vid.reach == 1500 and vid.score == 30 and vid.url == "https://www.youtube.com/watch?v=vid1"
    tw = (await _mentions(oid, source="x"))[0]
    assert tw.author_followers == 2200 and tw.author_handle == "@meera" and tw.url == "https://x.com/meera/status/1777"
    assert tw.needs_response and tw.response_reason == "question"
    # keys never come back through the API
    st = await client.get("/listening/status", headers=auth(org1))
    assert "yt-secret" not in st.text and "x-bearer" not in st.text


# ------------------------------------------------------------------ AI enrichment
async def test_ai_enrichment_drops_false_positives(client, org2, mock, monkeypatch):
    oid = org_of(org2)
    seen_prompts = []

    async def fake(session, task, prompt, *, system=None, **kw):
        seen_prompts.append((task, prompt))
        items = []
        rows = json.loads(prompt.split("Mentions:\n", 1)[1].split("\n\nAnswer as", 1)[0])
        for r in rows:
            hospital = "Hospital" in r["title"]
            items.append({"n": r["n"], "relevant": not hospital, "relevance": 0.1 if hospital else 0.9,
                          "sentiment": "positive" if "launches" in r["title"] else "neutral",
                          "tags": ["Launch", "payroll"], "needs_response": False, "reason": None,
                          "about": "brand"})
        return {"items": items}

    monkeypatch.setattr(llm_router, "complete_json", fake)
    mock.on("GET", r"news\.google\.com/rss/search", (200, rss()))
    r = await client.post("/listening/queries", headers=auth(org2), json={
        "name": "Listen One", "kind": "brand", "terms": ["Listen One"], "exclude": ["casino"],
        "sources": {"reddit": False}})
    assert r.status_code == 201, r.text
    assert r.json()["sources"] == {"reddit": False}
    await _settings(oid, sources={s: s == "google_news" for s in sources.SOURCES})
    res = await run_inline("listening.run", oid, {"force": True})
    assert res["enrichment"]["ai"] >= 2 and res["filtered_out"] == 1, res
    assert seen_prompts and seen_prompts[0][0] == "extract" and "Listen One Hospital" in seen_prompts[0][1]
    hosp = next(m for m in await _mentions(oid) if "Hospital" in (m.title or ""))
    assert hosp.relevant is False and hosp.sentiment_source == "ai"
    feed = (await client.get("/listening/mentions", headers=auth(org2))).json()
    assert all("Hospital" not in (i["title"] or "") for i in feed["items"])
    launch = next(i for i in feed["items"] if "launches" in i["title"])
    assert launch["sentiment"] == "positive" and launch["tags"] == ["launch", "payroll"]
    filtered = (await client.get("/listening/mentions?filtered=true", headers=auth(org2))).json()
    assert [i["title"] for i in filtered["items"]] == ["Listen One Hospital opens a new cardiac wing in Pune"]
    # a person can put it back
    r = await client.patch(f"/listening/mentions/{filtered['items'][0]['id']}", headers=auth(org2), json={"relevant": True})
    assert r.status_code == 200 and r.json()["relevant"] is True


# ------------------------------------------------------------------ feed, actions
async def test_feed_filters_and_actions(client, org1):
    h = auth(org1)
    allm = (await client.get("/listening/mentions?limit=200", headers=h)).json()
    assert allm["total"] == len(allm["items"]) > 5
    neg = (await client.get("/listening/mentions?sentiment=negative", headers=h)).json()["items"]
    assert neg and all(i["sentiment"] == "negative" for i in neg)
    nr = (await client.get("/listening/mentions?needs_response=true", headers=h)).json()["items"]
    assert {i["response_reason"] for i in nr} >= {"recommendation_request", "complaint"}
    red = (await client.get("/listening/mentions?source=reddit", headers=h)).json()["items"]
    assert red and all(i["source"] == "reddit" for i in red)
    qs_ = {q["name"]: q for q in (await client.get("/listening/queries", headers=h)).json()}
    comp = (await client.get(f"/listening/mentions?query_id={qs_['Rival HR']['id']}", headers=h)).json()
    assert comp["total"] == 2 and all(any(q["name"] == "Rival HR" for q in i["queries"]) for i in comp["items"])
    kind = (await client.get("/listening/mentions?kind=competitor", headers=h)).json()
    assert kind["total"] == 2
    search_ = (await client.get("/listening/mentions?q=support", headers=h)).json()["items"]
    assert [i["title"] for i in search_] == ["Listen One support is terrible"]
    top = (await client.get("/listening/mentions?sort=engagement&limit=1", headers=h)).json()["items"][0]
    assert top["engagement"] >= max(i["engagement"] for i in allm["items"]) - 1

    mid = search_[0]["id"]
    r = await client.patch(f"/listening/mentions/{mid}", headers=h, json={"status": "reviewed", "sentiment": "neutral"})
    assert r.json()["status"] == "reviewed" and r.json()["sentiment_source"] == "manual"
    r = await client.post("/listening/mentions/bulk", headers=h, json={"ids": [mid], "status": "hidden"})
    assert r.json()["updated"] == 1
    assert (await client.get("/listening/mentions?q=support", headers=h)).json()["total"] == 0
    assert (await client.get("/listening/mentions?q=support&status=hidden", headers=h)).json()["total"] == 1
    assert (await client.patch(f"/listening/mentions/{uuid.uuid4()}", headers=h, json={"status": "reviewed"})).status_code == 404


async def test_draft_reply_and_post_idea(client, org1, monkeypatch):
    h = auth(org1)
    item = next(i for i in (await client.get("/listening/mentions?needs_response=true", headers=h)).json()["items"]
                if i["source"] == "reddit")
    r = await client.post(f"/listening/mentions/{item['id']}/draft", headers=h)
    assert r.status_code == 400 and "AI text model" in r.json()["detail"]

    prompts = []

    async def fake(session, task, prompt, *, system=None, **kw):
        prompts.append((task, prompt, system))
        if task == "write":
            return {"replies": ["Happy to help! I work at Listen One.", "x" * 3000, "Happy to help! I work at Listen One."]}
        return {"title": "How to choose payroll software for a 40-person startup", "angle": "A checklist.",
                "why_now": "Founders are asking on Reddit.", "formats": ["linkedin_post", "bogus"]}

    monkeypatch.setattr(llm_router, "complete_json", fake)
    r = await client.post(f"/listening/mentions/{item['id']}/draft", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["drafts"][0] == "Happy to help! I work at Listen One." and len(d["drafts"]) == 2
    assert all(len(x) <= 1500 for x in d["drafts"]) and d["where"] == "Reddit"
    assert "Never invent" in prompts[0][2] and "recommend a payroll tool" in prompts[0][1]

    r = await client.post(f"/listening/mentions/{item['id']}/idea", headers=h)
    assert r.status_code == 201, r.text
    async with SessionLocal() as s:
        set_tenant(s, org_of(org1))
        b = await s.get(IdeaBrief, uuid.UUID(r.json()["idea_id"]))
        assert b.theme == "listening" and b.status == "saved"
        assert b.title.startswith("How to choose payroll") and [f["format"] for f in b.formats] == ["linkedin_post"]
        assert b.facts[0]["source_url"] == item["url"]
    briefs = (await client.get("/research/briefs?status=saved", headers=h)).json()
    assert any(x["title"].startswith("How to choose payroll") and x["theme_label"] == "Listening" for x in briefs)
    assert (await client.post(f"/listening/mentions/{item['id']}/idea", headers=h)).status_code == 409
    again = (await client.get(f"/listening/mentions?q={item['title'][:20]}&status=all", headers=h)).json()["items"][0]
    assert again["idea_id"] and again["status"] == "reviewed"


# ------------------------------------------------------------------ insights and summary
async def test_overview(client, org1):
    r = await client.get("/listening/overview?days=30", headers=auth(org1))
    assert r.status_code == 200, r.text
    d = r.json()
    t = d["totals"]
    assert t["mentions"] >= 8 and t["negative"] >= 0 and t["competitor"] >= 2
    assert len(d["volume"]) == 31 and sum(v["total"] for v in d["volume"]) == t["mentions"]
    assert {"reddit", "google_news"} <= set(d["volume_sources"])
    assert sum(sum(v[k] for k in ("positive", "neutral", "negative")) for v in d["sentiment_trend"]) == t["mentions"]
    sov = {r_["name"]: r_ for r_ in d["share_of_voice"]}
    assert sov["Listen One Pvt Ltd"]["is_us"] and sov["Rival HR"]["mentions"] == 2
    assert round(sum(r_["share"] for r_ in d["share_of_voice"])) == 100
    assert {x["domain"] for x in d["top_domains"]} >= {"economictimes.indiatimes.com"}
    assert any(a["source"] == "reddit" for a in d["top_authors"])
    assert all(x["term"] not in ("listen one", "the") for x in d["top_terms"])
    one = (await client.get(f"/listening/overview?days=30&query_id={sov['Rival HR']['key']}", headers=auth(org1)))
    assert one.status_code == 200


async def test_summary_is_grounded(client, org1, monkeypatch):
    oid = org_of(org1)
    r = await run_inline("listening.summary", oid, {"days": 7})
    assert r["method"] == "basic"

    async def fake(session, task, prompt, *, system=None, **kw):
        assert task == "plan" and "copied exactly" in system
        return {"summary": "People compare Listen One with Rival HR and ask about payroll.", "themes": [
            {"title": "Payroll tool choices", "detail": "Founders ask for recommendations.", "sentiment": "neutral",
             "mentions": [0, 1, 99], "quotes": [{"n": 0, "quote": "invented words nobody wrote anywhere"},
                                                 {"n": 99, "quote": "nope"}]},
            {"title": "", "detail": "dropped"}]}

    monkeypatch.setattr(llm_router, "complete_json", fake)
    r = await client.post("/listening/summary", headers=auth(org1), json={"days": 7})
    assert r.status_code == 202
    await run_inline("listening.summary", oid, {"days": 7})
    s = (await client.get("/listening/overview?days=7", headers=auth(org1))).json()["summary"]
    assert s["method"] == "ai" and s["summary"].startswith("People compare")
    assert len(s["themes"]) == 1 and s["themes"][0]["quotes"] == []  # the made-up quote was dropped
    assert len(s["themes"][0]["links"]) == 2

    async with SessionLocal() as ss:
        set_tenant(ss, oid)
        rows = await insights._rows(ss, NOW - timedelta(days=7), datetime.now(UTC))
        picked = sorted(rows, key=lambda m: (insights.engagement(m), insights.when(m)), reverse=True)
        real = (picked[0].title or picked[0].snippet)[:30]

    async def fake2(session, task, prompt, *, system=None, **kw):
        return {"summary": "ok", "themes": [{"title": "T", "detail": "", "sentiment": "neutral", "mentions": [0],
                                             "quotes": [{"n": 0, "quote": real}]}]}

    monkeypatch.setattr(llm_router, "complete_json", fake2)
    await run_inline("listening.summary", oid, {"days": 7})
    s = (await client.get("/listening/overview?days=7", headers=auth(org1))).json()["summary"]
    assert s["themes"][0]["quotes"][0]["quote"] == real and s["themes"][0]["quotes"][0]["url"]


# ------------------------------------------------------------------ spikes and digests
async def test_spike_detection(org2, sent):
    oid = org_of(org2)
    async with SessionLocal() as s:
        set_tenant(s, oid)
        st = await engine.get_settings(s, oid)
        st.state = {**(st.state or {}), "first_run_at": (NOW - timedelta(days=10)).isoformat()}
        for i in range(3):  # baseline: about 3 a week
            s.add(ListeningMention(organization_id=oid, source="reddit", dedupe_key=f"x:base{i}", title=f"base {i}",
                                   published_at=NOW - timedelta(days=2 + i), sentiment="neutral", relevant=True))
        for i in range(8):  # today: 8, 5 negative
            s.add(ListeningMention(organization_id=oid, source="reddit", dedupe_key=f"x:spike{i}", title=f"spike {i}",
                                   published_at=NOW - timedelta(hours=i + 1), relevant=True,
                                   sentiment="negative" if i < 5 else "neutral"))
        await s.commit()
        found = await engine.detect_spikes(s, oid, st)
        assert {f["kind"] for f in found} == {"volume", "negative"}
        assert {x["event"] for x in sent} == {"listening_spike"} and all(x["permission"] == "listening.view" for x in sent)
        # cooldown: no repeat within 12 hours
        assert await engine.detect_spikes(s, oid, st) == []
        # no alerts in the first days (the first run back-fills a week)
        st.state = {**st.state, "first_run_at": NOW.isoformat(), "spikes": {}}
        assert await engine.detect_spikes(s, oid, st) == []


async def test_digest_schedule():
    st = ListeningSettings(digest="daily", state={})
    ist_9am = datetime(2026, 10, 19, 3, 30, tzinfo=UTC)  # Monday 09:00 in India
    ist_6am = datetime(2026, 10, 19, 0, 30, tzinfo=UTC)
    assert engine.digest_due(st, "Asia/Kolkata", ist_9am) and not engine.digest_due(st, "Asia/Kolkata", ist_6am)
    st.state = {"last_digest_at": (ist_9am - timedelta(hours=2)).isoformat()}
    assert not engine.digest_due(st, "Asia/Kolkata", ist_9am)
    wk = ListeningSettings(digest="weekly", state={})
    assert engine.digest_due(wk, "Asia/Kolkata", ist_9am)  # Monday
    assert not engine.digest_due(wk, "Asia/Kolkata", ist_9am + timedelta(days=1))  # Tuesday
    wk.state = {"last_digest_at": (ist_9am - timedelta(days=9)).isoformat()}
    assert engine.digest_due(wk, "Asia/Kolkata", ist_9am + timedelta(days=1))  # missed Monday
    assert not engine.digest_due(ListeningSettings(digest="off", state={}), "Asia/Kolkata", ist_9am)


async def test_digest_send(client, org1, sent):
    r = await client.post("/listening/digest", headers=auth(org1))
    assert r.status_code == 202
    res = await run_inline("listening.digest", org_of(org1), {"force": True})
    assert res["sent"] is True
    msg = next(x for x in sent if x["event"] == "listening_digest")
    assert "weekly summary" in msg["title"] and "mentions this week" in msg["body"] and "Share of voice" in msg["body"]
    assert msg["link"] == "/listening?tab=overview"
    assert (await run_inline("listening.digest", org_of(org1), {}))["sent"] is False  # just sent


async def test_scheduler_picks_due_orgs(org1):
    from app.modules import listening as mod

    due = await mod._due_orgs(datetime.now(UTC) + timedelta(hours=4))
    assert org_of(org1) in due
    assert org_of(org1) not in await mod._due_orgs(datetime.now(UTC))


# ------------------------------------------------------------------ permissions and tenancy
async def test_viewer_can_read_but_not_manage(client, org1):
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(org1))).json()}
    r = await client.post("/users", headers=auth(org1), json={
        "email": "listen-viewer@listen-one.in", "full_name": "Lia Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    v = auth(await login(client, "listen-viewer@listen-one.in", r.json()["temporary_password"]))
    assert (await client.get("/listening/mentions", headers=v)).status_code == 200
    assert (await client.get("/listening/overview", headers=v)).status_code == 200
    mid = (await client.get("/listening/mentions", headers=v)).json()["items"][0]["id"]
    qid = (await client.get("/listening/queries", headers=v)).json()[0]["id"]
    for method, path, body in [
        ("post", "/listening/queries", {"name": "x", "terms": ["xx"]}), ("put", "/listening/settings", {}),
        ("delete", f"/listening/queries/{qid}", None), ("post", "/listening/run", {}),
        ("patch", f"/listening/mentions/{mid}", {"status": "hidden"}), ("post", f"/listening/mentions/{mid}/idea", None),
        ("post", "/listening/queries/starter", {}), ("post", "/listening/digest", None),
    ]:
        r = await getattr(client, method)(path, headers=v, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (path, r.text)


async def test_tenant_isolation(client, org1, org2):
    one = {i["id"] for i in (await client.get("/listening/mentions?limit=200&status=all", headers=auth(org1))).json()["items"]}
    two = {i["id"] for i in (await client.get("/listening/mentions?limit=200&status=all", headers=auth(org2))).json()["items"]}
    assert one and two and not one & two
    mid = next(iter(one))
    assert (await client.patch(f"/listening/mentions/{mid}", headers=auth(org2), json={"status": "hidden"})).status_code == 404
    q1 = (await client.get("/listening/queries", headers=auth(org1))).json()
    assert (await client.delete(f"/listening/queries/{q1[0]['id']}", headers=auth(org2))).status_code == 404


async def test_run_now_and_delete_with_purge(client, org2):
    h = auth(org2)
    r = await client.post("/listening/run", headers=h, json={})
    assert r.status_code == 202 and r.json()["job_id"]
    q = (await client.get("/listening/queries", headers=h)).json()[0]
    before = (await client.get("/listening/mentions?status=all&limit=200", headers=h)).json()["total"]
    assert (await client.delete(f"/listening/queries/{q['id']}?purge=true", headers=h)).status_code == 204
    after = (await client.get("/listening/mentions?status=all&limit=200", headers=h)).json()["total"]
    assert after < before
    r = await client.post("/listening/run", headers=h, json={})
    assert r.status_code == 400  # no topics left
    async with SessionLocal() as s:
        set_tenant(s, org_of(org2))
        assert (await s.scalar(select(ListeningQuery))) is None
