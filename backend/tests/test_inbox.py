"""Unified inbox and social listening: collectors and reply paths per platform (mocked HTTP), dedupe and
threading, permission errors, sentiment fallback, AI suggestions, notifications, API permissions and tenant
isolation. No real network: app.services.social.net.client is replaced with an httpx.MockTransport."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant
from app.models import Post, SocialAccount
from app.models.inbox import InboxItem, InboxSettings
from app.services.inbox import listening, sentiment
from app.services.inbox.sync import get_settings, notify_new, sync_accounts
from app.services.llm import router as llm_router
from app.services.social import common, net
from app.services.social.common import save_tokens
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"
NOW = datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat()


# ------------------------------------------------------------------ HTTP mock
class Mock:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, object]] = []
        self.calls: list[httpx.Request] = []

    def on(self, method: str, pattern: str, reply):
        self.routes.insert(0, (method, re.compile(pattern), reply))
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
                return httpx.Response(status, json=body, headers=rest[0] if rest else {})
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


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    """No AI provider by default: sentiment falls back to the heuristic."""
    async def none(*a, **kw):
        raise llm_router.NoProviderError("No AI text provider is set up.")

    monkeypatch.setattr(llm_router, "complete_json", none)


def form(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def query(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(req.url)).query).items()}


# ------------------------------------------------------------------ fixtures
async def _new_org(client, admin_tokens, name: str, email: str) -> dict:
    r = await client.post("/platform/organizations", headers=auth(admin_tokens),
                          json={"name": name, "admin_email": email, "admin_name": f"{name} Owner"})
    assert r.status_code == 201, r.text
    return await login(client, email, r.json()["temporary_password"])


@pytest.fixture(scope="module")
async def hrp(client, admin):
    return await login(client, "admin@hrpai.in", ADMIN_PW)


@pytest.fixture(scope="module")
async def org1(client, hrp):
    return await _new_org(client, hrp, "Inbox One", "owner@inbox-one.in")


@pytest.fixture(scope="module")
async def org2(client, hrp):
    return await _new_org(client, hrp, "Inbox Two", "owner@inbox-two.in")


def org_of(t: dict) -> uuid.UUID:
    return uuid.UUID(t["organization"]["id"])


async def make_account(org_id, platform, external_id, tokens=None, *, handle="hrpai", name=None, status="connected",
                       settings=None, expires_at=None) -> SocialAccount:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        a = SocialAccount(organization_id=org_id, platform=platform, external_id=external_id, status=status,
                          name=name or f"{platform} {external_id}", handle=handle, settings=settings or {})
        save_tokens(a, tokens or {}, expires_at)
        s.add(a)
        await s.commit()
        return a


async def make_post(org_id, account, external_id, text="Our new HRMS release is live", url=None) -> Post:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        p = Post(organization_id=org_id, social_account_id=account.id, platform=account.platform, post_type="text",
                 status="published", text=text, external_id=external_id, external_url=url, published_at=NOW)
        s.add(p)
        await s.commit()
        return p


async def run_sync(org_id, account_id=None) -> dict:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        r = await sync_accounts(s, org_id, account_id)
        await s.commit()
        return r


async def items(org_id, **filters) -> list[InboxItem]:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        q = select(InboxItem)
        for k, v in filters.items():
            q = q.where(getattr(InboxItem, k) == v)
        return list((await s.scalars(q.order_by(InboxItem.created_at))).all())


async def account_state(org_id, account_id) -> dict:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        st = await s.scalar(select(InboxSettings))
        return ((st.state or {}).get("accounts") or {}).get(str(account_id)) or {}


def perms(*granted):
    return (200, {"data": [{"permission": p, "status": "granted"} for p in granted]})


FB_READ = ("pages_show_list", "pages_read_engagement", "pages_read_user_content")


def fb_posts(count: int):
    return (200, {"data": [{"id": "page1_111", "message": "Our new HRMS release", "created_time": iso(NOW),
                            "permalink_url": "https://www.facebook.com/page1/posts/111",
                            "comments": {"summary": {"total_count": count}}}]})


FB_COMMENTS = (200, {"data": [
    {"id": "111_r2", "from": {"id": "page1", "name": "HRP AI"}, "message": "Sent you a DM!", "parent": {"id": "111_c1"},
     "created_time": iso(NOW - timedelta(minutes=1))},
    {"id": "111_r1", "from": {"id": "u2", "name": "Ravi"}, "message": "Following", "parent": {"id": "111_c1"},
     "created_time": iso(NOW - timedelta(minutes=2))},
    {"id": "111_c1", "from": {"id": "u1", "name": "Asha"}, "message": "How much does the HRMS cost?",
     "created_time": iso(NOW - timedelta(minutes=5)), "permalink_url": "https://www.facebook.com/111_c1"},
]})


# ------------------------------------------------------------------ scopes
async def test_new_scopes_are_requested():
    from app.services.social import google, meta, threads

    assert "pages_manage_engagement" in meta.SCOPES
    assert "instagram_manage_comments" in meta.IG_SCOPES
    assert {"threads_read_replies", "threads_manage_replies"} <= set(threads.SCOPES)
    assert "https://www.googleapis.com/auth/youtube.force-ssl" in google.SCOPES


# ------------------------------------------------------------------ Facebook
async def test_facebook_sync_threads_dedupes_and_skips_unchanged_posts(org1, mock):
    org = org_of(org1)
    acc = await make_account(org, "facebook_page", "page1", {"access_token": "page-tok", "user_token": "user-tok"},
                             name="HRP AI Page")
    post = await make_post(org, acc, "page1_111", url="https://www.facebook.com/page1/posts/111")
    mock.on("GET", r"/me/permissions$", perms(*FB_READ))
    mock.on("GET", r"/page1/published_posts$", fb_posts(3))
    mock.on("GET", r"/page1_111/comments$", FB_COMMENTS)

    r = await run_sync(org, acc.id)
    assert len(r["created"]) == 3 and r["notify"] == []  # the first sync back-fills quietly
    q = query(mock.find("GET", r"/page1_111/comments$")[0])
    assert q["filter"] == "stream" and q["access_token"] == "page-tok"
    rows = {i.external_id: i for i in await items(org, social_account_id=acc.id)}
    c1, r1, r2 = rows["111_c1"], rows["111_r1"], rows["111_r2"]
    assert c1.kind == "comment" and r1.kind == "reply" and r1.parent_id == c1.id and r1.thread_key == "111_c1"
    assert r2.is_own and r2.status == "read" and c1.status == "replied"  # our Page already answered
    assert c1.post_id == post.id and c1.post_snippet == "Our new HRMS release"
    assert c1.sentiment == "question" and c1.sentiment_source == "heuristic"
    st = await account_state(org, acc.id)
    assert st["ok"] and st["counts"] == {"page1_111": 3} and "pages_read_engagement" in st["granted"]

    # Same comment count: the comments aren't fetched again.
    await run_sync(org, acc.id)
    assert len(mock.find("GET", r"/page1_111/comments$")) == 1
    # Count changed: fetched again, de-duplicated, one new comment notified.
    mock.on("GET", r"/page1/published_posts$", fb_posts(4))
    mock.on("GET", r"/page1_111/comments$", (200, {"data": FB_COMMENTS[1]["data"] + [
        {"id": "111_c3", "from": {"id": "u3", "name": "Meera"}, "message": "This is a scam, my payroll is broken!",
         "created_time": iso(NOW)}]}))
    r = await run_sync(org, acc.id)
    assert [i.external_id for i in r["created"]] == ["111_c3"] and len(r["notify"]) == 1
    c3 = r["created"][0]
    assert c3.sentiment == "negative" and c3.priority
    assert len(await items(org, social_account_id=acc.id)) == 4


async def test_facebook_reply_permission_then_success(client, org1, mock):
    org = org_of(org1)
    acc = await make_account(org, "facebook_page", "page2", {"access_token": "p2", "user_token": "u2"})
    mock.on("GET", r"/me/permissions$", perms(*FB_READ))
    mock.on("GET", r"/page2/published_posts$", (200, {"data": [{"id": "page2_1", "message": "Hi",
                                                                 "comments": {"summary": {"total_count": 2}}}]}))
    mock.on("GET", r"/page2_1/comments$", (200, {"data": [
        {"id": "p2c1", "from": {"id": "x", "name": "Kiran"}, "message": "Great update!", "created_time": iso(NOW)},
        {"id": "p2r1", "from": {"id": "y", "name": "Dev"}, "message": "Agreed", "parent": {"id": "p2c1"},
         "created_time": iso(NOW)}]}))
    await run_sync(org, acc.id)
    c1 = (await items(org, external_id="p2c1"))[0]
    r1 = (await items(org, external_id="p2r1"))[0]

    # pages_manage_engagement isn't granted: refused before calling Facebook, with the fix spelled out.
    r = await client.post(f"/inbox/items/{c1.id}/reply", headers=auth(org1), json={"text": "Thank you!"})
    assert r.status_code == 409 and "pages_manage_engagement" in r.json()["detail"]
    assert "reconnect" in r.json()["detail"].lower() and not mock.find("POST", r"/comments$")
    d = (await client.get(f"/inbox/items/{c1.id}", headers=auth(org1))).json()
    assert d["item"]["reply_error"] and d["permission_issue"]["permission"] == "pages_manage_engagement"
    s = (await client.get("/inbox/status", headers=auth(org1))).json()
    row = next(a for a in s["accounts"] if a["id"] == str(acc.id))
    assert row["missing_permission"] == "pages_manage_engagement" and "Manage everything on your Page" in row["permission_help"]

    # Permission granted after reconnecting: the reply goes to the top-level comment, even when answering a reply.
    mock.on("GET", r"/me/permissions$", perms(*FB_READ, "pages_manage_engagement"))
    await run_sync(org, acc.id)
    mock.on("POST", r"/p2c1/comments$", (200, {"id": "p2c1_ours"}))
    r = await client.post(f"/inbox/items/{r1.id}/reply", headers=auth(org1), json={"text": "Thanks Dev!"})
    assert r.status_code == 200, r.text
    f = form(mock.find("POST", r"/p2c1/comments$")[0])
    assert f["message"] == "Thanks Dev!" and f["access_token"] == "p2"
    d = r.json()
    assert d["item"]["status"] == "replied" and d["item"]["reply_text"] == "Thanks Dev!" and d["item"]["reply_error"] is None
    assert any(t["is_own"] and t["text"] == "Thanks Dev!" for t in d["thread"]) and d["permission_issue"] is None
    # The next sync sees our reply and doesn't duplicate it.
    mock.on("GET", r"/page2/published_posts$", (200, {"data": [{"id": "page2_1", "message": "Hi",
                                                                 "comments": {"summary": {"total_count": 3}}}]}))
    mock.on("GET", r"/page2_1/comments$", (200, {"data": [
        {"id": "p2c1_ours", "from": {"id": "page2", "name": "HRP"}, "message": "Thanks Dev!", "parent": {"id": "p2c1"},
         "created_time": iso(NOW)}]}))
    await run_sync(org, acc.id)
    assert len(await items(org, external_id="p2c1_ours")) == 1

    # A Graph permission error (code 200) when replying is also reported as a permission problem.
    mock.on("POST", r"/p2c1/comments$", (403, {"error": {"code": 200, "message": "(#200) Permissions error"}}))
    r = await client.post(f"/inbox/items/{c1.id}/reply", headers=auth(org1), json={"text": "Hello"})
    assert r.status_code == 409 and "pages_manage_engagement" in r.json()["detail"]

    # Like is supported on Facebook.
    mock.on("POST", r"/p2c1/likes$", (200, {"success": True}))
    r = await client.post(f"/inbox/items/{c1.id}/like", headers=auth(org1))
    assert r.status_code == 200 and r.json()["liked_at"]


async def test_sync_permission_error_does_not_stop_other_accounts(org1, mock):
    org = org_of(org1)
    fb = await make_account(org, "facebook_page", "page3", {"access_token": "p3"})
    md = await make_account(org, "mastodon", "m.example:7", {"access_token": "mtok", "instance": "https://m.example"},
                            handle="@hrp@m.example")
    mock.on("GET", r"/page3/published_posts$", (400, {"error": {"code": 10, "message": "(#10) Requires pages_read_engagement"}}))
    mock.on("GET", r"m\.example/api/v1/notifications$", (200, [
        {"id": "n1", "type": "mention", "status": {"id": "901", "content": "<p>@hrp is HRP AI on Mastodon?</p>",
                                                    "created_at": iso(NOW), "url": "https://m.example/@a/901",
                                                    "account": {"id": "55", "acct": "ann@else.social", "display_name": "Ann"}}}]))
    r = await run_sync(org)
    assert (await account_state(org, fb.id))["missing_permission"] == "pages_read_engagement"
    assert (await account_state(org, md.id))["ok"]
    assert any(i.external_id == "901" for i in r["created"])


# ------------------------------------------------------------------ Instagram
async def test_instagram_comments_replies_mentions_and_reply_target(client, org1, mock):
    org = org_of(org1)
    acc = await make_account(org, "instagram", "ig1", {"access_token": "ig-tok", "user_token": "u"}, handle="hrpai")
    mock.on("GET", r"/me/permissions$", perms("instagram_basic", "instagram_manage_comments"))
    mock.on("GET", r"/ig1/media$", (200, {"data": [
        {"id": "m1", "caption": "Payroll in one click", "permalink": "https://instagram.com/p/m1", "timestamp": iso(NOW),
         "comments_count": 3},
        {"id": "m0", "caption": "Old", "timestamp": iso(NOW - timedelta(days=60)), "comments_count": 9}]}))
    mock.on("GET", r"/m1/comments$", (200, {"data": [
        {"id": "igc1", "text": "Love this ❤", "timestamp": iso(NOW), "username": "priya",
         "replies": {"data": [{"id": "igr1", "text": "@priya thanks!", "timestamp": iso(NOW), "username": "hrpai"},
                              {"id": "igr2", "text": "Me too", "timestamp": iso(NOW), "username": "sam"}]}}]}))
    mock.on("GET", r"/ig1/tags$", (200, {"data": [
        {"id": "tag1", "caption": "Loving @hrpai HRMS", "permalink": "https://instagram.com/p/tag1",
         "timestamp": iso(NOW), "username": "acme_hr", "like_count": 4, "comments_count": 1}]}))
    await run_sync(org, acc.id)
    assert not mock.find("GET", r"/m0/comments$")  # older than 30 days
    rows = {i.external_id: i for i in await items(org, social_account_id=acc.id)}
    assert rows["igc1"].status == "replied" and rows["igr1"].is_own and rows["igr2"].parent_id == rows["igc1"].id
    assert rows["igc1"].sentiment == "positive"
    assert rows["tag1"].kind == "mention" and rows["tag1"].engagement == 5

    mock.on("POST", r"/igc1/replies$", (200, {"id": "igr3"}))
    r = await client.post(f"/inbox/items/{rows['igr2'].id}/reply", headers=auth(org1), json={"text": "@sam welcome!"})
    assert r.status_code == 200, r.text
    assert form(mock.find("POST", r"/igc1/replies$")[0])["message"] == "@sam welcome!"
    mock.on("POST", r"/ig1/mentions$", (200, {"id": "igm1"}))
    r = await client.post(f"/inbox/items/{rows['tag1'].id}/reply", headers=auth(org1), json={"text": "Thank you!"})
    assert r.status_code == 200 and form(mock.find("POST", r"/ig1/mentions$")[0])["media_id"] == "tag1"
    # Instagram has no like endpoint for comments.
    assert (await client.post(f"/inbox/items/{rows['igc1'].id}/like", headers=auth(org1))).status_code == 400


async def test_instagram_tags_without_permission_is_not_fatal(org1, mock):
    org = org_of(org1)
    acc = await make_account(org, "instagram", "ig2", {"access_token": "t"})
    mock.on("GET", r"/ig2/media$", (200, {"data": []}))
    mock.on("GET", r"/ig2/tags$", (400, {"error": {"code": 10, "message": "(#10) Application does not have permission"}}))
    await run_sync(org, acc.id)
    st = await account_state(org, acc.id)
    assert st["ok"] and "instagram_manage_comments" in st["notes"][0]


# ------------------------------------------------------------------ Threads
async def test_threads_conversation_threading_and_reply(client, org1, mock):
    org = org_of(org1)
    acc = await make_account(org, "threads", "th1", {"access_token": "th-tok"}, handle="hrpai")
    mock.on("GET", r"/th1/threads$", (200, {"data": [{"id": "t100", "text": "AI for HR teams", "timestamp": iso(NOW),
                                                       "permalink": "https://threads.net/t100"}]}))
    mock.on("GET", r"/t100/conversation$", (200, {"data": [
        {"id": "t1", "text": "When is the demo?", "username": "neha", "timestamp": iso(NOW), "replied_to": {"id": "t100"}},
        {"id": "t2", "text": "Tomorrow 11am", "username": "hrpai", "timestamp": iso(NOW), "replied_to": {"id": "t1"}},
        {"id": "t3", "text": "Can I join?", "username": "om", "timestamp": iso(NOW), "replied_to": {"id": "t2"}},
        {"id": "t4", "text": "spam", "username": "bot", "timestamp": iso(NOW), "replied_to": {"id": "t100"},
         "hide_status": "HIDDEN"}]}))
    await run_sync(org, acc.id)
    rows = {i.external_id: i for i in await items(org, social_account_id=acc.id)}
    assert set(rows) == {"t1", "t2", "t3"}
    assert rows["t1"].kind == "comment" and rows["t3"].kind == "reply" and rows["t3"].thread_key == "t1"
    assert rows["t1"].status == "replied" and rows["t2"].is_own

    mock.on("POST", r"/th1/threads$", (200, {"id": "cont9"}))
    mock.on("GET", r"/cont9$", (200, {"status": "FINISHED"}))
    mock.on("POST", r"/th1/threads_publish$", (200, {"id": "t9"}))
    mock.on("GET", r"/t9$", (200, {"permalink": "https://threads.net/t9"}))
    r = await client.post(f"/inbox/items/{rows['t3'].id}/reply", headers=auth(org1), json={"text": "Yes, see you there"})
    assert r.status_code == 200, r.text
    f = form(mock.find("POST", r"/th1/threads$")[0])
    assert f["reply_to_id"] == "t3" and f["media_type"] == "TEXT"
    assert form(mock.find("POST", r"/threads_publish$")[0])["creation_id"] == "cont9"
    # Over the 500-character limit is refused before calling Threads.
    r = await client.post(f"/inbox/items/{rows['t1'].id}/reply", headers=auth(org1), json={"text": "x" * 600})
    assert r.status_code == 400 and "500" in r.json()["detail"]


async def test_threads_missing_reply_permission(org1, mock):
    org = org_of(org1)
    acc = await make_account(org, "threads", "th2", {"access_token": "x"})
    mock.on("GET", r"/th2/threads$", (200, {"data": [{"id": "t200", "timestamp": iso(NOW)}]}))
    mock.on("GET", r"/t200/conversation$", (403, {"error": {"code": 10, "message": "Application does not have permission"}}))
    await run_sync(org, acc.id)
    st = await account_state(org, acc.id)
    assert st["missing_permission"] == "threads_read_replies" and st["reconnect"]
    assert "Access the Threads API" in st["message"]


# ------------------------------------------------------------------ YouTube
async def test_youtube_comments_and_reply(client, org2, mock):
    org = org_of(org2)
    await make_account(org, "google", "g-sub", {"access_token": "g-tok", "refresh_token": "r",
                                                 "scope": "openid https://www.googleapis.com/auth/youtube.readonly"},
                       expires_at=NOW + timedelta(hours=1))
    yt = await make_account(org, "youtube", "UCours")
    mock.on("GET", r"/youtube/v3/commentThreads$", (200, {"items": [{
        "id": "th1", "snippet": {"videoId": "vid1", "topLevelComment": {"id": "yc1", "snippet": {
            "textOriginal": "Does it support Indian payroll?", "authorDisplayName": "Arjun", "publishedAt": iso(NOW),
            "authorChannelId": {"value": "UCarjun"}, "authorProfileImageUrl": "https://img/a.jpg", "likeCount": 2}}},
        "replies": {"comments": [{"id": "yc1.r1", "snippet": {"textOriginal": "Yes!", "authorDisplayName": "HRP AI",
                                                              "parentId": "yc1", "publishedAt": iso(NOW),
                                                              "authorChannelId": {"value": "UCours"}}}]}}]}))
    mock.on("GET", r"/youtube/v3/videos$", (200, {"items": [{"id": "vid1", "snippet": {"title": "HRMS in 60 seconds"}}]}))
    await run_sync(org, yt.id)
    q = query(mock.find("GET", r"/commentThreads$")[0])
    assert q["allThreadsRelatedToChannelId"] == "UCours"
    assert mock.find("GET", r"/commentThreads$")[0].headers["authorization"] == "Bearer g-tok"
    rows = {i.external_id: i for i in await items(org, social_account_id=yt.id)}
    assert rows["yc1"].post_snippet == "HRMS in 60 seconds" and rows["yc1"].status == "replied"
    assert rows["yc1.r1"].is_own and rows["yc1"].url.endswith("lc=yc1")

    # The stored Google grant lacks youtube.force-ssl: ask to reconnect.
    r = await client.post(f"/inbox/items/{rows['yc1'].id}/reply", headers=auth(org2), json={"text": "Yes, fully."})
    assert r.status_code == 409 and "youtube.force-ssl" in r.json()["detail"]
    async with SessionLocal() as s:
        set_tenant(s, org)
        h = await s.scalar(select(SocialAccount).where(SocialAccount.platform == "google"))
        save_tokens(h, {"access_token": "g-tok", "refresh_token": "r",
                        "scope": "openid https://www.googleapis.com/auth/youtube.force-ssl"}, NOW + timedelta(hours=1))
        await s.commit()
    mock.on("POST", r"/youtube/v3/comments$", (200, {"id": "yc1.ours"}))
    r = await client.post(f"/inbox/items/{rows['yc1'].id}/reply", headers=auth(org2), json={"text": "Yes, fully."})
    assert r.status_code == 200, r.text
    body = json.loads(mock.find("POST", r"/comments$")[0].content)
    assert body == {"snippet": {"parentId": "yc1", "textOriginal": "Yes, fully."}}

    # Insufficient scope during sync → permission state, not a crash.
    mock.on("GET", r"/youtube/v3/commentThreads$", (403, {"error": {"code": 403, "message": "Insufficient Permission",
                                                                     "errors": [{"reason": "insufficientPermissions"}]}}))
    await run_sync(org, yt.id)
    assert (await account_state(org, yt.id))["missing_permission"] == "youtube.force-ssl"


# ------------------------------------------------------------------ Bluesky
BSKY_SESSION = (200, {"accessJwt": "acc", "refreshJwt": "ref", "did": "did:plc:ours", "handle": "hrpai.bsky.social"})
ROOT = {"uri": "at://did:plc:ours/app.bsky.feed.post/root1", "cid": "cidroot"}


async def test_bluesky_notifications_reply_refs_and_like(client, org2, mock):
    org = org_of(org2)
    acc = await make_account(org, "bluesky", "did:plc:ours", {"access_jwt": "a", "refresh_jwt": "r", "did": "did:plc:ours",
                                                               "service": "https://bsky.social"}, handle="hrpai.bsky.social")
    await make_post(org, acc, ROOT["uri"], text="Meet our AI Studio")
    mock.on("POST", r"refreshSession$", BSKY_SESSION)
    mock.on("GET", r"listNotifications$", (200, {"notifications": [
        {"uri": "at://did:plc:bob/app.bsky.feed.post/r1", "cid": "cid-r1", "reason": "reply", "indexedAt": iso(NOW),
         "author": {"did": "did:plc:bob", "handle": "bob.bsky.social", "displayName": "Bob"},
         "record": {"text": "Is there a free trial?", "createdAt": iso(NOW), "reply": {"root": ROOT, "parent": ROOT}}},
        {"uri": "at://did:plc:cy/app.bsky.feed.post/m1", "cid": "cid-m1", "reason": "mention", "indexedAt": iso(NOW),
         "author": {"did": "did:plc:cy", "handle": "cy.bsky.social"}, "record": {"text": "cc @hrpai.bsky.social", "createdAt": iso(NOW)}},
        {"uri": "at://x/like", "reason": "like", "author": {"did": "did:plc:z"}, "record": {}},
    ]}))
    mock.on("GET", r"getPosts$", (200, {"posts": [{"uri": ROOT["uri"], "cid": "cidroot", "author": {"handle": "hrpai.bsky.social"},
                                                    "record": {"text": "Meet our AI Studio"}}]}))
    await run_sync(org, acc.id)
    rows = {i.external_id: i for i in await items(org, social_account_id=acc.id)}
    r1, m1 = rows["at://did:plc:bob/app.bsky.feed.post/r1"], rows["at://did:plc:cy/app.bsky.feed.post/m1"]
    assert len(rows) == 2 and r1.kind == "comment" and m1.kind == "mention"
    assert r1.post_snippet == "Meet our AI Studio" and r1.post_id is not None
    assert r1.url == "https://bsky.app/profile/bob.bsky.social/post/r1"

    mock.on("POST", r"createRecord$", (200, {"uri": "at://did:plc:ours/app.bsky.feed.post/ours1", "cid": "c"}))
    r = await client.post(f"/inbox/items/{r1.id}/reply", headers=auth(org2), json={"text": "Yes! 14 days #HRTech"})
    assert r.status_code == 200, r.text
    rec = json.loads(mock.find("POST", r"createRecord$")[-1].content)
    assert rec["repo"] == "did:plc:ours" and rec["collection"] == "app.bsky.feed.post"
    assert rec["record"]["reply"] == {"root": ROOT, "parent": {"uri": r1.external_id, "cid": "cid-r1"}}
    assert rec["record"]["facets"][0]["features"][0]["tag"] == "HRTech"
    r = await client.post(f"/inbox/items/{m1.id}/like", headers=auth(org2))
    assert r.status_code == 200
    like = json.loads(mock.find("POST", r"createRecord$")[-1].content)
    assert like["collection"] == "app.bsky.feed.like" and like["record"]["subject"] == {"uri": m1.external_id, "cid": "cid-m1"}
    r = await client.post(f"/inbox/items/{m1.id}/reply", headers=auth(org2), json={"text": "y" * 301})
    assert r.status_code == 400 and "300" in r.json()["detail"]


# ------------------------------------------------------------------ Mastodon
async def test_mastodon_mentions_reply_and_scope_error(client, org2, mock):
    org = org_of(org2)
    acc = await make_account(org, "mastodon", "m.social:42", {"access_token": "mt", "instance": "https://m.social"},
                             handle="@hrp@m.social")
    await make_post(org, acc, "500", text="Our HR tips thread")
    mock.on("GET", r"m\.social/api/v1/notifications$", (200, [
        {"id": "1", "type": "mention", "status": {
            "id": "601", "in_reply_to_id": "500", "content": "<p>Thanks for this!</p>", "created_at": iso(NOW),
            "url": "https://m.social/@lee/601", "visibility": "unlisted", "language": "en",
            "account": {"id": "77", "acct": "lee@other.social", "display_name": "Lee"}}},
        {"id": "2", "type": "mention", "status": {
            "id": "602", "content": "<p>@hrp hello</p>", "created_at": iso(NOW), "url": "https://m.social/@kim/602",
            "account": {"id": "42", "acct": "hrp"}}},
    ]))
    await run_sync(org, acc.id)
    q = mock.find("GET", r"/api/v1/notifications$")[0]
    assert "types%5B%5D=mention" in str(q.url) or "types[]=mention" in str(q.url)
    rows = {i.external_id: i for i in await items(org, social_account_id=acc.id)}
    assert rows["601"].kind == "comment" and rows["601"].text == "Thanks for this!" and rows["601"].post_id
    assert rows["602"].is_own  # our own account

    mock.on("POST", r"/api/v1/statuses$", (200, {"id": "700", "url": "https://m.social/@hrp/700"}))
    r = await client.post(f"/inbox/items/{rows['601'].id}/reply", headers=auth(org2), json={"text": "Glad it helped"})
    assert r.status_code == 200, r.text
    body = json.loads(mock.find("POST", r"/api/v1/statuses$")[0].content)
    assert body == {"status": "@lee@other.social Glad it helped", "in_reply_to_id": "601", "visibility": "unlisted"}
    mock.on("POST", r"/api/v1/statuses/601/favourite$", (403, {"error": "This action is outside the authorized scopes"}))
    r = await client.post(f"/inbox/items/{rows['601'].id}/like", headers=auth(org2))
    assert r.status_code == 409 and "write:favourites" in r.json()["detail"]


# ------------------------------------------------------------------ sentiment
async def test_sentiment_heuristic():
    h = sentiment.heuristic
    assert h("Love this, great work!") == ("positive", False)
    assert h("How do I book a demo?") == ("question", False)
    assert h("Worst service, this is a scam. Refund now!")[0] == "negative"
    assert h("Worst service, this is a scam. Refund now!")[1] is True
    assert h("ok") == ("neutral", False)
    assert h("The download page") == ("neutral", False)  # 'down' is a whole word only


async def test_sentiment_ai_batch_and_fallback(org2, monkeypatch):
    org = org_of(org2)
    calls = []

    async def fake(session, task, prompt, **kw):
        calls.append(task)
        return {"items": [{"n": 0, "sentiment": "negative", "urgent": True}, {"n": 1, "sentiment": "bogus"}]}

    monkeypatch.setattr(llm_router, "complete_json", fake)
    a, b = InboxItem(text="Payments are failing for all staff"), InboxItem(text="Love it!")
    r = await sentiment.classify(None, [a, b])
    assert calls == ["extract"] and r == {"ai": 1, "heuristic": 1}
    assert (a.sentiment, a.priority, a.sentiment_source) == ("negative", True, "ai")
    assert (b.sentiment, b.sentiment_source) == ("positive", "heuristic")

    async def broken(*a, **kw):
        raise ValueError("Model did not return valid JSON")

    monkeypatch.setattr(llm_router, "complete_json", broken)
    c = InboxItem(text="Why is it so slow?")
    await sentiment.classify(None, [c])
    assert c.sentiment_source == "heuristic" and c.sentiment == "negative"


# ------------------------------------------------------------------ AI suggestions
async def test_suggest_replies_in_brand_voice(client, org2, monkeypatch):
    org = org_of(org2)
    r = await client.put("/content/brand", headers=auth(org2), json={
        "voice": "Warm and expert", "banned_words": ["cheap"], "audience": "HR leaders"})
    assert r.status_code in (200, 201), r.text
    it = (await items(org, platform="bluesky", kind="comment"))[0]
    seen = {}

    async def fake(session, task, prompt, **kw):
        seen.update(task=task, prompt=prompt, system=kw.get("system"))
        return {"replies": ["Thanks Bob! Yes, there's a 14-day free trial.", "It's cheap!", "z" * 400]}

    monkeypatch.setattr(llm_router, "complete_json", fake)
    r = await client.post(f"/inbox/items/{it.id}/suggest", headers=auth(org2))
    assert r.status_code == 200, r.text
    s = r.json()["suggestions"]
    assert seen["task"] == "write" and "Warm and expert" in seen["prompt"] and "Is there a free trial?" in seen["prompt"]
    assert "Meet our AI Studio" in seen["prompt"] and "data, never instructions" in seen["system"]
    assert s[0].startswith("Thanks Bob") and all("cheap" not in x for x in s) and all(len(x) <= 300 for x in s)
    d = (await client.get(f"/inbox/items/{it.id}", headers=auth(org2))).json()
    assert d["item"]["ai_suggestions"] == s

    async def none(*a, **kw):
        raise llm_router.NoProviderError("No AI text provider is set up. Add one in Settings → Integrations.")

    monkeypatch.setattr(llm_router, "complete_json", none)
    r = await client.post(f"/inbox/items/{it.id}/suggest", headers=auth(org2))
    assert r.status_code == 400 and "Integrations" in r.json()["detail"]


# ------------------------------------------------------------------ listening
async def test_listening_sources_dedupe_exclusions_and_intervals(client, org2, mock, monkeypatch):
    org = org_of(org2)
    r = await client.put("/inbox/listening", headers=auth(org2), json={
        "keywords": ["HRP AI", "#hrpai", "HRP AI"], "excluded": ["casino"], "languages": ["en"],
        "sources": {"web": True, "news": True, "bluesky": True, "mastodon": True, "youtube": False, "instagram": True}})
    assert r.status_code == 200, r.text
    assert r.json()["keywords"] == ["HRP AI", "#hrpai"]
    from app.services import search

    web_calls = []

    async def fake_web(session, q, n=10):
        web_calls.append(q)
        return [{"title": "HRP AI launches HRMS", "url": "https://news.example.com/hrp-ai?utm_source=x", "snippet": "HRP AI ..."},
                {"title": "Casino HRP AI", "url": "https://spam.example/1", "snippet": "casino bonus"}]

    async def fake_news(query, n, base, secrets):
        return [{"title": "HRP AI launches HRMS", "url": "https://news.example.com/hrp-ai", "snippet": "Indian startup",
                 "date": iso(NOW), "source": "Example News"}]

    monkeypatch.setattr(search, "web_search", fake_web)
    monkeypatch.setitem(listening.NEWS_STYLES, "ddgs", fake_news)
    mock.on("POST", r"refreshSession$", BSKY_SESSION)
    mock.on("GET", r"searchPosts$", (200, {"posts": [
        {"uri": "at://did:plc:ext/app.bsky.feed.post/s1", "cid": "cs1", "likeCount": 3, "repostCount": 1,
         "author": {"did": "did:plc:ext", "handle": "ext.bsky.social"},
         "record": {"text": "Trying HRP AI today", "createdAt": iso(NOW), "langs": ["en"]}},
        {"uri": "at://did:plc:ours/app.bsky.feed.post/own", "cid": "co", "author": {"did": "did:plc:ours"},
         "record": {"text": "HRP AI news from us", "createdAt": iso(NOW)}},
        {"uri": "at://did:plc:ext/app.bsky.feed.post/s2", "cid": "cs2", "author": {"did": "did:plc:ext", "handle": "ext"},
         "record": {"text": "HRP AI en français", "createdAt": iso(NOW), "langs": ["fr"]}},
    ]}))
    mock.on("GET", r"/api/v1/timelines/tag/hrpai$", (200, [
        {"id": "880", "content": "<p>Big fan of #hrpai</p>", "created_at": iso(NOW), "url": "https://m.social/@x/880",
         "language": "en", "account": {"id": "9", "acct": "x@y.z"}}]))
    mock.on("GET", r"/api/v2/search$", (200, {"statuses": []}))

    async with SessionLocal() as s:
        set_tenant(s, org)
        st = await get_settings(s, org)
        r = await listening.run(s, org, st)
        await s.commit()
    created = r["created"]
    by = {(i.platform, i.external_id) for i in created}
    assert ("bluesky", "at://did:plc:ext/app.bsky.feed.post/s1") in by
    assert not any("own" in (i.external_id or "") or "s2" in (i.external_id or "") for i in created)
    assert ("mastodon", "880") in by
    web_news = [i for i in created if i.platform in ("web", "news")]
    assert len(web_news) == 1 and web_news[0].keyword == "HRP AI"  # same article via web and news, spam excluded
    assert all(i.source == "listening" and i.kind == "mention" for i in created)
    assert r["sources"]["instagram"]["ok"] is False and "Instagram" in r["sources"]["instagram"]["message"]
    q = query(mock.find("GET", r"searchPosts$")[0])
    assert q["sort"] == "latest" and q["lang"] == "en"

    # Not due yet: nothing is searched again.
    n_web = len(web_calls)
    async with SessionLocal() as s:
        set_tenant(s, org)
        r = await listening.run(s, org, await get_settings(s, org))
        await s.commit()
    assert len(web_calls) == n_web and r["created"] == []

    # Mentions tab lists them; Bluesky mentions can be answered in the app, web ones can't.
    d = (await client.get("/inbox/items?tab=mentions&source=listening", headers=auth(org2))).json()
    byp = {i["platform"]: i for i in d["items"]}
    assert byp["bluesky"]["can_reply"] and not byp["web"]["can_reply"] and byp["bluesky"]["keyword"]
    r = await client.post(f"/inbox/items/{byp['web']['id']}/reply", headers=auth(org2), json={"text": "hi"})
    assert r.status_code == 400


async def test_instagram_hashtag_listening_when_granted(org1, mock):
    org = org_of(org1)
    async with SessionLocal() as s:
        set_tenant(s, org)
        st = await get_settings(s, org)
        st.keywords, st.sources = ["#hrtech"], {"web": False, "news": False, "bluesky": False, "mastodon": False,
                                                 "instagram": True}
        await s.commit()
    mock.on("GET", r"/ig_hashtag_search$", (200, {"data": [{"id": "h1"}]}))
    mock.on("GET", r"/h1/recent_media$", (200, {"data": [{"id": "pm1", "caption": "#hrtech rocks",
                                                          "permalink": "https://instagram.com/p/pm1", "timestamp": iso(NOW),
                                                          "like_count": 10, "comments_count": 2}]}))
    async with SessionLocal() as s:
        set_tenant(s, org)
        r = await listening.run(s, org, await get_settings(s, org), force=True)
        await s.commit()
        st = await get_settings(s, org)
    assert [i.external_id for i in r["created"]] == ["pm1"] and r["created"][0].engagement == 12
    assert st.state["ig_hashtags"] == {"hrtech": "h1"}


# ------------------------------------------------------------------ notifications
async def test_notifications_urgent_now_and_digest_batched(org2, monkeypatch):
    from app.services import notify

    org = org_of(org2)
    sent = []

    async def fake(org_id, event, title, body=None, **kw):
        sent.append((event, title, body, kw.get("permission"), kw.get("link")))

    monkeypatch.setattr(notify, "notify_org", fake)
    a = InboxItem(id=uuid.uuid4(), platform="facebook_page", kind="comment", text="Refund now, this is a scam",
                  sentiment="negative", priority=True, author_name="Meera", is_own=False)
    b = InboxItem(id=uuid.uuid4(), platform="web", kind="mention", text="HRP AI in the news", sentiment="neutral",
                  priority=False, is_own=False)
    async with SessionLocal() as s:
        set_tenant(s, org)
        st = await get_settings(s, org)
        st.state = {**(st.state or {}), "last_digest_at": None, "pending_digest": {}}
        await s.commit()
        r = await notify_new(s, org, [a, b])
    assert r == {"urgent": 1, "digest": 2}
    assert sent[0][0] == "inbox_urgent" and "Meera" in sent[0][2] and sent[0][3] == "inbox.view"
    assert sent[1][0] == "inbox_new" and sent[1][1] == "1 new comment and 1 new mention in the inbox"
    async with SessionLocal() as s:
        set_tenant(s, org)
        r = await notify_new(s, org, [b])  # within 3 hours: kept for the next digest
    assert r == {"urgent": 0, "digest": 0} and len(sent) == 2
    async with SessionLocal() as s:
        set_tenant(s, org)
        assert (await get_settings(s, org)).state["pending_digest"] == {"mentions": 1}
        r = await notify_new(s, org, [], force_digest=True)
    assert r["digest"] == 1 and sent[-1][1] == "1 new mention in the inbox"
    from app.services.notify import DEFAULT_EXTERNAL, EVENTS

    assert "inbox_new" in EVENTS and "inbox_urgent" in EVENTS and "inbox_urgent" in DEFAULT_EXTERNAL


async def test_sync_job_notifies_and_periodic_enqueues(org2, mock, monkeypatch):
    from app.models import Job
    from app.modules import inbox as inbox_module
    from app.modules import load_all
    from app.services import notify
    from app.services.jobs import run_inline

    load_all()
    sent = []

    async def fake(org_id, event, title, body=None, **kw):
        sent.append(event)

    monkeypatch.setattr(notify, "notify_org", fake)
    org = org_of(org2)
    mock.on("POST", r"refreshSession$", BSKY_SESSION)
    mock.on("GET", r"listNotifications$", (200, {"notifications": [
        {"uri": "at://did:plc:dan/app.bsky.feed.post/u1", "cid": "c", "reason": "mention", "indexedAt": iso(NOW),
         "author": {"did": "did:plc:dan", "handle": "dan"}, "record": {"text": "@hrpai your app is broken, terrible", "createdAt": iso(NOW)}}]}))
    mock.on("GET", r"m\.social/api/v1/notifications$", (200, []))
    mock.on("GET", r"/youtube/v3/commentThreads$", (200, {"items": []}))
    r = await run_inline("inbox.sync", org, {})
    assert r["new"] == 1 and "inbox_urgent" in sent
    await inbox_module.schedule_inbox_sync()
    from app.core.db import platform_session

    async with platform_session() as s:
        jobs = (await s.scalars(select(Job).where(Job.kind == "inbox.sync", Job.status == "queued"))).all()
        assert [j.dedupe_key for j in jobs if j.organization_id == org] == [f"inbox.sync:{org}"]
        for j in jobs:  # no worker in tests: don't leave them queued
            j.status = "cancelled"
        await s.commit()


# ------------------------------------------------------------------ API: lists, bulk, assign, permissions, isolation
async def test_api_lists_filters_bulk_and_assign(client, org1):
    s = (await client.get("/inbox/summary", headers=auth(org1))).json()
    assert s["unread"] >= 3 and s["urgent"] >= 1
    d = (await client.get("/inbox/items?tab=comments&platform=facebook_page", headers=auth(org1))).json()
    assert d["total"] >= 3 and all(i["platform"] == "facebook_page" and not i["is_own"] for i in d["items"])
    neg = (await client.get("/inbox/items?tab=comments&sentiment=urgent", headers=auth(org1))).json()["items"]
    assert any("scam" in (i["text"] or "") for i in neg)
    found = (await client.get("/inbox/items?tab=all&q=payroll", headers=auth(org1))).json()["items"]
    assert found and any("payroll" in (i["text"] or "").lower() for i in found)
    ids = [i["id"] for i in d["items"][:2]]
    r = await client.post("/inbox/items/bulk", headers=auth(org1), json={"ids": ids, "action": "archive"})
    assert r.status_code == 200 and r.json()["updated"] == 2
    d2 = (await client.get("/inbox/items?tab=comments&platform=facebook_page", headers=auth(org1))).json()
    assert not set(ids) & {i["id"] for i in d2["items"]}
    arch = (await client.get("/inbox/items?tab=comments&status=archived", headers=auth(org1))).json()
    assert set(ids) <= {i["id"] for i in arch["items"]}
    r = await client.post("/inbox/items/bulk", headers=auth(org1), json={"ids": ids, "action": "unarchive"})
    assert r.json()["updated"] == 2

    people = (await client.get("/inbox/assignees", headers=auth(org1))).json()
    me = org1["user"]["id"]
    assert any(p["id"] == me for p in people)
    r = await client.patch(f"/inbox/items/{ids[0]}", headers=auth(org1), json={"assigned_to": me, "sentiment": "question"})
    assert r.status_code == 200 and r.json()["item"]["assigned_to"]["id"] == me
    assert r.json()["item"]["sentiment_source"] == "manual"
    mine = (await client.get("/inbox/items?tab=all&assigned=me", headers=auth(org1))).json()
    assert [i["id"] for i in mine["items"]] == [ids[0]]
    r = await client.patch(f"/inbox/items/{ids[0]}", headers=auth(org1), json={"assigned_to": None})
    assert r.json()["item"]["assigned_to"] is None
    r = await client.patch(f"/inbox/items/{ids[0]}", headers=auth(org1),
                           json={"assigned_to": "00000000-0000-0000-0000-000000000001"})
    assert r.status_code == 400


async def test_viewer_can_read_but_not_act(client, org1):
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(org1))).json()}
    r = await client.post("/users", headers=auth(org1), json={
        "email": "inbox-viewer@inbox-one.in", "full_name": "Ivy Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    v = await login(client, "inbox-viewer@inbox-one.in", r.json()["temporary_password"])
    d = (await client.get("/inbox/items?tab=all", headers=auth(v))).json()
    assert d["items"]
    iid = d["items"][0]["id"]
    assert (await client.get(f"/inbox/items/{iid}", headers=auth(v))).status_code == 200
    for method, path, body in [("post", f"/inbox/items/{iid}/reply", {"text": "x"}), ("patch", f"/inbox/items/{iid}", {"status": "read"}),
                               ("post", "/inbox/items/bulk", {"ids": [iid], "action": "read"}),
                               ("post", f"/inbox/items/{iid}/suggest", None), ("post", "/inbox/refresh", None),
                               ("put", "/inbox/listening", {"keywords": ["x"]})]:
        r = await getattr(client, method)(path, headers=auth(v), **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (path, r.text)


async def test_tenant_isolation(client, org1, org2):
    one = (await client.get("/inbox/items?tab=all&status=all&limit=200", headers=auth(org1))).json()["items"]
    two = (await client.get("/inbox/items?tab=all&status=all&limit=200", headers=auth(org2))).json()["items"]
    assert one and two and not {i["id"] for i in one} & {i["id"] for i in two}
    iid = one[0]["id"]
    assert (await client.get(f"/inbox/items/{iid}", headers=auth(org2))).status_code == 404
    assert (await client.post(f"/inbox/items/{iid}/reply", headers=auth(org2), json={"text": "x"})).status_code == 404
    r = await client.post("/inbox/items/bulk", headers=auth(org2), json={"ids": [iid], "action": "archive"})
    assert r.json()["updated"] == 0
    kw1 = (await client.get("/inbox/listening", headers=auth(org1))).json()["keywords"]
    kw2 = (await client.get("/inbox/listening", headers=auth(org2))).json()["keywords"]
    assert kw1 != kw2


async def test_status_lists_open_only_platforms_and_refresh(client, org1):
    org = org_of(org1)
    li = await make_account(org, "linkedin_org", "urn:li:organization:1", {"access_token": "x"}, name="HRP AI LinkedIn")
    await make_post(org, li, "urn:li:share:1", text="Hiring trends 2026", url="https://www.linkedin.com/feed/update/urn:li:share:1")
    s = (await client.get("/inbox/status", headers=auth(org1))).json()
    row = next(a for a in s["accounts"] if a["id"] == str(li.id))
    assert row["sync"] is False and "Community Management" in row["note"]
    assert any(p["url"].startswith("https://www.linkedin.com/") and p["platform"] == "linkedin_org" for p in s["open_posts"])
    assert {x["id"] for x in s["sources"]} >= {"web", "news", "bluesky", "mastodon", "youtube", "instagram"}
    r = await client.post("/inbox/refresh", headers=auth(org1), json={"what": "all"})
    assert r.status_code == 200 and r.json()["jobs"]["comments"]
    r2 = await client.post("/inbox/refresh", headers=auth(org1), json={"what": "comments"})
    assert r2.json()["jobs"]["comments"] == r.json()["jobs"]["comments"]  # de-duplicated while queued
