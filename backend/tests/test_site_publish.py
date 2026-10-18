"""Publishing SEO articles to the company website. WordPress REST, the webhook receiver, IndexNow and the live
page are all mocked with httpx.MockTransport (app.services.social.net.client)."""

import base64
import io
import json
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

import app.modules.seo  # noqa: F401 - registers handlers
import app.modules.site_publish  # noqa: F401
from app.core import storage
from app.core.config import get_settings
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import Job, MediaAsset, SeoArticle, SitePublication, SiteRedirect
from app.services import site_publish
from app.services.jobs import Worker
from app.services.social import net
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"
SITE = "https://blog.example.com"
WP_USER, WP_PASS = "editor", "abcd efgh ijkl mnop"
HOOK = "https://hooks.example.com/hrp"
HOOK_SECRET = "s3cret-signing-key"
STATE: dict = {}

BODY = (
    "# HR onboarding guide\n\n"
    "A good **onboarding plan** makes the first week calm.\n\n"
    "## Steps that work\n\n- Prepare the desk\n- Share the plan\n\n"
    "| Step | Owner |\n|---|---|\n| Laptop | IT |\n\n"
    "![Checklist](https://cdn.example.com/checklist.png)\n\n"
    "Read the [HRMS page](https://hrpai.in/products/hrms).\n"
)


# ------------------------------------------------------------------ fake WordPress + webhook receiver
class FakeSite:
    def __init__(self, plugins=("yoast/v1",), registered_meta=True):
        self.ns = ["wp/v2", "oembed/1.0", *plugins]
        self.registered = registered_meta
        self.auth_ok = True
        self.posts: dict[int, dict] = {}
        self.next_id = 100
        self.tags = {"HR": 7}
        self.redirects: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.hooks: list[dict] = []
        self.hook_status = 200
        self.indexnow: list[dict] = []
        self.page_title_override = None

    def _json(self, req):
        return json.loads(req.content or b"{}")

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.url.host == "api.indexnow.org":
            self.indexnow.append(self._json(req))
            return httpx.Response(202)
        if req.url.host == "hooks.example.com":
            ts, sig = req.headers.get("x-hrp-timestamp"), req.headers.get("x-hrp-signature")
            if not site_publish.verify_signature(HOOK_SECRET, ts, req.content, sig):
                return httpx.Response(401, text="bad signature")
            body = self._json(req)
            self.hooks.append({"event": req.headers.get("x-hrp-event"), "body": body})
            if self.hook_status != 200:
                return httpx.Response(self.hook_status, text="<h1>Server error</h1>")
            slug = (body.get("article") or {}).get("slug")
            return httpx.Response(200, json={"id": "cms-1", "url": f"https://hrpai.in/blog/{slug}"} if slug else {"ok": True})
        if req.url.host == "hrpai.in":  # webhook-published live page
            slug = req.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, text=f'<html><head><title>HR onboarding guide: steps that work</title>'
                                            f'<link rel="canonical" href="https://hrpai.in/blog/{slug}"></head></html>')
        assert req.url.host == "blog.example.com", req.url
        path = req.url.path
        if not path.startswith("/wp-json"):
            return self.live_page(path)
        route = path[len("/wp-json"):] or "/"
        if route != "/":
            expected = "Basic " + base64.b64encode(f"{WP_USER}:{WP_PASS}".encode()).decode()
            if not self.auth_ok or req.headers.get("authorization") != expected:
                return httpx.Response(401, json={"code": "incorrect_password", "message": "The password is wrong.",
                                                 "data": {"status": 401}})
        m, p = req.method, route
        if m == "GET" and p == "/":
            return httpx.Response(200, json={"name": "Example Blog", "namespaces": self.ns})
        if m == "GET" and p == "/wp/v2/users/me":
            return httpx.Response(200, json={"id": 1, "name": "Editor", "capabilities": {"publish_posts": True}})
        if m == "GET" and p == "/wp/v2/categories":
            return httpx.Response(200, json=[{"id": 3, "name": "HR &amp; People", "slug": "hr", "count": 4}])
        if m == "GET" and p == "/wp/v2/users":
            return httpx.Response(200, json=[{"id": 1, "name": "Editor", "slug": "editor"}])
        if m == "GET" and p == "/wp/v2/tags":
            q = (req.url.params.get("search") or "").lower()
            return httpx.Response(200, json=[{"id": i, "name": n} for n, i in self.tags.items() if q in n.lower()])
        if m == "POST" and p == "/wp/v2/tags":
            name = self._json(req)["name"]
            self.tags[name] = 50 + len(self.tags)
            return httpx.Response(201, json={"id": self.tags[name], "name": name})
        if m == "POST" and p == "/wp/v2/media":
            assert "attachment; filename=" in req.headers["content-disposition"]
            return httpx.Response(201, json={"id": 55, "source_url": f"{SITE}/wp-content/uploads/cover.png"})
        if m == "POST" and p.startswith("/wp/v2/media/"):
            return httpx.Response(200, json={"id": 55})
        if m == "POST" and p == "/wp/v2/posts":
            self.next_id += 1
            return self.save(self.next_id, self._json(req), new=True)
        mm = re.match(r"^/wp/v2/posts/(\d+)$", p)
        if m == "POST" and mm:
            pid = int(mm.group(1))
            if pid not in self.posts:
                return httpx.Response(404, json={"code": "rest_post_invalid_id", "message": "Invalid post ID.",
                                                 "data": {"status": 404}})
            return self.save(pid, self._json(req))
        if m == "GET" and p == "/redirection/v1/group":
            return httpx.Response(200, json={"items": [{"id": 1, "name": "Redirections", "module_id": 1}], "total": 1})
        if m == "POST" and p == "/redirection/v1/redirect":
            b = self._json(req)
            self.redirects.append(b)
            return httpx.Response(200, json={"items": [{"id": len(self.redirects), "url": b["url"]}]})
        if m == "POST" and p == "/rankmath/v1/updateMeta":
            return httpx.Response(200, json={"slug": True})
        return httpx.Response(404, json={"code": "rest_no_route", "message": "No route was found.", "data": {"status": 404}})

    def save(self, pid: int, body: dict, new: bool = False) -> httpx.Response:
        post = self.posts.setdefault(pid, {"id": pid, "status": "draft", "slug": "", "meta": {}})
        for k, v in body.items():
            if k == "meta":
                if self.registered:
                    post["meta"].update(v)
            else:
                post[k] = v
        st = post.get("status")
        post["link"] = f"{SITE}/{post['slug']}/" if st in ("publish", "future") else f"{SITE}/?p={pid}"
        post["date_gmt"] = body.get("date_gmt") or post.get("date_gmt") or "2026-10-17T09:00:00"
        out = {k: v for k, v in post.items() if k not in ("content", "meta")}
        out["meta"] = dict(post["meta"]) if post["meta"] else []  # PHP sends [] for no meta
        return httpx.Response(201 if new else 200, json=out)

    def live_page(self, path: str) -> httpx.Response:
        slug = path.strip("/")
        post = next((p for p in self.posts.values() if p["slug"] == slug and p["status"] == "publish"), None)
        if post is None:
            return httpx.Response(404, text="Not found")
        title = self.page_title_override or post.get("meta", {}).get("_yoast_wpseo_title") or post["title"]
        return httpx.Response(200, text=f'<!doctype html><html><head><title>{title} - Example Blog</title>'
                                        f'<link rel="canonical" href="{SITE}/{slug}/"></head><body>ok</body></html>')

    def last(self, method: str, prefix: str) -> httpx.Request:
        return [r for r in self.requests if r.method == method and r.url.path.startswith(prefix)][-1]


_FAKE: list[FakeSite] = []


@pytest.fixture
def site(monkeypatch):
    """One fake site for the module (posts persist between tests); per-test switches are reset."""
    if not _FAKE:
        _FAKE.append(FakeSite())
    s = _FAKE[0]
    s.ns, s.registered, s.auth_ok, s.hook_status, s.page_title_override = (
        ["wp/v2", "oembed/1.0", "yoast/v1"], True, True, 200, None)
    monkeypatch.setattr(net, "client", lambda timeout=60.0: httpx.AsyncClient(transport=httpx.MockTransport(s.handler)))
    return s


@pytest.fixture(scope="module", autouse=True)
def temp_storage(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage"))
    yield
    s.storage_dir = old


# ------------------------------------------------------------------ helpers
async def _setup(client, admin) -> dict:
    if "org" in STATE:
        return STATE
    hrp = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(hrp),
                          json={"name": "Site Acme", "admin_email": "owner@site-acme.in", "admin_name": "Site Owner"})
    assert r.status_code == 201, r.text
    t = await login(client, "owner@site-acme.in", r.json()["temporary_password"])
    if t["user"]["must_change_password"]:
        r = await client.post("/auth/change-password", headers=auth(t),
                              json={"current_password": r.json()["temporary_password"], "new_password": ADMIN_PW})
        t = r.json()
    STATE.update(t=t, org=uuid.UUID(t["organization"]["id"]))
    r = await client.post("/integrations/providers", headers=auth(t), json={
        "provider": "wordpress", "name": "Company blog",
        "secrets": {"site_url": "blog.example.com/", "username": WP_USER, "app_password": WP_PASS}})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "website" and r.json()["secrets"]["app_password"].startswith("••")
    assert WP_PASS not in r.text
    STATE["wp"] = r.json()["id"]
    r = await client.post("/integrations/providers", headers=auth(t), json={
        "provider": "website_webhook", "name": "hrpai.in",
        "secrets": {"webhook_url": HOOK, "signing_secret": HOOK_SECRET, "article_url": "https://hrpai.in/blog/{slug}"}})
    assert r.status_code == 201, r.text
    STATE["hook"] = r.json()["id"]
    return STATE


async def make_article(org_id: uuid.UUID, slug: str, cover: bool = True) -> str:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        cover_id = None
        if cover:
            rel, sha = storage.save_bytes(org_id, "media", b"\x89PNG\r\n\x1a\nfakeimage", "png")
            asset = MediaAsset(organization_id=org_id, kind="image", mime="image/png", path=rel, size_bytes=17,
                               sha256=sha, source="upload", alt_text="People at a desk")
            s.add(asset)
            await s.flush()
            cover_id = asset.id
        a = SeoArticle(organization_id=org_id, title="HR onboarding guide", slug=slug, keyword="hr onboarding",
                       secondary_keywords=["onboarding checklist"], body_md=BODY, status="draft", outline=[],
                       meta_title="HR onboarding guide: steps that work",
                       meta_description="How to plan the first week for new hires, step by step.",
                       faq=[{"q": "What is onboarding?", "a": "Helping new people settle in."}], cover_media_id=cover_id)
        s.add(a)
        await s.commit()
        return str(a.id)


async def run_job(job_id: str) -> Job:
    async with platform_session() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        job.attempts = 1
        await s.commit()
        s.expunge(job)
    await Worker().run_job(job)
    async with platform_session() as s:
        return await s.get(Job, uuid.UUID(job_id))


async def publish(client, t, aid, provider_id, **opts) -> Job:
    r = await client.post(f"/seo/articles/{aid}/website/publish", headers=auth(t), json={"provider_id": provider_id, **opts})
    assert r.status_code == 200, r.text
    return await run_job(r.json()["job_id"])


async def state(client, t, aid, provider_id) -> dict:
    r = await client.get(f"/seo/articles/{aid}/website", headers=auth(t))
    assert r.status_code == 200, r.text
    return next(i for i in r.json()["items"] if i["connection"]["id"] == provider_id)


# ------------------------------------------------------------------ tests
async def test_connection_test_and_options(client, admin, site):
    st = await _setup(client, admin)
    t = st["t"]
    r = await client.post(f"/integrations/providers/{st['wp']}/test", headers=auth(t))
    assert r.json()["ok"], r.json()
    assert "Example Blog" in r.json()["message"] and "Yoast SEO" in r.json()["message"]
    r = await client.get(f"/seo/website/connections/{st['wp']}/options", headers=auth(t))
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["categories"][0] == {"id": 3, "name": "HR & People", "slug": "hr"}
    assert o["authors"][0]["name"] == "Editor" and o["plugins"]["yoast"] and not o["plugins"]["redirection"]
    # the webhook connection sends a signed ping
    r = await client.post(f"/integrations/providers/{st['hook']}/test", headers=auth(t))
    assert r.json()["ok"], r.json()
    assert site.hooks[-1]["event"] == "ping"
    conns = (await client.get("/seo/website/connections", headers=auth(t))).json()
    assert {c["target"] for c in conns} == {"wordpress", "webhook"}
    assert all("app_password" not in json.dumps(c) for c in conns)


async def test_wrong_password_gives_clear_message(client, admin, site):
    st = await _setup(client, admin)
    site.auth_ok = False
    aid = await make_article(st["org"], "bad-password-article", cover=False)
    job = await publish(client, st["t"], aid, st["wp"], status="publish")
    assert job.status == "failed"
    item = await state(client, st["t"], aid, st["wp"])
    pub = item["publication"]
    assert pub["status"] == "error" and "application password" in pub["error"]
    assert "Traceback" not in pub["error"]
    r = await client.post(f"/integrations/providers/{st['wp']}/test", headers=auth(st["t"]))
    assert not r.json()["ok"] and "application password" in r.json()["message"]


async def test_publish_wordpress_with_yoast(client, admin, site):
    st = await _setup(client, admin)
    t = st["t"]
    aid = await make_article(st["org"], "hr-onboarding-guide")
    item = await state(client, t, aid, st["wp"])
    assert item["publication"] is None
    job = await publish(client, t, aid, st["wp"], status="publish", categories=[3], tags=["HR", "New hires"],
                        author=1, check=True)
    assert job.status == "done", job.error
    assert "Published" in job.result["message"]

    req = site.last("POST", "/wp-json/wp/v2/posts")
    body = json.loads(req.content)
    assert body["status"] == "publish" and body["slug"] == "hr-onboarding-guide" and body["title"] == "HR onboarding guide"
    assert body["categories"] == [3] and body["tags"] == [7, site.tags["New hires"]] and body["author"] == 1
    assert body["featured_media"] == 55 and body["excerpt"].startswith("How to plan")
    html = body["content"]
    assert "<h1" not in html and '<h2 id="steps-that-work">' in html and "<table>" in html and "<li>Share the plan</li>" in html
    assert '<img src="https://cdn.example.com/checklist.png"' in html and 'href="https://hrpai.in/products/hrms"' in html
    # Yoast prints the Article schema itself; we only add the FAQ
    assert '"FAQPage"' in html and '"Article"' not in html
    assert body["meta"]["_yoast_wpseo_title"] == "HR onboarding guide: steps that work"
    assert body["meta"]["_yoast_wpseo_metadesc"].startswith("How to plan")
    assert body["meta"]["_yoast_wpseo_focuskw"] == "hr onboarding"
    assert "_yoast_wpseo_canonical" not in body["meta"]  # no explicit canonical: Yoast uses the permalink

    pub = (await state(client, t, aid, st["wp"]))["publication"]
    assert pub["status"] == "published" and not pub["update_available"]
    assert pub["remote_url"] == f"{SITE}/hr-onboarding-guide/"
    assert pub["warnings"] == [] and pub["seo_plugin"] == "yoast"
    assert pub["check"]["ok"] and pub["check"]["status_code"] == 200, pub["check"]
    assert pub["search_console_url"].startswith("https://search.google.com/search-console/inspect?resource_id=")
    # the article's canonical now points at the live page
    art = (await client.get(f"/seo/articles/{aid}", headers=auth(t))).json()
    assert art["canonical_url"] == f"{SITE}/hr-onboarding-guide/"

    # edit → update available → update the same post (no second post, image not re-uploaded)
    r = await client.patch(f"/seo/articles/{aid}", headers=auth(t), json={"meta_description": "A calmer first week for "
                                                                          "every new hire, planned step by step."})
    assert r.status_code == 200
    assert (await state(client, t, aid, st["wp"]))["publication"]["update_available"]
    media_uploads = len([x for x in site.requests if x.method == "POST" and x.url.path == "/wp-json/wp/v2/media"])
    job = await publish(client, t, aid, st["wp"], status="publish")
    assert job.status == "done", job.error
    assert len(site.posts) == 1
    upd = site.last("POST", "/wp-json/wp/v2/posts/")
    assert upd.url.path == f"/wp-json/wp/v2/posts/{pub['remote_id']}"
    assert json.loads(upd.content)["meta"]["_yoast_wpseo_metadesc"].startswith("A calmer first week")
    assert len([x for x in site.requests if x.method == "POST" and x.url.path == "/wp-json/wp/v2/media"]) == media_uploads
    assert not (await state(client, t, aid, st["wp"]))["publication"]["update_available"]
    STATE["published"] = aid


async def test_slug_change_records_and_pushes_redirect(client, admin, site):
    st = await _setup(client, admin)
    t = st["t"]
    aid = STATE["published"]
    r = await client.patch(f"/seo/articles/{aid}", headers=auth(t), json={"slug": "employee-onboarding-guide"})
    assert r.status_code == 200, r.text
    reds = (await client.get(f"/seo/redirects?article_id={aid}", headers=auth(t))).json()
    assert len(reds) == 1 and reds[0]["from_path"] == "/hr-onboarding-guide/"
    assert reds[0]["to_path"] == "/employee-onboarding-guide/" and reds[0]["status"] == "pending"

    # exports for sites we can't push to
    for fmt, expect in (("htaccess", "Redirect 301 /hr-onboarding-guide/ /employee-onboarding-guide/"),
                        ("nginx", "location = /hr-onboarding-guide/ { return 301 /employee-onboarding-guide/; }"),
                        ("netlify", "/hr-onboarding-guide/  /employee-onboarding-guide/  301"),
                        ("csv", "/hr-onboarding-guide/,/employee-onboarding-guide/,301")):
        out = (await client.get(f"/seo/redirects?format={fmt}", headers=auth(t))).json()
        assert expect in out["content"], (fmt, out)
    vercel = json.loads((await client.get("/seo/redirects?format=vercel", headers=auth(t))).json()["content"])
    assert vercel["redirects"][0] == {"source": "/hr-onboarding-guide/", "destination": "/employee-onboarding-guide/",
                                      "permanent": True}

    # Redirection plugin present → pushed with the update
    site.ns = [*site.ns, "redirection/v1"]
    job = await publish(client, t, aid, st["wp"], status="publish")
    assert job.status == "done", job.error
    assert site.redirects == [{"url": "/hr-onboarding-guide/", "action_data": {"url": "/employee-onboarding-guide/"},
                               "action_type": "url", "action_code": 301, "match_type": "url", "group_id": 1,
                               "regex": False}]
    reds = (await client.get(f"/seo/redirects?article_id={aid}", headers=auth(t))).json()
    assert reds[0]["status"] == "pushed"
    pub = (await state(client, t, aid, st["wp"]))["publication"]
    assert pub["remote_url"] == f"{SITE}/employee-onboarding-guide/" and pub["published_slug"] == "employee-onboarding-guide"

    # changing it again chains A→C instead of A→B→C
    r = await client.patch(f"/seo/articles/{aid}", headers=auth(t), json={"slug": "onboarding-guide"})
    reds = {x["from_path"]: x for x in (await client.get(f"/seo/redirects?article_id={aid}", headers=auth(t))).json()}
    assert reds["/hr-onboarding-guide/"]["to_path"] == "/onboarding-guide/"
    assert reds["/employee-onboarding-guide/"]["to_path"] == "/onboarding-guide/"


async def test_unpublish_moves_to_draft(client, admin, site):
    st = await _setup(client, admin)
    t = st["t"]
    aid = STATE["published"]
    r = await client.post(f"/seo/articles/{aid}/website/unpublish", headers=auth(t), json={"provider_id": st["wp"]})
    assert r.status_code == 200, r.text
    job = await run_job(r.json()["job_id"])
    assert job.status == "done", job.error
    req = site.last("POST", "/wp-json/wp/v2/posts/")
    assert json.loads(req.content) == {"status": "draft"}
    pub = (await state(client, t, aid, st["wp"]))["publication"]
    assert pub["status"] == "draft" and not pub["can_unpublish"]
    r = await client.post(f"/seo/articles/{aid}/website/unpublish", headers=auth(t), json={"provider_id": st["wp"]})
    assert r.status_code == 400


async def test_unregistered_meta_warns_with_snippet(client, admin, site):
    st = await _setup(client, admin)
    site.registered = False
    aid = await make_article(st["org"], "meta-not-registered", cover=False)
    job = await publish(client, st["t"], aid, st["wp"], status="draft")
    assert job.status == "done", job.error
    pub = (await state(client, st["t"], aid, st["wp"]))["publication"]
    assert pub["status"] == "draft"
    w = next(x for x in pub["warnings"] if x["code"] == "seo_meta_not_registered")
    assert "register_post_meta" in w["snippet"] and "Code Snippets" in w["help"]
    assert pub["check"] is None  # drafts aren't public, so no live check


async def test_no_seo_plugin_embeds_full_schema_and_schedules(client, admin, site):
    st = await _setup(client, admin)
    site.ns = ["wp/v2"]
    aid = await make_article(st["org"], "scheduled-article", cover=False)
    when = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    job = await publish(client, st["t"], aid, st["wp"], status="future", publish_at=when.isoformat())
    assert job.status == "done", job.error
    posts = [json.loads(r.content) for r in site.requests if r.method == "POST" and r.url.path.startswith("/wp-json/wp/v2/posts")]
    first = posts[-2] if len(posts) >= 2 and "title" not in posts[-1] else posts[-1]
    assert first["status"] == "future" and first["date_gmt"] == when.astimezone(UTC).replace(tzinfo=None).isoformat()
    assert "meta" not in first
    final_content = posts[-1]["content"]
    assert '"Article"' in final_content and f"{SITE}/scheduled-article/" in final_content  # schema fixed to the permalink
    pub = (await state(client, st["t"], aid, st["wp"]))["publication"]
    assert pub["status"] == "scheduled" and pub["scheduled_at"]
    assert any(w["code"] == "no_seo_plugin" for w in pub["warnings"])
    # past dates are refused
    r = await client.post(f"/seo/articles/{aid}/website/publish", headers=auth(st["t"]),
                          json={"provider_id": st["wp"], "status": "future",
                                "publish_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()})
    assert r.status_code == 400


async def test_rank_math_uses_its_meta_endpoint(client, admin, site):
    st = await _setup(client, admin)
    site.ns = ["wp/v2", "rankmath/v1"]
    site.registered = False
    aid = await make_article(st["org"], "rank-math-article", cover=False)
    job = await publish(client, st["t"], aid, st["wp"], status="publish")
    assert job.status == "done", job.error
    req = site.last("POST", "/wp-json/rankmath/v1/updateMeta")
    b = json.loads(req.content)
    assert b["objectType"] == "post" and b["meta"]["rank_math_title"] == "HR onboarding guide: steps that work"
    pub = (await state(client, st["t"], aid, st["wp"]))["publication"]
    assert pub["seo_plugin"] == "rankmath" and not any(w["code"] == "seo_meta_not_registered" for w in pub["warnings"])


async def test_webhook_publish_is_signed_and_complete(client, admin, site, monkeypatch):
    st = await _setup(client, admin)
    t = st["t"]
    import app.services.media as media_mod

    async def ensure_public_url(session, asset, variant=None):
        return f"https://cdn.example.com/{asset.id}.png"

    monkeypatch.setattr(media_mod, "ensure_public_url", ensure_public_url)
    aid = await make_article(st["org"], "webhook-article")
    job = await publish(client, t, aid, st["hook"], status="publish", tags=["HR"])
    assert job.status == "done", job.error
    hook = site.hooks[-1]
    assert hook["event"] == "article.published"
    a = hook["body"]["article"]
    assert a["slug"] == "webhook-article" and a["url"] == "https://hrpai.in/blog/webhook-article"
    assert "<h1" not in a["html"] and "<table>" in a["html"] and a["markdown"].startswith("# HR onboarding guide")
    assert a["meta"]["title"] == "HR onboarding guide: steps that work" and a["meta"]["canonical"] == a["url"]
    assert a["image"]["url"].startswith("https://cdn.example.com/") and a["image"]["alt"] == "People at a desk"
    assert a["schema"]["@graph"][0]["url"] == a["url"] and a["tags"] == ["HR"]
    assert '<link rel="canonical" href="https://hrpai.in/blog/webhook-article">' in a["head_html"]
    assert 'property="og:image"' in a["head_html"] and "application/ld+json" in a["head_html"]
    pub = (await state(client, t, aid, st["hook"]))["publication"]
    assert pub["status"] == "published" and pub["remote_id"] == "cms-1" and pub["check"]["ok"], pub

    # slug change → redirect goes out in the next payload
    await client.patch(f"/seo/articles/{aid}", headers=auth(t), json={"slug": "webhook-article-2"})
    assert (await state(client, t, aid, st["hook"]))["publication"]["update_available"]
    job = await publish(client, t, aid, st["hook"], status="publish")
    assert job.status == "done", job.error
    hook = site.hooks[-1]
    assert hook["event"] == "article.updated"
    assert hook["body"]["article"]["previous_slug"] == "webhook-article"
    assert hook["body"]["redirects"] == [{"from": "/blog/webhook-article", "to": "/blog/webhook-article-2", "code": 301}]
    reds = (await client.get(f"/seo/redirects?article_id={aid}", headers=auth(t))).json()
    assert reds[0]["status"] == "sent"

    # receiver errors are explained
    site.hook_status = 500
    await client.patch(f"/seo/articles/{aid}", headers=auth(t), json={"title": "HR onboarding guide (2026)"})
    job = await publish(client, t, aid, st["hook"], status="publish")
    assert job.status == "failed"
    pub = (await state(client, t, aid, st["hook"]))["publication"]
    assert pub["status"] == "published" and "error 500" in pub["error"] and "<h1>" not in pub["error"]
    site.hook_status = 200

    r = await client.post(f"/seo/articles/{aid}/website/unpublish", headers=auth(t), json={"provider_id": st["hook"]})
    job = await run_job(r.json()["job_id"])
    assert job.status == "done" and site.hooks[-1]["event"] == "article.unpublished"
    assert (await state(client, t, aid, st["hook"]))["publication"]["status"] == "unpublished"


async def test_bad_webhook_secret_is_rejected(client, admin, site):
    st = await _setup(client, admin)
    t = st["t"]
    r = await client.post("/integrations/providers", headers=auth(t), json={
        "provider": "website_webhook", "name": "wrong secret",
        "secrets": {"webhook_url": HOOK, "signing_secret": "not-the-secret"}})
    r = await client.post(f"/integrations/providers/{r.json()['id']}/test", headers=auth(t))
    assert not r.json()["ok"] and "signing secret" in r.json()["message"]


async def test_indexnow_and_live_check(client, admin, site):
    st = await _setup(client, admin)
    t = st["t"]
    site.ns = ["wp/v2", "yoast/v1"]
    site.registered = True
    r = await client.post(f"/seo/website/connections/{st['wp']}/indexnow-key", headers=auth(t))
    assert r.status_code == 200
    k = r.json()
    assert re.fullmatch(r"[0-9a-f]{32}", k["key"]) and k["file_url"] == f"{SITE}/{k['key']}.txt"
    again = (await client.post(f"/seo/website/connections/{st['wp']}/indexnow-key", headers=auth(t))).json()
    assert again["key"] == k["key"]

    aid = await make_article(st["org"], "indexnow-article", cover=False)
    job = await publish(client, t, aid, st["wp"], status="publish", indexnow=True)
    assert job.status == "done", job.error
    assert site.indexnow[-1] == {"host": "blog.example.com", "key": k["key"],
                                 "keyLocation": f"{SITE}/{k['key']}.txt", "urlList": [f"{SITE}/indexnow-article/"]}
    pub = (await state(client, t, aid, st["wp"]))["publication"]
    assert pub["indexnow"]["ok"]

    site.page_title_override = "Something else"
    r = await client.post(f"/seo/articles/{aid}/website/check", headers=auth(t), json={"provider_id": st["wp"]})
    c = r.json()
    assert c["status_code"] == 200 and c["title_ok"] is False and c["canonical_ok"] is True and not c["ok"]
    assert "not the meta title" in c["message"]
    r = await client.post(f"/seo/articles/{aid}/website/indexnow", headers=auth(t), json={"provider_id": st["wp"]})
    assert r.json()["ok"]


async def test_download_for_website_bundle(client, admin, site):
    st = await _setup(client, admin)
    aid = await make_article(st["org"], "bundle-article")
    r = await client.get(f"/seo/articles/{aid}/bundle?as_json=true", headers=auth(st["t"]))
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(r.json()["content_b64"])))
    names = set(z.namelist())
    assert {"bundle-article/index.html", "bundle-article/article.mdx", "bundle-article/meta.json",
            "bundle-article/schema.json", "bundle-article/README.txt", "bundle-article/cover.png"} <= names
    page = z.read("bundle-article/index.html").decode()
    for tag in ("<title>HR onboarding guide: steps that work</title>", 'rel="canonical"', 'property="og:url"',
                'property="og:image"', 'name="twitter:card" content="summary_large_image"', "application/ld+json",
                'property="og:site_name" content="Site Acme"'):
        assert tag in page, tag
    r = await client.get(f"/seo/articles/{aid}/bundle", headers=auth(st["t"]))
    assert r.headers["content-type"] == "application/zip"


async def test_permissions(client, admin, site):
    st = await _setup(client, admin)
    t = st["t"]
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(t))).json()}
    r = await client.post("/users", headers=auth(t),
                          json={"email": "reviewer@site-acme.in", "full_name": "Rev", "role_ids": [roles["Reviewer"]]})
    assert r.status_code == 201, r.text
    v = await login(client, "reviewer@site-acme.in", r.json()["temporary_password"])
    aid = STATE["published"]
    assert (await client.get(f"/seo/articles/{aid}/website", headers=auth(v))).status_code == 200
    r = await client.post(f"/seo/articles/{aid}/website/publish", headers=auth(v), json={"provider_id": st["wp"]})
    assert r.status_code == 403 and "seo.publish" in r.json()["detail"]
    assert (await client.get(f"/seo/articles/{aid}/bundle", headers=auth(v))).status_code == 403
    me = (await client.get("/auth/me", headers=auth(t))).json()
    assert "seo.publish" in me["permissions"]


async def test_tenant_isolation(client, admin, site):
    st = await _setup(client, admin)
    hrp = await login(client, "admin@hrpai.in", ADMIN_PW)
    aid = STATE["published"]
    assert (await client.get(f"/seo/articles/{aid}/website", headers=auth(hrp))).status_code == 404
    assert (await client.get("/seo/redirects", headers=auth(hrp))).json() == []
    async with SessionLocal() as s:
        set_tenant(s, uuid.UUID(hrp["organization"]["id"]))
        assert (await s.scalars(select(SitePublication))).all() == []
        assert (await s.scalars(select(SiteRedirect))).all() == []
