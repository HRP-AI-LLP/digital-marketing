"""Publishing & social accounts: OAuth flows, adapters' request shapes, error mapping, token refresh,
permissions and tenant isolation. No real network: adapters' HTTP client is replaced with
httpx.MockTransport (app.services.social.net.client)."""

import json
import re
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import MediaAsset, OAuthState, Post, ProviderConfig, SocialAccount
from app.services import publishing
from app.services.publishing import PublishError, account_tokens, save_tokens
from app.services.social import common, net
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"


# ------------------------------------------------------------------ HTTP mock
class Mock:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, object]] = []
        self.calls: list[httpx.Request] = []

    def on(self, method: str, pattern: str, reply):
        """reply: (status, json) | (status, json, headers) | callable(request) -> httpx.Response"""
        self.routes.insert(0, (method, re.compile(pattern), reply))  # later registrations win
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.calls.append(request)
        url = str(request.url.copy_with(query=None))
        for method, pat, reply in self.routes:
            if method == request.method and pat.search(url):
                if callable(reply):
                    return reply(request)
                status, body, *rest = reply
                headers = rest[0] if rest else {}
                return httpx.Response(status, json=body, headers=headers)
        return httpx.Response(404, json={"error": {"message": f"no mock for {request.method} {url}"}})

    def find(self, method: str, pattern: str) -> list[httpx.Request]:
        return [r for r in self.calls if r.method == method and re.search(pattern, str(r.url.copy_with(query=None)))]


@pytest.fixture
def mock(monkeypatch):
    m = Mock()
    monkeypatch.setattr(net, "client", lambda timeout=60.0: httpx.AsyncClient(transport=httpx.MockTransport(m.handler)))

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(common, "sleep", no_sleep)
    return m


@pytest.fixture
def public_media(monkeypatch):
    """Stand-in for app.services.media.ensure_public_url (built by another module)."""
    async def ensure_public_url(session, asset, variant=None):
        ext = "jpeg" if variant == "jpg" else asset.mime.split('/')[-1]
        return f"https://cdn.example.com/{asset.id}.{ext}"

    async def ensure_jpeg(session, asset):
        return "" if asset.mime == "image/jpeg" else "jpg"

    try:
        import app.services.media as media_mod
    except ImportError:
        media_mod = types.ModuleType("app.services.media")
        monkeypatch.setitem(sys.modules, "app.services.media", media_mod)
    monkeypatch.setattr(media_mod, "ensure_public_url", ensure_public_url, raising=False)
    monkeypatch.setattr(media_mod, "ensure_jpeg", ensure_jpeg, raising=False)


@pytest.fixture(scope="module", autouse=True)
def temp_storage(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage"))
    yield
    s.storage_dir = old


def form(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def query(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(req.url)).query).items()}


# ------------------------------------------------------------------ fixtures & helpers
@pytest.fixture(scope="module")
async def hrp(client, admin):
    t = await login(client, "admin@hrpai.in", ADMIN_PW)
    for provider, secrets in {
        "meta_app": {"client_id": "meta-id", "client_secret": "meta-secret"},
        "linkedin_app": {"client_id": "li-id", "client_secret": "li-secret"},
        "pinterest_app": {"client_id": "pin-id", "client_secret": "pin-secret"},
        "google_app": {"client_id": "g-id", "client_secret": "g-secret"},
    }.items():
        r = await client.post("/integrations/providers", headers=auth(t), json={"provider": provider, "secrets": secrets})
        assert r.status_code == 201, r.text
    return t


@pytest.fixture(scope="module")
async def acme(client, hrp):
    r = await client.post("/platform/organizations", headers=auth(hrp),
                          json={"name": "Pub Acme", "admin_email": "owner@pub-acme.in", "admin_name": "Acme Owner"})
    assert r.status_code == 201, r.text
    return await login(client, "owner@pub-acme.in", r.json()["temporary_password"])


def org_of(t: dict) -> uuid.UUID:
    return uuid.UUID(t["organization"]["id"])


async def make_account(org_id, platform, external_id, tokens=None, settings=None, status="connected",
                       expires_at=None, name=None) -> SocialAccount:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        a = SocialAccount(organization_id=org_id, platform=platform, external_id=external_id,
                          name=name or f"{platform} {external_id}", status=status, settings=settings or {},
                          handle="hrpai")
        save_tokens(a, tokens or {}, expires_at)
        if not tokens:
            a.tokens_enc = None
        s.add(a)
        await s.commit()
        return a


async def make_media(org_id, kind="image", mime="image/png", data=b"\x89PNG-fake", **kw) -> MediaAsset:
    from app.core.storage import save_bytes

    rel, sha = save_bytes(org_id, "media", data, mime.split("/")[-1])
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        m = MediaAsset(organization_id=org_id, kind=kind, mime=mime, path=rel, sha256=sha, source="upload",
                       size_bytes=len(data), **kw)
        s.add(m)
        await s.commit()
        return m


async def make_post(org_id, account, post_type="text", media=(), **kw) -> Post:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        p = Post(organization_id=org_id, social_account_id=account.id, platform=account.platform, post_type=post_type,
                 status="publishing", text=kw.pop("text", "Hello from HRP AI"), media_ids=[str(m.id) for m in media], **kw)
        s.add(p)
        await s.commit()
        return p


async def do_publish(org_id, post, account):
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        p = await s.get(Post, post.id)
        a = await s.get(SocialAccount, account.id)
        try:
            return await publishing.publish(s, p, a)
        finally:
            await s.commit()


async def do_refresh(org_id, account) -> SocialAccount:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        a = await s.get(SocialAccount, account.id)
        await publishing.refresh_account(s, a)
        await s.commit()
        return a


async def start_oauth(client, t, provider, body=None) -> tuple[str, str]:
    r = await client.post(f"/social/connect/{provider}", headers=auth(t), json=body)
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    return url, parse_qs(urlsplit(url).query)["state"][0]


# ------------------------------------------------------------------ platforms & OAuth
async def test_platforms_list_connectors_guides_and_redirect_uris(client, hrp):
    r = await client.get("/social/platforms", headers=auth(hrp))
    assert r.status_code == 200
    d = r.json()
    conn = {c["id"]: c for c in d["connectors"]}
    assert conn["meta"]["redirect_uri"].endswith("/api/v1/social/oauth/meta/callback")
    assert conn["meta"]["app_configured"] is True
    assert "Facebook Login" in conn["meta"]["guide"]
    assert conn["bluesky"]["method"] == "app_password" and conn["bluesky"]["redirect_uri"] is None
    assert {p["id"] for p in d["platforms"]} >= {"linkedin_org", "instagram", "gbp", "x"}
    assert "cloudflared" in d["media_guide"]


async def test_connect_builds_authorize_urls_and_stores_state(client, hrp):
    url, state = await start_oauth(client, hrp, "meta")
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://www.facebook.com/")
    assert q["client_id"] == ["meta-id"]
    assert q["redirect_uri"][0].endswith("/social/oauth/meta/callback")
    assert "pages_manage_posts" in q["scope"][0] and "instagram" not in q["scope"][0]
    ig_url, _ = await start_oauth(client, hrp, "meta", {"include_instagram": True})
    assert "instagram_content_publish" in parse_qs(urlsplit(ig_url).query)["scope"][0]
    async with platform_session() as s:
        st = await s.get(OAuthState, state)
        assert st is not None and st.platform == "meta" and str(st.organization_id) == hrp["organization"]["id"]

    url, _ = await start_oauth(client, hrp, "linkedin")
    assert "w_organization_social" not in parse_qs(urlsplit(url).query)["scope"][0]
    url, _ = await start_oauth(client, hrp, "linkedin", {"company_pages": True})
    scope = parse_qs(urlsplit(url).query)["scope"][0]
    assert "w_organization_social" in scope and "w_member_social" in scope

    url, state = await start_oauth(client, hrp, "google")
    q = parse_qs(urlsplit(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
    assert "https://www.googleapis.com/auth/business.manage" in q["scope"][0]
    async with platform_session() as s:
        assert (await s.get(OAuthState, state)).data["code_verifier"]


async def test_connect_without_app_credentials_is_refused(client, acme):
    r = await client.post("/social/connect/pinterest", headers=auth(acme))
    assert r.status_code == 400
    assert "Integrations" in r.json()["detail"]


async def test_meta_callback_creates_page_and_instagram_accounts(client, hrp, mock):
    _, state = await start_oauth(client, hrp, "meta", {"include_instagram": True})

    def token(req):
        q = query(req)
        if q.get("grant_type") == "fb_exchange_token":
            assert q["fb_exchange_token"] == "short-tok"
            return httpx.Response(200, json={"access_token": "long-user-tok", "expires_in": 5184000})
        assert q["code"] == "the-code" and q["client_secret"] == "meta-secret"
        return httpx.Response(200, json={"access_token": "short-tok"})

    mock.on("GET", r"/oauth/access_token$", token)
    mock.on("GET", r"/me/accounts$", (200, {"data": [{
        "id": "111", "name": "HRP AI", "access_token": "page-tok", "link": "https://facebook.com/hrpai",
        "picture": {"data": {"url": "https://img/p.png"}},
        "instagram_business_account": {"id": "17841", "username": "hrpai", "profile_picture_url": "https://img/i.png"},
    }]}))
    r = await client.get(f"/social/oauth/meta/callback?code=the-code&state={state}")
    assert r.status_code == 302
    assert "connected=meta" in r.headers["location"] and "count=2" in r.headers["location"]

    accounts = (await client.get("/social/accounts", headers=auth(hrp))).json()
    fb = next(a for a in accounts if a["platform"] == "facebook_page" and a["name"] == "HRP AI")
    ig = next(a for a in accounts if a["platform"] == "instagram")
    assert fb["status"] == "connected" and ig["profile_url"] == "https://www.instagram.com/hrpai/"
    assert "page-tok" not in json.dumps(accounts) and "tokens_enc" not in fb
    async with SessionLocal() as s:
        set_tenant(s, org_of(hrp))
        row = await s.get(SocialAccount, uuid.UUID(ig["id"]))
        assert account_tokens(row)["access_token"] == "page-tok"

    # The state is single-use.
    r = await client.get(f"/social/oauth/meta/callback?code=the-code&state={state}")
    assert "error=" in r.headers["location"]


async def test_linkedin_callback_creates_person_and_company_page(client, hrp, mock):
    _, state = await start_oauth(client, hrp, "linkedin", {"company_pages": True})
    mock.on("POST", r"linkedin\.com/oauth/v2/accessToken$", (200, {
        "access_token": "li-tok", "expires_in": 5184000, "refresh_token": "li-refresh", "refresh_token_expires_in": 31536000}))
    mock.on("GET", r"/v2/userinfo$", (200, {"sub": "abc123", "name": "Prashant S", "email": "p@hrpai.in"}))
    mock.on("GET", r"/rest/organizationAcls$", (200, {"elements": [
        {"organization": "urn:li:organization:9001", "role": "ADMINISTRATOR", "state": "APPROVED"}]}))
    mock.on("GET", r"/rest/organizations/9001$", (200, {"localizedName": "HRP AI LLP", "vanityName": "hrp-ai"}))
    r = await client.get(f"/social/oauth/linkedin/callback?code=c0de&state={state}")
    assert "connected=linkedin" in r.headers["location"] and "count=2" in r.headers["location"], r.headers["location"]
    acl = mock.find("GET", r"/rest/organizationAcls$")[0]
    assert acl.headers["LinkedIn-Version"] and acl.headers["X-Restli-Protocol-Version"] == "2.0.0"
    accounts = (await client.get("/social/accounts", headers=auth(hrp))).json()
    person = next(a for a in accounts if a["platform"] == "linkedin_person")
    org = next(a for a in accounts if a["platform"] == "linkedin_org")
    assert person["name"] == "Prashant S" and org["name"] == "HRP AI LLP"
    assert org["profile_url"] == "https://www.linkedin.com/company/hrp-ai/"
    assert org["token_expires_at"]


async def test_linkedin_unapproved_company_scopes_explained(client, hrp):
    _, state = await start_oauth(client, hrp, "linkedin", {"company_pages": True})
    r = await client.get(f"/social/oauth/linkedin/callback?error=unauthorized_scope_error"
                         f"&error_description=Scope+not+authorized&state={state}")
    assert r.status_code == 302
    assert "Community+Management+API" in r.headers["location"]


async def test_viewer_cannot_connect_or_remove(client, hrp):
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(hrp))).json()}
    r = await client.post("/users", headers=auth(hrp), json={
        "email": "social-viewer@hrpai.in", "full_name": "Sam Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    v = await login(client, "social-viewer@hrpai.in", r.json()["temporary_password"])
    assert (await client.get("/social/accounts", headers=auth(v))).status_code == 200
    r = await client.post("/social/connect/meta", headers=auth(v))
    assert r.status_code == 403 and "integration.manage" in r.json()["detail"]
    r = await client.post("/social/accounts/manual", headers=auth(v), json={"platform": "x", "name": "X"})
    assert r.status_code == 403


async def test_tenant_isolation(client, hrp, acme):
    r = await client.post("/social/accounts/manual", headers=auth(hrp),
                          json={"platform": "x", "name": "HRP on X", "handle": "@hrpai"})
    assert r.status_code == 201 and r.json()["status"] == "manual"
    hrp_acc = r.json()["id"]
    acme_list = (await client.get("/social/accounts", headers=auth(acme))).json()
    assert all(a["id"] != hrp_acc for a in acme_list)
    assert (await client.patch(f"/social/accounts/{hrp_acc}", headers=auth(acme), json={"name": "pwned"})).status_code == 404
    assert (await client.delete(f"/social/accounts/{hrp_acc}", headers=auth(acme))).status_code == 404
    assert (await client.post(f"/social/accounts/{hrp_acc}/refresh", headers=auth(acme))).status_code == 404


async def test_accounts_list_hides_google_holder_and_remove_is_soft(client, hrp):
    org = org_of(hrp)
    holder = await make_account(org, "google", "g-sub-1", {"access_token": "g", "refresh_token": "r"},
                                expires_at=datetime.now(UTC) + timedelta(hours=1))
    ids = [a["id"] for a in (await client.get("/social/accounts", headers=auth(hrp))).json()]
    assert str(holder.id) not in ids
    ids_all = [a["id"] for a in (await client.get("/social/accounts?all=true", headers=auth(hrp))).json()]
    assert str(holder.id) in ids_all

    m = await make_account(org, "mastodon", "m:1", {"access_token": "tok", "instance": "https://m.social"})
    assert (await client.delete(f"/social/accounts/{m.id}", headers=auth(hrp))).status_code == 204
    assert str(m.id) not in [a["id"] for a in (await client.get("/social/accounts", headers=auth(hrp))).json()]
    async with SessionLocal() as s:
        set_tenant(s, org)
        row = await s.get(SocialAccount, m.id)
        assert row is not None and row.status == "removed" and row.tokens_enc is None


# ------------------------------------------------------------------ publishing request shapes
async def test_facebook_photo_post(hrp, mock):
    org = org_of(hrp)
    acc = await make_account(org, "facebook_page", "555", {"access_token": "page-tok"})
    img = await make_media(org)
    post = await make_post(org, acc, "image", [img], text="New feature!", hashtags=["HRTech", "AI"])
    mock.on("POST", r"/555/photos$", (200, {"id": "photo1", "post_id": "555_999"}))
    ext, url = await do_publish(org, post, acc)
    assert ext == "555_999" and url == "https://www.facebook.com/555_999"
    req = mock.find("POST", r"/555/photos$")[0]
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    assert b'name="caption"' in body and b"New feature!\n\n#HRTech #AI" in body
    assert b'name="source"' in body and b"\x89PNG-fake" in body and b"page-tok" in body


async def test_facebook_multi_photo_uses_attached_media(hrp, mock):
    org = org_of(hrp)
    acc = await make_account(org, "facebook_page", "556", {"access_token": "page-tok"})
    imgs = [await make_media(org), await make_media(org)]
    post = await make_post(org, acc, "carousel", imgs)
    n = iter(["p1", "p2"])
    mock.on("POST", r"/556/photos$", lambda req: httpx.Response(200, json={"id": next(n)}))
    mock.on("POST", r"/556/feed$", (200, {"id": "556_1"}))
    ext, _ = await do_publish(org, post, acc)
    assert ext == "556_1"
    assert all(b'name="published"' in r.content for r in mock.find("POST", r"/556/photos$"))
    f = form(mock.find("POST", r"/556/feed$")[0])
    assert json.loads(f["attached_media[0]"]) == {"media_fbid": "p1"} and json.loads(f["attached_media[1]"]) == {"media_fbid": "p2"}


async def test_instagram_carousel_container_flow(hrp, mock, public_media):
    org = org_of(hrp)
    acc = await make_account(org, "instagram", "1789", {"access_token": "page-tok", "page_id": "555"})
    imgs = [await make_media(org), await make_media(org, mime="image/jpeg")]
    post = await make_post(org, acc, "carousel", imgs, text="Swipe →")
    n = iter(["c1", "c2", "parent"])
    mock.on("POST", r"/1789/media$", lambda req: httpx.Response(200, json={"id": next(n)}))
    mock.on("GET", r"/parent$", (200, {"status_code": "FINISHED"}))
    mock.on("POST", r"/1789/media_publish$", (200, {"id": "ig-media-1"}))
    mock.on("GET", r"/ig-media-1$", (200, {"permalink": "https://www.instagram.com/p/xyz/"}))
    ext, url = await do_publish(org, post, acc)
    assert (ext, url) == ("ig-media-1", "https://www.instagram.com/p/xyz/")
    calls = [form(r) for r in mock.find("POST", r"/1789/media$")]
    assert calls[0]["is_carousel_item"] == "true" and calls[0]["image_url"] == f"https://cdn.example.com/{imgs[0].id}.jpeg"
    assert calls[1]["image_url"].endswith(".jpeg")
    assert calls[2]["media_type"] == "CAROUSEL" and calls[2]["children"] == "c1,c2" and calls[2]["caption"] == "Swipe →"
    assert form(mock.find("POST", r"/media_publish$")[0])["creation_id"] == "parent"


async def test_instagram_reel_waits_for_processing(hrp, mock, public_media):
    org = org_of(hrp)
    acc = await make_account(org, "instagram", "1790", {"access_token": "page-tok"})
    vid = await make_media(org, kind="video", mime="video/mp4", data=b"mp4")
    post = await make_post(org, acc, "video", [vid])
    mock.on("POST", r"/1790/media$", (200, {"id": "reel"}))
    states = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    mock.on("GET", r"/reel$", lambda req: httpx.Response(200, json={"status_code": next(states)}))
    mock.on("POST", r"/1790/media_publish$", (200, {"id": "r1"}))
    ext, _ = await do_publish(org, post, acc)
    assert ext == "r1" and len(mock.find("GET", r"/reel$")) == 3
    assert form(mock.find("POST", r"/1790/media$")[0])["media_type"] == "REELS"


async def test_linkedin_image_upload_and_post(hrp, mock):
    org = org_of(hrp)
    urn = "urn:li:organization:9001"
    acc = await make_account(org, "linkedin_org", urn, {"access_token": "li-tok"}, {"org_urn": urn})
    img = await make_media(org, alt_text="Team photo")
    post = await make_post(org, acc, "image", [img], text="We're hiring (Pune) #AI_jobs")
    mock.on("POST", r"api\.linkedin\.com/rest/images$", (200, {"value": {
        "uploadUrl": "https://www.linkedin.com/dms-uploads/abc", "image": "urn:li:image:C4D"}}))
    mock.on("PUT", r"dms-uploads/abc$", (201, {}))
    mock.on("POST", r"api\.linkedin\.com/rest/posts$", (201, {}, {"x-restli-id": "urn:li:share:777"}))
    ext, url = await do_publish(org, post, acc)
    assert ext == "urn:li:share:777" and url == "https://www.linkedin.com/feed/update/urn:li:share:777/"
    init = mock.find("POST", r"/rest/images$")[0]
    assert query(init)["action"] == "initializeUpload"
    assert json.loads(init.content) == {"initializeUploadRequest": {"owner": urn}}
    assert mock.find("PUT", r"dms-uploads/abc$")[0].content == b"\x89PNG-fake"
    p = mock.find("POST", r"/rest/posts$")[0]
    assert p.headers["LinkedIn-Version"].isdigit() and p.headers["X-Restli-Protocol-Version"] == "2.0.0"
    body = json.loads(p.content)
    assert body["author"] == urn and body["lifecycleState"] == "PUBLISHED" and body["visibility"] == "PUBLIC"
    assert body["content"] == {"media": {"id": "urn:li:image:C4D", "altText": "Team photo"}}
    assert body["commentary"] == r"We're hiring \(Pune\) {hashtag|\#|AI\_jobs}"


async def test_pinterest_base64_pin(hrp, mock):
    org = org_of(hrp)
    acc = await make_account(org, "pinterest", "pin-user", {"access_token": "pin-tok"}, {"board_id": "b42"})
    img = await make_media(org)
    post = await make_post(org, acc, "image", [img], title="10 HR automation ideas", text="Save these.",
                           link_url="https://hrpai.in/blog/hr")
    mock.on("POST", r"api\.pinterest\.com/v5/pins$", (201, {"id": "9988"}))
    ext, url = await do_publish(org, post, acc)
    assert (ext, url) == ("9988", "https://www.pinterest.com/pin/9988/")
    req = mock.find("POST", r"/v5/pins$")[0]
    body = json.loads(req.content)
    assert req.headers["Authorization"] == "Bearer pin-tok"
    assert body["board_id"] == "b42" and body["title"] == "10 HR automation ideas" and body["link"] == "https://hrpai.in/blog/hr"
    assert body["media_source"]["source_type"] == "image_base64" and body["media_source"]["content_type"] == "image/png"
    import base64
    assert base64.b64decode(body["media_source"]["data"]) == b"\x89PNG-fake"


async def test_gbp_local_post_with_photo_and_cta(hrp, mock, public_media):
    org = org_of(hrp)
    await make_account(org, "google", "g-sub-gbp", {"access_token": "g-tok", "refresh_token": "g-ref"},
                       expires_at=datetime.now(UTC) + timedelta(hours=1))
    acc = await make_account(org, "gbp", "accounts/1/locations/2", None, {"account": "accounts/1", "location": "locations/2"})
    img = await make_media(org)
    post = await make_post(org, acc, "image", [img], text="Visit our new office #news", hashtags=["HR"],
                           link_url="https://hrpai.in")
    mock.on("POST", r"mybusiness\.googleapis\.com/v4/accounts/1/locations/2/localPosts$",
            (200, {"name": "accounts/1/locations/2/localPosts/xyz", "searchUrl": "https://local.google.com/p/xyz"}))
    ext, url = await do_publish(org, post, acc)
    assert ext == "accounts/1/locations/2/localPosts/xyz" and url == "https://local.google.com/p/xyz"
    req = mock.find("POST", r"/localPosts$")[0]
    body = json.loads(req.content)
    assert req.headers["Authorization"].startswith("Bearer ")
    assert body["topicType"] == "STANDARD" and body["summary"] == "Visit our new office #news"
    assert body["callToAction"] == {"actionType": "LEARN_MORE", "url": "https://hrpai.in"}
    assert body["media"] == [{"mediaFormat": "PHOTO", "sourceUrl": f"https://cdn.example.com/{img.id}.png"}]


async def test_bluesky_facets_and_link_card(hrp, mock):
    org = org_of(hrp)
    acc = await make_account(org, "bluesky", "did:plc:abc", {
        "access_jwt": "old", "refresh_jwt": "ref", "service": "https://bsky.social", "did": "did:plc:abc",
        "app_password": "pw"})
    post = await make_post(org, acc, "link", text="Read ✨ https://hrpai.in/ai #HRTech", link_url="https://hrpai.in/ai",
                           title="AI for HR")
    mock.on("POST", r"com\.atproto\.server\.refreshSession$", (200, {"accessJwt": "new-access", "refreshJwt": "new-ref",
                                                                       "did": "did:plc:abc"}))
    mock.on("POST", r"com\.atproto\.repo\.createRecord$", (200, {"uri": "at://did:plc:abc/app.bsky.feed.post/3kxyz",
                                                                   "cid": "c"}))
    ext, url = await do_publish(org, post, acc)
    assert ext == "at://did:plc:abc/app.bsky.feed.post/3kxyz" and url == "https://bsky.app/profile/hrpai/post/3kxyz"
    req = mock.find("POST", r"createRecord$")[0]
    assert req.headers["Authorization"] == "Bearer new-access"
    rec = json.loads(req.content)["record"]
    text = rec["text"].encode()
    link = next(f for f in rec["facets"] if f["features"][0]["$type"].endswith("#link"))
    tag = next(f for f in rec["facets"] if f["features"][0]["$type"].endswith("#tag"))
    assert text[link["index"]["byteStart"]:link["index"]["byteEnd"]] == b"https://hrpai.in/ai"
    assert text[tag["index"]["byteStart"]:tag["index"]["byteEnd"]] == b"#HRTech" and tag["features"][0]["tag"] == "HRTech"
    assert rec["embed"]["$type"] == "app.bsky.embed.external" and rec["embed"]["external"]["uri"] == "https://hrpai.in/ai"
    async with SessionLocal() as s:
        set_tenant(s, org)
        assert account_tokens(await s.get(SocialAccount, acc.id))["refresh_jwt"] == "new-ref"


async def test_bluesky_add_account_endpoint(client, hrp, mock):
    mock.on("POST", r"createSession$", (200, {"accessJwt": "a", "refreshJwt": "r", "did": "did:plc:new",
                                               "handle": "hrpai.bsky.social"}))
    mock.on("GET", r"app\.bsky\.actor\.getProfile$", (200, {"displayName": "HRP AI", "avatar": "https://img/a.jpg"}))
    r = await client.post("/social/accounts/bluesky", headers=auth(hrp),
                          json={"handle": "@hrpai.bsky.social", "app_password": "abcd-efgh-ijkl-mnop"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "HRP AI" and r.json()["profile_url"] == "https://bsky.app/profile/hrpai.bsky.social"
    assert json.loads(mock.find("POST", r"createSession$")[0].content)["identifier"] == "hrpai.bsky.social"
    mock.on("POST", r"createSession$", (401, {"error": "AuthenticationRequired", "message": "Invalid identifier or password"}))
    r = await client.post("/social/accounts/bluesky", headers=auth(hrp), json={"handle": "x.bsky.social", "app_password": "nope"})
    assert r.status_code == 400 and "app password" in r.json()["detail"]


async def test_mastodon_media_is_polled_then_posted(hrp, mock):
    org = org_of(hrp)
    acc = await make_account(org, "mastodon", "m.social:42", {"access_token": "m-tok", "instance": "https://m.social"})
    vid = await make_media(org, kind="video", mime="video/mp4", data=b"mp4", alt_text="Demo clip")
    post = await make_post(org, acc, "video", [vid], text="Demo")
    mock.on("POST", r"m\.social/api/v2/media$", (202, {"id": "77", "url": None}))
    states = iter([httpx.Response(206, json={"id": "77", "url": None}),
                   httpx.Response(200, json={"id": "77", "url": "https://m.social/m/77.mp4"})])
    mock.on("GET", r"m\.social/api/v1/media/77$", lambda req: next(states))
    mock.on("POST", r"m\.social/api/v1/statuses$", (200, {"id": "1001", "url": "https://m.social/@hrp/1001"}))
    ext, url = await do_publish(org, post, acc)
    assert (ext, url) == ("1001", "https://m.social/@hrp/1001")
    up = mock.find("POST", r"/api/v2/media$")[0]
    assert b'name="description"' in up.content and b"Demo clip" in up.content
    st = mock.find("POST", r"/api/v1/statuses$")[0]
    assert json.loads(st.content)["media_ids"] == ["77"] and st.headers["Idempotency-Key"] == str(post.id)
    assert len(mock.find("GET", r"/api/v1/media/77$")) == 2


# ------------------------------------------------------------------ validation & error mapping
async def test_unsupported_post_type_and_manual_accounts(hrp):
    org = org_of(hrp)
    ig = await make_account(org, "instagram", "1791", {"access_token": "t"})
    with pytest.raises(PublishError) as e:
        await do_publish(org, await make_post(org, ig, "text"), ig)
    assert "doesn't support" in e.value.message and not e.value.retryable
    x = await make_account(org, "x", "manual:x1", None, status="manual")
    with pytest.raises(PublishError) as e:
        await do_publish(org, await make_post(org, x, "text"), x)
    assert "by hand" in e.value.message


async def test_error_mapping(hrp, mock):
    org = org_of(hrp)
    pin = await make_account(org, "pinterest", "pin-err", {"access_token": "bad"}, {"board_id": "b1"})
    img = await make_media(org)
    post = await make_post(org, pin, "image", [img])
    mock.on("POST", r"/v5/pins$", (401, {"code": 2, "message": "Authentication failed."}))
    with pytest.raises(PublishError) as e:
        await do_publish(org, post, pin)
    assert e.value.reconnect and not e.value.retryable

    mock.on("POST", r"/v5/pins$", (429, {"code": 8, "message": "Rate limit exceeded"}))
    with pytest.raises(PublishError) as e:
        await do_publish(org, post, pin)
    assert e.value.retryable and not e.value.reconnect

    mock.on("POST", r"/v5/pins$", (503, {"message": "down"}))
    with pytest.raises(PublishError) as e:
        await do_publish(org, post, pin)
    assert e.value.retryable

    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)

    mock.on("POST", r"/v5/pins$", boom)
    with pytest.raises(PublishError) as e:
        await do_publish(org, post, pin)
    # A read timeout may mean the pin was created: never retry automatically.
    assert not e.value.retryable and "may or may not be live" in str(e.value)

    def refused(req):
        raise httpx.ConnectError("refused", request=req)

    mock.on("POST", r"/v5/pins$", refused)
    with pytest.raises(PublishError) as e:
        await do_publish(org, post, pin)
    assert e.value.retryable  # the request never reached Pinterest

    fb = await make_account(org, "facebook_page", "557", {"access_token": "dead"})
    fpost = await make_post(org, fb, "text")
    mock.on("POST", r"/557/feed$", (400, {"error": {"message": "Error validating access token", "type": "OAuthException",
                                                    "code": 190}}))
    with pytest.raises(PublishError) as e:
        await do_publish(org, fpost, fb)
    assert e.value.reconnect
    mock.on("POST", r"/557/feed$", (403, {"error": {"message": "Application request limit reached", "code": 4}}))
    with pytest.raises(PublishError) as e:
        await do_publish(org, fpost, fb)
    assert e.value.retryable and not e.value.reconnect


# ------------------------------------------------------------------ token refresh & background job
async def test_pinterest_token_refresh(hrp, mock):
    org = org_of(hrp)
    acc = await make_account(org, "pinterest", "pin-refresh", {"access_token": "old", "refresh_token": "pin-ref"},
                             {"board_id": "b1"}, expires_at=datetime.now(UTC) + timedelta(days=2))
    mock.on("POST", r"/v5/oauth/token$", (200, {"access_token": "fresh", "expires_in": 2592000, "scope": "pins:write"}))
    mock.on("GET", r"/v5/user_account$", (200, {"username": "hrpai"}))
    a = await do_refresh(org, acc)
    assert a.status == "connected"
    assert account_tokens(a)["access_token"] == "fresh"
    assert account_tokens(a)["refresh_token"] == "pin-ref"
    assert a.token_expires_at > datetime.now(UTC) + timedelta(days=29)
    req = mock.find("POST", r"/v5/oauth/token$")[0]
    assert form(req) == {"grant_type": "refresh_token", "refresh_token": "pin-ref"}
    assert req.headers["Authorization"].startswith("Basic ")
    assert mock.find("GET", r"/v5/user_account$")[0].headers["Authorization"] == "Bearer fresh"


async def test_google_access_token_refreshes_when_expired(hrp, mock):
    from app.services.social.google import google_access_token

    org = org_of(hrp)
    async with SessionLocal() as s:  # make the test's holder the only live one
        set_tenant(s, org)
        for h in (await s.scalars(select(SocialAccount).where(SocialAccount.platform == "google"))).all():
            h.status = "removed"
        await s.commit()
    await make_account(org, "google", "g-sub-exp", {"access_token": "stale", "refresh_token": "g-ref"},
                       expires_at=datetime.now(UTC) - timedelta(minutes=1))
    mock.on("POST", r"oauth2\.googleapis\.com/token$", (200, {"access_token": "g-fresh", "expires_in": 3599}))
    async with SessionLocal() as s:
        set_tenant(s, org)
        assert await google_access_token(s) == "g-fresh"
        await s.commit()
    f = form(mock.find("POST", r"/token$")[0])
    assert f["grant_type"] == "refresh_token" and f["refresh_token"] == "g-ref" and f["client_id"] == "g-id"


async def test_refresh_job_marks_expired_and_notifies_once(hrp, mock, monkeypatch):
    from app.modules import load_all
    from app.services import notify
    from app.services.jobs import run_inline

    load_all()
    sent = []

    async def fake_notify(org_id, event, title, body=None, **kw):
        sent.append((event, title, body))

    monkeypatch.setattr(notify, "notify_org", fake_notify)
    org = org_of(hrp)
    acc = await make_account(org, "mastodon", "m.social:dead", {"access_token": "revoked", "instance": "https://m.social"},
                             name="HRP Mastodon")
    mock.on("GET", r"verify_credentials$", (401, {"error": "The access token is invalid"}))
    r = await run_inline("account.refresh", org, {"account_id": str(acc.id)})
    assert r["expired"] == ["HRP Mastodon"] and r["notified"] == 1
    assert sent and sent[0][0] == "account_issue" and "HRP Mastodon" in sent[0][2]
    r = await run_inline("account.refresh", org, {"account_id": str(acc.id)})
    assert r["notified"] == 0 and len(sent) == 1
    async with SessionLocal() as s:
        set_tenant(s, org)
        row = await s.get(SocialAccount, acc.id)
        assert row.status == "expired" and "invalid" in row.status_message

    # Reconnected: the flag clears so a later failure notifies again.
    mock.on("GET", r"verify_credentials$", (200, {"id": "1", "username": "hrp"}))
    await run_inline("account.refresh", org, {"account_id": str(acc.id)})
    async with SessionLocal() as s:
        set_tenant(s, org)
        row = await s.get(SocialAccount, acc.id)
        assert row.status == "connected" and "_issue_notified" not in row.settings

    # Publishing to an expired account asks for a reconnect without calling the platform.
    async with SessionLocal() as s:
        set_tenant(s, org)
        (await s.get(SocialAccount, acc.id)).status = "expired"
        await s.commit()
    with pytest.raises(PublishError) as e:
        await do_publish(org, await make_post(org, acc, "text"), acc)
    assert e.value.reconnect


async def test_oauth_app_tester_returns_redirect_uris():
    from app.api.integrations import TESTERS
    from app.core.crypto import encrypt_json
    from app.modules import load_all

    load_all()
    cfg = ProviderConfig(provider="meta_app", kind="social_app", name="Meta",
                         secrets_enc=encrypt_json({"client_id": "1", "client_secret": "2"}))
    msg = await TESTERS["oauth"](cfg)
    assert "/social/oauth/meta/callback" in msg and "/social/oauth/threads/callback" in msg
    cfg.secrets_enc = encrypt_json({"client_id": "1"})
    with pytest.raises(ValueError):
        await TESTERS["oauth"](cfg)


async def test_patch_settings_and_metrics(client, hrp, mock):
    org = org_of(hrp)
    acc = await make_account(org, "linkedin_person", "urn:li:person:zz", {"access_token": "li"})
    r = await client.patch(f"/social/accounts/{acc.id}", headers=auth(hrp),
                           json={"name": "Prashant", "settings": {"privacy": "unlisted", "_issue_notified": "x"}})
    assert r.status_code == 200 and r.json()["name"] == "Prashant" and r.json()["settings"] == {"privacy": "unlisted"}
    assert (await client.patch(f"/social/accounts/{acc.id}", headers=auth(hrp),
                               json={"settings": {"privacy": "secret"}})).status_code == 400
    post = await make_post(org, acc, "text", external_id="urn:li:share:1")
    mock.on("GET", r"/rest/socialActions/", (200, {"likesSummary": {"totalLikes": 12},
                                                   "commentsSummary": {"aggregatedTotalComments": 3}}))
    async with SessionLocal() as s:
        set_tenant(s, org)
        m = await publishing.fetch_metrics(s, await s.get(Post, post.id), await s.get(SocialAccount, acc.id))
    assert m["likes"] == 12 and m["comments"] == 3 and m["impressions"] == 0 and "raw" in m
    mock.on("GET", r"/rest/socialActions/", (403, {"message": "Not enough permissions"}))
    async with SessionLocal() as s:
        set_tenant(s, org)
        assert await publishing.fetch_metrics(s, await s.get(Post, post.id), await s.get(SocialAccount, acc.id)) is None


async def test_google_locations_and_youtube_short(client, hrp, mock):
    org = org_of(hrp)
    async with SessionLocal() as s:
        set_tenant(s, org)
        for h in (await s.scalars(select(SocialAccount).where(SocialAccount.platform == "google"))).all():
            h.status = "removed"
        await s.commit()
    await make_account(org, "google", "g-sub-loc", {"access_token": "g-live", "refresh_token": "g-ref"},
                       expires_at=datetime.now(UTC) + timedelta(hours=1))
    mock.on("GET", r"mybusinessaccountmanagement\.googleapis\.com/v1/accounts$",
            (200, {"accounts": [{"name": "accounts/7", "accountName": "HRP AI"}]}))
    mock.on("GET", r"mybusinessbusinessinformation\.googleapis\.com/v1/accounts/7/locations$", (200, {"locations": [
        {"name": "locations/70", "title": "HRP AI Pune", "storefrontAddress": {"addressLines": ["Baner Rd"], "locality": "Pune"},
         "metadata": {"mapsUri": "https://maps.google.com/?cid=1"}}]}))
    r = await client.get("/social/google/locations", headers=auth(hrp))
    assert r.status_code == 200, r.text
    loc = r.json()["locations"][0]
    assert loc["name"] == "accounts/7/locations/70" and loc["connected"] is False and loc["address"] == "Baner Rd, Pune"
    assert query(mock.find("GET", r"/locations$")[0])["readMask"].startswith("name,title")
    r = await client.post("/social/google/locations", headers=auth(hrp), json=["accounts/7/locations/70"])
    assert r.status_code == 200, r.text
    assert r.json()[0]["platform"] == "gbp" and r.json()[0]["name"] == "HRP AI Pune"
    assert (await client.get("/social/google/locations", headers=auth(hrp))).json()["locations"][0]["connected"] is True

    yt = await make_account(org, "youtube", "UC123", None, {"privacy": "unlisted"})
    vid = await make_media(org, kind="video", mime="video/mp4", data=b"short-video")
    post = await make_post(org, yt, "video", [vid], title="Automate payroll in 30 seconds", hashtags=["HRTech"])
    mock.on("POST", r"upload/youtube/v3/videos$", (200, {}, {"Location": "https://www.googleapis.com/upload/session/1"}))
    mock.on("PUT", r"upload/session/1$", (200, {"id": "yt-vid-1"}))
    ext, url = await do_publish(org, post, yt)
    assert (ext, url) == ("yt-vid-1", "https://www.youtube.com/shorts/yt-vid-1")
    init = mock.find("POST", r"upload/youtube/v3/videos$")[0]
    q = query(init)
    assert q["uploadType"] == "resumable" and q["part"] == "snippet,status"
    meta = json.loads(init.content)
    assert meta["snippet"]["title"] == "Automate payroll in 30 seconds #Shorts"
    assert meta["status"]["privacyStatus"] == "unlisted" and init.headers["X-Upload-Content-Length"] == "11"
    assert mock.find("PUT", r"upload/session/1$")[0].content == b"short-video"
