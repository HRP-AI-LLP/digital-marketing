"""Link tracking: the public /r/<code> redirect (UTM merge, anonymous counts, bots skipped, friendly 404, tunnel),
automatic wrapping at publish time (idempotent, never breaks publishing, off by default, skipped where links are
disabled), the analytics API and tenant isolation. No real network."""

import json
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant
from app.models import LinkClick, Post, SocialAccount, TrackedLink
from app.services import links as svc
from tests.test_content import new_org
from tests.test_publishing import do_publish, make_account, make_post, mock  # noqa: F401 - fixtures

ROOT = "http://test"
BASE = "https://studio.example.in"
BROWSER = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) LinkedInApp/9.1"}
DESKTOP = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"}


@pytest.fixture(autouse=True)
def public_base():
    s = get_settings()
    old = s.public_base_url
    s.public_base_url = BASE
    yield
    s.public_base_url = old


@pytest.fixture(scope="module")
async def org(client):
    return await new_org(client)


@pytest.fixture(scope="module")
async def other(client):
    return await new_org(client)


def qs(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def rows(org_id, model, *where):
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return list((await s.scalars(select(model).where(*where))).all())


async def link_by_code(org_id, code) -> TrackedLink:
    return (await rows(org_id, TrackedLink, TrackedLink.code == code))[0]


async def set_tracking(client, o, enabled: bool):
    r = await client.put("/links/settings", headers=o["h"], json={"enabled": enabled})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is enabled and r.json()["base_url"] == BASE


# ============================================================================ helpers
async def test_url_finding_and_replacement():
    text = "Read https://hrpai.in/blog/a. Also (https://hrpai.in/b) and https://hrpai.in/blog/a again!"
    assert svc.find_urls(text) == ["https://hrpai.in/blog/a", "https://hrpai.in/b"]
    out = svc.replace_urls(text, {"https://hrpai.in/blog/a": "S1", "https://hrpai.in/b": "S2"})
    assert out == "Read S1. Also (S2) and S1 again!"
    assert svc.referrer_bucket("https://lnkd.in/xyz") == "linkedin.com"
    assert svc.referrer_bucket("https://www.facebook.com/") == "facebook.com"
    assert svc.referrer_bucket("https://t.co/abc") == "x.com"
    assert svc.referrer_bucket(None) == "direct"
    assert svc.app_bucket(BROWSER["user-agent"]) == "linkedin" and svc.app_bucket(DESKTOP["user-agent"]) == "browser"
    assert svc.device_bucket(BROWSER["user-agent"]) == "mobile"
    assert not svc.countable({"user-agent": "facebookexternalhit/1.1"})
    assert not svc.countable({"user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"})
    assert not svc.countable({**DESKTOP, "sec-purpose": "prefetch"})
    assert svc.countable(DESKTOP)


# ============================================================================ redirect
async def test_manual_link_redirects_with_utm_merged_and_counts(client, org):
    r = await client.post("/links", headers=org["h"], json={
        "url": "https://hrpai.in/hrms?ref=x&utm_source=newsletter", "platform": "linkedin_org", "label": "HRMS page"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert len(d["code"]) == 7 and d["code"].isalnum()
    assert d["short_url"] == f"{BASE}/r/{d['code']}"
    r = await client.get(f"{ROOT}/r/{d['code']}", headers={**BROWSER, "referer": "https://lnkd.in/abc"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["cache-control"] == "no-store"
    loc = r.headers["location"]
    assert loc.startswith("https://hrpai.in/hrms?")
    q = qs(loc)
    assert q["ref"] == "x" and q["utm_source"] == "newsletter"  # the target's own parameters win
    assert q["utm_medium"] == "social" and q["utm_campaign"] == "hrp-studio" and "utm_content" not in q
    link = await link_by_code(org["id"], d["code"])
    assert link.clicks_total == 1 and link.last_click_at is not None
    clicks = await rows(org["id"], LinkClick, LinkClick.link_id == link.id)
    assert [(c.referrer, c.app, c.device, c.count) for c in clicks] == [("linkedin.com", "linkedin", "mobile", 1)]
    await client.get(f"{ROOT}/r/{d['code']}", headers={**BROWSER, "referer": "https://lnkd.in/zzz"}, follow_redirects=False)
    clicks = await rows(org["id"], LinkClick, LinkClick.link_id == link.id)
    assert len(clicks) == 1 and clicks[0].count == 2  # same day + bucket: one row, upserted


async def test_bots_and_previews_redirect_but_are_not_counted(client, org):
    d = (await client.post("/links", headers=org["h"], json={"url": "https://hrpai.in/demo", "platform": "email"})).json()
    for ua in ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", "LinkedInBot/1.0",
               "Twitterbot/1.0", "WhatsApp/2.23", "Slackbot-LinkExpanding 1.0", ""):
        r = await client.get(f"{ROOT}/r/{d['code']}", headers={"user-agent": ua}, follow_redirects=False)
        assert r.status_code == 302, ua
        assert qs(r.headers["location"])["utm_source"] == "email" and qs(r.headers["location"])["utm_medium"] == "email"
    r = await client.head(f"{ROOT}/r/{d['code']}", headers=DESKTOP)
    assert r.status_code == 302
    link = await link_by_code(org["id"], d["code"])
    assert link.clicks_total == 0 and not await rows(org["id"], LinkClick, LinkClick.link_id == link.id)


async def test_unknown_code_is_a_friendly_404_and_tunnel_allows_r(client, org):
    r = await client.get(f"{ROOT}/r/Zz9Zz9Q", headers=DESKTOP)
    assert r.status_code == 404 and "doesn't work any more" in r.text and "text/html" in r.headers["content-type"]
    assert (await client.get(f"{ROOT}/r/bad-code!", headers=DESKTOP)).status_code == 404
    d = (await client.post("/links", headers=org["h"], json={"url": "https://hrpai.in/x"})).json()
    tunnel = {"cf-connecting-ip": "203.0.113.9", "cf-ray": "abc-BOM", **DESKTOP}
    r = await client.get(f"{ROOT}/r/{d['code']}", headers=tunnel, follow_redirects=False)
    assert r.status_code == 302
    assert (await client.get(f"{ROOT}/r/{d['code']}/x", headers=tunnel)).status_code == 404
    assert (await client.get("/links", headers={**tunnel, **org["h"]})).status_code == 404  # API stays private


async def test_create_validates_input(client, org):
    for body in ({"url": "javascript:alert(1)"}, {"url": "ftp://hrpai.in/x"}, {"url": "hrpai.in"},
                 {"url": f"{BASE}/r/abcdefg"}, {"url": "https://hrpai.in", "platform": "myspace"}):
        r = await client.post("/links", headers=org["h"], json=body)
        assert r.status_code == 400, body
    r = await client.post("/links", headers=org["h"], json={"url": "https://hrpai.in/p", "platform": "bio",
                                                             "utm": {"campaign": "Diwali Sale", "bogus": "x"}})
    assert r.status_code == 201
    assert r.json()["utm"] == {"utm_source": "link-in-bio", "utm_medium": "social", "utm_campaign": "Diwali-Sale"}


# ============================================================================ wrapping at publish
def mastodon_capture(mock):
    sent = []

    def statuses(req):
        sent.append(json.loads(req.content))
        import httpx

        return httpx.Response(200, json={"id": str(len(sent)), "url": f"https://m.social/@hrp/{len(sent)}"})

    mock.on("POST", r"m\.social/api/v1/statuses$", statuses)
    return sent


TEXT = "New guide: https://hrpai.in/blog/onboarding. Try the demo https://hrpai.in/demo?x=1"


async def test_wrapping_at_publish_is_idempotent_and_keeps_original_text(client, org, mock):
    await set_tracking(client, org, True)
    oid = org["id"]
    acc = await make_account(oid, "mastodon", f"m:{uuid.uuid4().hex[:6]}", {"access_token": "tok", "instance": "https://m.social"})
    post = await make_post(oid, acc, text=TEXT, link_url="https://hrpai.in/hrms")
    sent = mastodon_capture(mock)
    ext_id, _ = await do_publish(oid, post, acc)
    assert ext_id == "1"
    status = sent[0]["status"]
    assert "hrpai.in/blog" not in status and "hrpai.in/demo" not in status and "hrpai.in/hrms" not in status
    assert status.count(f"{BASE}/r/") == 3 and ". Try the demo" in status  # punctuation kept outside the link
    links = await rows(oid, TrackedLink, TrackedLink.post_id == post.id)
    assert {lk.target_url for lk in links} == {"https://hrpai.in/blog/onboarding", "https://hrpai.in/demo?x=1",
                                               "https://hrpai.in/hrms"}
    for lk in links:
        assert lk.source == "auto" and lk.platform == "mastodon"
        assert lk.utm == {"utm_source": "mastodon", "utm_medium": "social", "utm_campaign": "hrp-studio",
                          "utm_content": str(post.id)[:8]}
    async with SessionLocal() as s:
        set_tenant(s, oid)
        p = await s.get(Post, post.id)
        assert p.text == TEXT and p.link_url == "https://hrpai.in/hrms"  # the editor keeps what was written
        assert {x["url"] for x in p.meta["tracked_links"]} == {lk.target_url for lk in links}
    # Publishing again (a retry) reuses the same links.
    await do_publish(oid, post, acc)
    assert len(await rows(oid, TrackedLink, TrackedLink.post_id == post.id)) == 3
    assert sorted(sent[1]["status"].split()) == sorted(status.split())
    # A browser click on one of them lands on the original page with the post's UTM tags.
    code = next(lk.code for lk in links if lk.target_url.endswith("demo?x=1"))
    r = await client.get(f"{ROOT}/r/{code}", headers=DESKTOP, follow_redirects=False)
    q = qs(r.headers["location"])
    assert r.headers["location"].startswith("https://hrpai.in/demo?") and q["x"] == "1"
    assert q["utm_source"] == "mastodon" and q["utm_content"] == str(post.id)[:8]


async def test_wrapping_uses_campaign_slug(client, org, mock):
    from app.models import Campaign
    from tests.test_content import add

    oid = org["id"]
    camp = await add(oid, Campaign(id=uuid.uuid4(), organization_id=oid, name="Diwali Offer 2026!"))
    acc = await make_account(oid, "mastodon", f"m:{uuid.uuid4().hex[:6]}", {"access_token": "tok", "instance": "https://m.social"})
    post = await make_post(oid, acc, text="Offer: https://hrpai.in/offer", campaign_id=camp.id)
    mastodon_capture(mock)
    await do_publish(oid, post, acc)
    lk = (await rows(oid, TrackedLink, TrackedLink.post_id == post.id))[0]
    assert lk.utm["utm_campaign"] == "diwali-offer-2026" and lk.campaign_id == camp.id


async def test_wrapping_falls_back_to_original_urls_on_error(client, org, mock, monkeypatch):
    await set_tracking(client, org, True)
    oid = org["id"]

    async def broken(*a, **k):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(svc, "links_for_post", broken)
    acc = await make_account(oid, "mastodon", f"m:{uuid.uuid4().hex[:6]}", {"access_token": "tok", "instance": "https://m.social"})
    post = await make_post(oid, acc, text=TEXT)
    sent = mastodon_capture(mock)
    ext_id, _ = await do_publish(oid, post, acc)
    assert ext_id == "1" and sent[0]["status"].startswith(TEXT)
    assert not await rows(oid, TrackedLink, TrackedLink.post_id == post.id)


async def test_no_wrapping_when_off_or_without_public_address(client, org, mock):
    oid = org["id"]
    acc = await make_account(oid, "mastodon", f"m:{uuid.uuid4().hex[:6]}", {"access_token": "tok", "instance": "https://m.social"})
    await set_tracking(client, org, False)
    post = await make_post(oid, acc, text=TEXT)
    sent = mastodon_capture(mock)
    await do_publish(oid, post, acc)
    assert sent[0]["status"].startswith(TEXT) and not await rows(oid, TrackedLink, TrackedLink.post_id == post.id)
    await set_tracking(client, org, True)
    get_settings().public_base_url = ""
    post2 = await make_post(oid, acc, text=TEXT)
    await do_publish(oid, post2, acc)
    assert sent[1]["status"].startswith(TEXT) and not await rows(oid, TrackedLink, TrackedLink.post_id == post2.id)


async def test_skipped_where_links_are_disabled(client, org):
    await set_tracking(client, org, True)
    oid = org["id"]
    for platform, settings, expect in (("x", {"allow_links": False}, False), ("x", {"allow_links": True}, True),
                                       ("instagram", {}, False), ("linkedin_org", {}, True)):
        acc = await make_account(oid, platform, f"{platform}:{uuid.uuid4().hex[:6]}", {"access_token": "t"}, settings=settings)
        post = await make_post(oid, acc, text="See https://hrpai.in/z")
        async with SessionLocal() as s:
            set_tenant(s, oid)
            p, a = await s.get(Post, post.id), await s.get(SocialAccount, acc.id)
            restore = await svc.wrap_for_publish(s, p, a)
            assert (f"{BASE}/r/" in p.text) is expect, platform
            restore()
            assert p.text == "See https://hrpai.in/z"
            await s.rollback()


async def test_skipped_when_short_link_would_break_the_length_limit(client, org):
    """Bluesky allows 300 characters: a post that fits with its own URL must not fail because the short link is longer."""
    await set_tracking(client, org, True)
    oid = org["id"]
    acc = await make_account(oid, "bluesky", f"b:{uuid.uuid4().hex[:6]}", {"access_token": "t"})
    url = "https://hrpai.in/a"
    fits = "x" * (300 - len(url) - 1) + " " + url            # exactly 300 with the original URL
    short = "Short post " + url
    for text, wrapped in ((fits, False), (short, True)):
        post = await make_post(oid, acc, text=text)
        async with SessionLocal() as s:
            set_tenant(s, oid)
            p, a = await s.get(Post, post.id), await s.get(SocialAccount, acc.id)
            restore = await svc.wrap_for_publish(s, p, a)
            assert (f"{BASE}/r/" in p.text) is wrapped
            restore()
            assert p.text == text
            await s.rollback()


# ============================================================================ analytics API
async def test_list_and_series_api(client, org):
    oid = org["id"]
    d = (await client.post("/links", headers=org["h"], json={"url": "https://hrpai.in/series", "platform": "facebook_page",
                                                              "label": "Series test"})).json()
    for ref in ("https://l.facebook.com/", "https://l.facebook.com/", None):
        await client.get(f"{ROOT}/r/{d['code']}", headers={**DESKTOP, **({"referer": ref} if ref else {})},
                         follow_redirects=False)
    r = await client.get("/links", headers=org["h"], params={"days": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["series"]) == 7 and sum(x["clicks"] for x in body["series"]) == body["kpis"]["clicks"]
    item = next(i for i in body["items"] if i["id"] == d["id"])
    assert item["clicks"] == 3 and item["clicks_total"] == 3 and item["label"] == "Series test"
    assert body["settings"]["base_url"] == BASE
    assert any(x["key"] == "facebook_page" and x["clicks"] >= 3 for x in body["by_platform"])
    assert any(p["clicks"] >= 1 for p in body["top_posts"])  # the mastodon post's demo link was clicked
    # Filters.
    f = (await client.get("/links", headers=org["h"], params={"platform": "facebook_page"})).json()
    assert {i["platform"] for i in f["items"]} == {"facebook_page"} and f["kpis"]["clicks"] >= 3
    none = (await client.get("/links", headers=org["h"], params={"campaign_id": str(uuid.uuid4())})).json()
    assert none["items"] == [] and none["kpis"]["clicks"] == 0
    # One link's daily series and breakdowns.
    r = await client.get(f"/links/{d['id']}", headers=org["h"], params={"days": 14})
    one = r.json()
    assert r.status_code == 200 and len(one["series"]) == 14 and one["series"][-1]["clicks"] == 3
    assert {x["key"]: x["clicks"] for x in one["by_referrer"]} == {"facebook.com": 2, "direct": 1}
    assert (await client.get("/links", headers=org["h"], params={"start": "nope"})).status_code == 400
    # Rename and delete a manual link.
    assert (await client.patch(f"/links/{d['id']}", headers=org["h"], json={"label": "Renamed"})).json()["label"] == "Renamed"
    assert (await client.delete(f"/links/{d['id']}", headers=org["h"])).status_code == 204
    assert (await client.get(f"{ROOT}/r/{d['code']}", headers=DESKTOP)).status_code == 404
    auto = (await rows(oid, TrackedLink, TrackedLink.source == "auto"))[0]
    assert (await client.delete(f"/links/{auto.id}", headers=org["h"])).status_code == 400


async def test_tenant_isolation(client, org, other):
    d = (await client.post("/links", headers=org["h"], json={"url": "https://hrpai.in/secret"})).json()
    assert (await client.get(f"/links/{d['id']}", headers=other["h"])).status_code == 404
    assert (await client.delete(f"/links/{d['id']}", headers=other["h"])).status_code == 404
    listing = (await client.get("/links", headers=other["h"])).json()
    assert all(i["id"] != d["id"] for i in listing["items"]) and listing["kpis"]["clicks"] == 0
    assert (await client.get("/links/settings", headers=other["h"])).json()["enabled"] is False
    async with SessionLocal() as s:
        set_tenant(s, other["id"])
        assert await s.scalar(select(func.count()).select_from(TrackedLink)) == 0
    # The public link still works for everyone, and clicks land with its owner.
    r = await client.get(f"{ROOT}/r/{d['code']}", headers=DESKTOP, follow_redirects=False)
    assert r.status_code == 302
    assert (await link_by_code(org["id"], d["code"])).clicks_total == 1


async def test_links_report_builds(client, org):
    from app.models import Organization
    from app.services import reports as rep

    async with SessionLocal() as s:
        set_tenant(s, org["id"])
        o = await s.get(Organization, org["id"])
        _, doc, _ = await rep.build(s, o, "links", {})
        assert doc.title == "Link clicks"
        titles = [b.get("title") for b in doc.blocks]
        assert "Clicks per day" in titles and "Top posts" in titles and "Links" in titles
        assert "Link clicks" in rep.render_html(doc, o, "data:image/png;base64,", "now")
