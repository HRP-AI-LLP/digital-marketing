"""New networks: X (OAuth 2.0 PKCE, threads, media upload, plan limits, delete), TikTok (PKCE, creator info,
FILE_UPLOAD / PULL_FROM_URL, private posting for unaudited apps), Pinterest gaps, Canva import, and the public
link-in-bio page (rendering, click redirect, counts, tunnel guard). No real network."""

import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import DesignConnection, LinkPageStat, MediaAsset, OAuthState, Post, SocialAccount
from app.services import publishing
from app.services.publishing import PublishError, account_tokens
from app.services.social import tiktok, x
from tests.conftest import auth, login
from tests.test_publishing import (  # noqa: F401 - fixtures
    do_publish, form, make_account, make_media, make_post, mock, org_of, public_media, query,
)

ADMIN_PW = "Str0ngPassw0rd!"
TUNNEL = {"cf-connecting-ip": "203.0.113.9", "cf-ray": "abc-BOM"}
BROWSER = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
ROOT = "http://test"  # the client's base_url is http://test/api/v1


@pytest.fixture(scope="module", autouse=True)
def temp_storage(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage-networks"))
    yield
    s.storage_dir = old


def png_bytes(color=(10, 80, 160), size=(40, 30), fmt="PNG") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture(scope="module")
async def org(client, admin):
    t = await login(client, "admin@hrpai.in", ADMIN_PW)
    existing = {p["provider"] for p in (await client.get("/integrations/providers", headers=auth(t))).json()}
    for provider, secrets in {
        "x_app": {"client_id": "x-id", "client_secret": "x-secret"},
        "tiktok_app": {"client_id": "tt-key", "client_secret": "tt-secret"},
        "canva_app": {"client_id": "cv-id", "client_secret": "cv-secret"},
    }.items():
        if provider not in existing:
            r = await client.post("/integrations/providers", headers=auth(t), json={"provider": provider, "secrets": secrets})
            assert r.status_code == 201, r.text
    return t


async def start(client, t, path, body=None):
    r = await client.post(path, headers=auth(t), json=body)
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    return url, parse_qs(urlsplit(url).query)


# ============================================================================ X
async def test_x_weighted_length_and_thread_split():
    assert x.weighted_len("hello") == 5
    assert x.weighted_len("see https://example.com/a/very/long/path/that/goes/on") == 4 + 23
    assert x.weighted_len("日本") == 4
    short = "Short post #AI"
    assert x.split_thread(short) == [short]
    para = ("HR teams spend hours on manual onboarding. " * 4).strip()
    text = "\n\n".join([para, para, para, "#HRTech #AI"])
    parts = x.split_thread(text)
    assert len(parts) > 1
    assert all(x.weighted_len(p) <= 280 for p in parts)
    assert parts[0].endswith(f" 1/{len(parts)}") and parts[-1].endswith(f"{len(parts)}/{len(parts)}")
    assert "#HRTech #AI" in parts[-1]
    joined = " ".join(re.sub(r" \d+/\d+$", "", p) for p in parts)
    assert joined.split() == text.split()  # nothing lost, never split mid-word
    giant = "a" * 700
    assert all(x.weighted_len(p) <= 280 for p in x.split_thread(giant))


async def test_x_connect_uses_pkce_and_creates_account(client, org, mock):
    url, q = await start(client, org, "/social/connect/x")
    assert url.startswith("https://x.com/i/oauth2/authorize")
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    assert set(q["scope"][0].split()) == {"tweet.read", "tweet.write", "users.read", "offline.access", "media.write"}
    state = q["state"][0]
    async with platform_session() as s:
        verifier = (await s.get(OAuthState, state)).data["code_verifier"]

    def token(req):
        f = form(req)
        assert f["code"] == "x-code" and f["code_verifier"] == verifier and f["grant_type"] == "authorization_code"
        assert req.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"access_token": "x-at", "refresh_token": "x-rt", "expires_in": 7200,
                                         "scope": "tweet.read tweet.write users.read offline.access media.write"})

    mock.on("POST", r"api\.x\.com/2/oauth2/token$", token)
    mock.on("GET", r"api\.x\.com/2/users/me$", (200, {"data": {"id": "4242", "name": "HRP AI", "username": "hrpai"}}))
    r = await client.get(f"/social/oauth/x/callback?code=x-code&state={state}", follow_redirects=False)
    assert r.status_code == 302 and "connected=x" in r.headers["location"], r.headers["location"]
    async with SessionLocal() as s:
        set_tenant(s, org_of(org))
        acc = await s.scalar(select(SocialAccount).where(SocialAccount.platform == "x", SocialAccount.external_id == "4242"))
        assert acc.handle == "@hrpai" and account_tokens(acc)["refresh_token"] == "x-rt"
        assert acc.settings["collect_metrics"] is False


async def test_x_thread_with_image(org, mock):
    o = org_of(org)
    acc = await make_account(o, "x", "x-thread", {"access_token": "at", "refresh_token": "rt"},
                             expires_at=datetime.now(UTC) + timedelta(hours=1))
    img = await make_media(o, alt_text="Team photo")
    long = "\n\n".join(["Sentence number one is here. " * 5] * 3)
    post = await make_post(o, acc, "image", [img], text=long, hashtags=["HRTech"])
    mock.on("POST", r"/2/media/upload/initialize$", (200, {"data": {"id": "m1"}}))
    mock.on("POST", r"/2/media/upload/m1/append$", (200, {}))
    mock.on("POST", r"/2/media/upload/m1/finalize$", (200, {"data": {"id": "m1"}}))
    mock.on("POST", r"/2/media/metadata$", (200, {}))
    n = {"i": 0}

    def tweet(req):
        n["i"] += 1
        return httpx.Response(201, json={"data": {"id": f"t{n['i']}"}})

    mock.on("POST", r"/2/tweets$", tweet)
    ext, url = await do_publish(o, post, acc)
    assert ext == "t1" and url == "https://x.com/hrpai/status/t1"
    bodies = [json.loads(r.content) for r in mock.find("POST", r"/2/tweets$")]
    assert len(bodies) >= 2
    assert bodies[0]["media"] == {"media_ids": ["m1"]} and "reply" not in bodies[0]
    assert bodies[1]["reply"] == {"in_reply_to_tweet_id": "t1"} and "media" not in bodies[1]
    assert "#HRTech" in bodies[-1]["text"]
    init = json.loads(mock.find("POST", r"/initialize$")[0].content)
    assert init["media_category"] == "tweet_image" and init["media_type"] == "image/png"
    async with SessionLocal() as s:
        set_tenant(s, o)
        assert (await s.get(Post, post.id)).meta["x_thread_ids"][:2] == ["t1", "t2"]


async def test_x_plan_limits_are_explained_not_reconnect(org, mock):
    o = org_of(org)
    acc = await make_account(o, "x", "x-tier", {"access_token": "at", "refresh_token": "rt"},
                             expires_at=datetime.now(UTC) + timedelta(hours=1))
    mock.on("POST", r"/2/tweets$", (402, {"title": "Payment Required", "detail": "Your enrolled account does not have any credits",
                                         "type": "https://api.twitter.com/2/problems/credits"}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", text="hello"), acc)
    assert not e.value.reconnect and not e.value.retryable and "plan" in e.value.message
    mock.on("POST", r"/2/tweets$", (403, {"detail": "You are not allowed to create a Tweet with duplicate content."}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", text="hello"), acc)
    assert "duplicate" in e.value.message and not e.value.reconnect
    mock.on("POST", r"/2/tweets$", (429, {"title": "Too Many Requests"}, {"x-user-limit-24hour-remaining": "0"}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", text="hello"), acc)
    assert "daily" in e.value.message and not e.value.retryable


async def test_x_refresh_rotation_survives_rollback(org, mock):
    o = org_of(org)
    acc = await make_account(o, "x", "x-rot", {"access_token": "old", "refresh_token": "rt-1"},
                             expires_at=datetime.now(UTC) + timedelta(minutes=1))
    mock.on("POST", r"/2/oauth2/token$", (200, {"access_token": "new-at", "refresh_token": "rt-2", "expires_in": 7200}))
    mock.on("POST", r"/2/tweets$", (500, {"title": "boom"}))
    with pytest.raises(PublishError):
        async with SessionLocal() as s:
            set_tenant(s, o)
            a = await s.get(SocialAccount, acc.id)
            p = await s.get(Post, (await make_post(o, acc, "text", text="hi")).id)
            try:
                await publishing.publish(s, p, a)
            finally:
                await s.rollback()  # the publish job rolls back on failure
    assert form(mock.find("POST", r"/oauth2/token$")[0])["refresh_token"] == "rt-1"
    async with SessionLocal() as s:
        set_tenant(s, o)
        assert account_tokens(await s.get(SocialAccount, acc.id))["refresh_token"] == "rt-2"


async def test_x_delete_thread_endpoint_and_metrics_opt_in(client, org, mock):
    o = org_of(org)
    acc = await make_account(o, "x", "x-del", {"access_token": "at", "refresh_token": "rt"},
                             expires_at=datetime.now(UTC) + timedelta(hours=1))
    post = await make_post(o, acc, "text", text="bye")
    async with SessionLocal() as s:
        set_tenant(s, o)
        p = await s.get(Post, post.id)
        p.status, p.external_id, p.external_url = "published", "t1", "https://x.com/hrpai/status/t1"
        p.meta = {"x_thread_ids": ["t1", "t2"]}
        await s.commit()
    # Metrics are off by default (reading costs credits / isn't in the free tier).
    async with SessionLocal() as s:
        set_tenant(s, o)
        assert await publishing.fetch_metrics(s, await s.get(Post, post.id), await s.get(SocialAccount, acc.id)) is None
    assert not mock.find("GET", r"/2/tweets/t1$")
    # Switched on but refused by the plan: friendly note, switched off again.
    r = await client.patch(f"/social/accounts/{acc.id}", headers=auth(org), json={"settings": {"collect_metrics": True}})
    assert r.status_code == 200 and r.json()["settings"]["collect_metrics"] is True and r.json()["can_delete_posts"] is True
    mock.on("GET", r"/2/tweets/t1$", (403, {"title": "Forbidden", "detail": "client-not-enrolled"}))
    async with SessionLocal() as s:
        set_tenant(s, o)
        a = await s.get(SocialAccount, acc.id)
        assert await publishing.fetch_metrics(s, await s.get(Post, post.id), a) is None
        assert a.settings["collect_metrics"] is False and "doesn't include" in a.status_message
        await s.commit()

    mock.on("DELETE", r"/2/tweets/t\d$", (200, {"data": {"deleted": True}}))
    r = await client.post(f"/social/posts/{post.id}/delete-remote", headers=auth(org))
    assert r.status_code == 200, r.text
    assert [str(q.url).rsplit("/", 1)[-1] for q in mock.find("DELETE", r"/2/tweets/")] == ["t2", "t1"]
    async with SessionLocal() as s:
        set_tenant(s, o)
        assert (await s.get(Post, post.id)).status == "removed"
    assert (await client.post(f"/social/posts/{post.id}/delete-remote", headers=auth(org))).status_code == 409


async def test_manual_x_accounts_still_publish_by_hand(org):
    o = org_of(org)
    acc = await make_account(o, "x", "manual:legacy", None, status="manual")
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text"), acc)
    assert "by hand" in e.value.message


async def test_platform_rules_and_connectors(client, org):
    d = (await client.get("/social/platforms", headers=auth(org))).json()
    conn = {c["id"]: c for c in d["connectors"]}
    assert conn["x"]["method"] == "oauth" and conn["x"]["app_configured"] is True
    assert conn["tiktok"]["redirect_uri"].endswith("/social/oauth/tiktok/callback")
    assert "Only me" in conn["tiktok"]["guide"] and "optional" in conn["x"]["guide"].lower()
    plats = {p["id"]: p for p in d["platforms"]}
    assert plats["tiktok"]["max_chars"] == 2200 and "video" in plats["tiktok"]["post_types"]
    assert "text" not in plats["tiktok"]["post_types"]
    assert plats["x"]["max_images"] == 4 and plats["x"]["connector"] == "x"


# ============================================================================ TikTok
async def test_tiktok_connect_hex_pkce_and_private_default(client, org, mock):
    url, q = await start(client, org, "/social/connect/tiktok")
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/") and q["client_key"] == ["tt-key"]
    state = q["state"][0]
    async with platform_session() as s:
        verifier = (await s.get(OAuthState, state)).data["code_verifier"]
    assert q["code_challenge"] == [tiktok.pkce_challenge(verifier)] and re.fullmatch(r"[0-9a-f]{64}", q["code_challenge"][0])

    def token(req):
        f = form(req)
        assert f["client_key"] == "tt-key" and f["code_verifier"] == verifier
        return httpx.Response(200, json={"access_token": "tt-at", "refresh_token": "tt-rt", "expires_in": 86400,
                                         "refresh_expires_in": 31536000, "open_id": "oid-1",
                                         "scope": "user.info.basic,video.publish,video.upload"})

    mock.on("POST", r"open\.tiktokapis\.com/v2/oauth/token/$", token)
    mock.on("GET", r"/v2/user/info/$", (200, {"data": {"user": {"open_id": "oid-1", "display_name": "HRP AI"}},
                                              "error": {"code": "ok"}}))
    mock.on("POST", r"/creator_info/query/$", (200, {"data": {
        "creator_username": "hrpai", "creator_nickname": "HRP AI", "max_video_post_duration_sec": 600,
        "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}, "error": {"code": "ok"}}))
    r = await client.get(f"/social/oauth/tiktok/callback?code=tt-code&state={state}", follow_redirects=False)
    assert "connected=tiktok" in r.headers["location"], r.headers["location"]
    async with SessionLocal() as s:
        set_tenant(s, org_of(org))
        acc = await s.scalar(select(SocialAccount).where(SocialAccount.platform == "tiktok"))
        assert acc.handle == "@hrpai" and acc.settings["privacy_level"] == "SELF_ONLY" and acc.settings["audited"] is False
        assert "Only me" in acc.status_message


def _tt_ok(data):
    return (200, {"data": data, "error": {"code": "ok", "message": ""}})


async def test_tiktok_video_file_upload_posts_privately_when_unaudited(org, mock):
    o = org_of(org)
    acc = await make_account(o, "tiktok", "oid-v", {"access_token": "at", "refresh_token": "rt"},
                             {"audited": False, "privacy_level": "PUBLIC_TO_EVERYONE"},
                             expires_at=datetime.now(UTC) + timedelta(hours=5))
    vid = await make_media(o, kind="video", mime="video/mp4", data=b"\x00\x00\x00\x18ftypmp42-fake-video")
    post = await make_post(o, acc, "video", [vid], text="Watch this", hashtags=["HR"])
    mock.on("POST", r"/creator_info/query/$", _tt_ok({"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                                                      "max_video_post_duration_sec": 300, "creator_username": "hrpai"}))
    mock.on("POST", r"/post/publish/video/init/$", _tt_ok({"publish_id": "pub-1", "upload_url": "https://upload.tiktok.test/u1"}))
    mock.on("PUT", r"upload\.tiktok\.test/u1$", (201, {}))
    polls = {"n": 0}

    def status(req):
        polls["n"] += 1
        st = "PROCESSING_UPLOAD" if polls["n"] == 1 else "PUBLISH_COMPLETE"
        return httpx.Response(200, json={"data": {"status": st}, "error": {"code": "ok"}})

    mock.on("POST", r"/post/publish/status/fetch/$", status)
    ext, url = await do_publish(o, post, acc)
    assert ext == "pub-1"  # private posts have no public id
    init = json.loads(mock.find("POST", r"/video/init/$")[0].content)
    assert init["post_info"]["privacy_level"] == "SELF_ONLY"
    assert init["post_info"]["title"].startswith("Watch this") and "#HR" in init["post_info"]["title"]
    size = len(b"\x00\x00\x00\x18ftypmp42-fake-video")
    assert init["source_info"] == {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}
    put = mock.find("PUT", r"u1$")[0]
    assert put.headers["Content-Range"] == f"bytes 0-{size - 1}/{size}"


async def test_tiktok_photo_post_and_friendly_errors(org, mock, public_media):
    o = org_of(org)
    acc = await make_account(o, "tiktok", "oid-p", {"access_token": "at", "refresh_token": "rt"},
                             {"audited": True, "privacy_level": "FOLLOWER_OF_CREATOR"},
                             expires_at=datetime.now(UTC) + timedelta(hours=5), name="TT")
    a1, a2 = await make_media(o), await make_media(o)
    post = await make_post(o, acc, "carousel", [a1, a2], title="Five tips", text="Swipe for tips")
    mock.on("POST", r"/creator_info/query/$", _tt_ok({"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, post, acc)
    assert "doesn't offer" in e.value.message  # followers-only isn't offered right now
    mock.on("POST", r"/creator_info/query/$", _tt_ok({"privacy_level_options": ["FOLLOWER_OF_CREATOR", "SELF_ONLY"]}))
    mock.on("POST", r"/post/publish/content/init/$", _tt_ok({"publish_id": "pub-photo"}))
    mock.on("POST", r"/post/publish/status/fetch/$", _tt_ok({"status": "PUBLISH_COMPLETE",
                                                              "publicaly_available_post_id": [7311]}))
    post2 = await make_post(o, acc, "carousel", [a1, a2], title="Five tips", text="Swipe for tips")
    async with SessionLocal() as s:
        set_tenant(s, o)
        a = await s.get(SocialAccount, acc.id)
        a.handle = "@hrpai"
        await s.commit()
    acc.handle = "@hrpai"
    ext, url = await do_publish(o, post2, acc)
    assert (ext, url) == ("7311", "https://www.tiktok.com/@hrpai/photo/7311")
    body = json.loads(mock.find("POST", r"/content/init/$")[-1].content)
    assert body["media_type"] == "PHOTO" and body["post_mode"] == "DIRECT_POST"
    assert body["source_info"]["photo_images"][0].endswith(".jpeg")  # JPEG copies of PNGs
    assert body["post_info"]["privacy_level"] == "FOLLOWER_OF_CREATOR" and body["post_info"]["title"] == "Five tips"
    mock.on("POST", r"/post/publish/content/init/$", (403, {"data": {}, "error": {
        "code": "unaudited_client_can_only_post_to_private_accounts", "message": "x"}}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "carousel", [a1]), acc)
    assert "Only me" in e.value.message and not e.value.reconnect
    with pytest.raises(PublishError) as e:  # text-only isn't a TikTok post type
        await do_publish(o, await make_post(o, acc, "text"), acc)
    assert "doesn't support" in e.value.message


# ============================================================================ Pinterest
async def test_pinterest_webp_sent_as_jpeg_and_delete(client, org, mock):
    o = org_of(org)
    acc = await make_account(o, "pinterest", "pin-n", {"access_token": "pin-tok"}, {"board_id": "b1"})
    webp = await make_media(o, mime="image/webp", data=png_bytes(fmt="WEBP"))
    post = await make_post(o, acc, "image", [webp], title="Tips")
    mock.on("POST", r"api\.pinterest\.com/v5/pins$", (201, {"id": "77"}))
    ext, _ = await do_publish(o, post, acc)
    body = json.loads(mock.find("POST", r"/v5/pins$")[-1].content)
    assert ext == "77" and body["media_source"]["content_type"] == "image/jpeg"
    import base64
    assert base64.b64decode(body["media_source"]["data"])[:3] == b"\xff\xd8\xff"
    async with SessionLocal() as s:
        set_tenant(s, o)
        p = await s.get(Post, post.id)
        p.status, p.external_id = "published", "77"
        await s.commit()
    mock.on("DELETE", r"/v5/pins/77$", (204, None))
    r = await client.post(f"/social/posts/{post.id}/delete-remote", headers=auth(org))
    assert r.status_code == 200 and r.json()["status"] == "removed"


# ============================================================================ Canva
async def test_canva_connect_browse_and_import(client, org, mock):
    import app.modules.networks  # noqa: F401 - registers canva.import
    from app.services.jobs import run_inline

    st = (await client.get("/canva/status", headers=auth(org))).json()
    assert st["configured"] is True and st["connected"] is False and "Connect API" in st["guide"]
    url, q = await start(client, org, "/canva/connect", {"return_to": "/media"})
    assert url.startswith("https://www.canva.com/api/oauth/authorize") and q["code_challenge_method"] == ["s256"]
    assert "design:content:read" in q["scope"][0].split()
    mock.on("POST", r"api\.canva\.com/rest/v1/oauth/token$", (200, {"access_token": "cv-at", "refresh_token": "cv-rt",
                                                                     "expires_in": 14400}))
    mock.on("GET", r"/v1/users/me/profile$", (200, {"profile": {"display_name": "Prashant"}}))
    mock.on("GET", r"/v1/users/me$", (200, {"team_user": {"user_id": "u1", "team_id": "t1"}}))
    r = await client.get(f"/social/oauth/canva/callback?code=c&state={q['state'][0]}", follow_redirects=False)
    loc = r.headers["location"]
    assert urlsplit(loc).path == "/media" and "connected=canva" in loc, loc
    assert (await client.get("/canva/status", headers=auth(org))).json()["display_name"] == "Prashant"

    mock.on("GET", r"/v1/designs$", (200, {"items": [{"id": "DAF1", "title": "Diwali post", "page_count": 2,
                                                      "thumbnail": {"url": "https://canva.test/t.png", "width": 1080, "height": 1350}}],
                                           "continuation": "next"}))
    d = (await client.get("/canva/designs?query=diwali", headers=auth(org))).json()
    assert d["items"][0]["id"] == "DAF1" and d["continuation"] == "next"
    assert query(mock.find("GET", r"/v1/designs$")[-1])["query"] == "diwali"

    mock.on("GET", r"/v1/designs/DAF1$", (200, {"design": {"id": "DAF1", "title": "Diwali post", "page_count": 2}}))
    mock.on("POST", r"/v1/exports$", (200, {"job": {"id": "ex1", "status": "in_progress"}}))
    mock.on("GET", r"/v1/exports/ex1$", (200, {"job": {"id": "ex1", "status": "success",
                                                       "urls": ["https://export.canva.test/p1.png", "https://export.canva.test/p2.png"]}}))
    mock.on("GET", r"export\.canva\.test/p\d\.png$", lambda req: httpx.Response(200, content=png_bytes()))
    r = await client.post("/canva/import", headers=auth(org), json={"design_id": "DAF1", "format": "png", "pages": [1, 2]})
    assert r.status_code == 202
    out = await run_inline("canva.import", org_of(org), {"design_id": "DAF1", "format": "png", "pages": [1, 2]})
    assert out["count"] == 2
    exp = json.loads(mock.find("POST", r"/v1/exports$")[0].content)
    assert exp == {"design_id": "DAF1", "format": {"type": "png", "pages": [1, 2]}}
    async with SessionLocal() as s:
        set_tenant(s, org_of(org))
        a = await s.get(MediaAsset, uuid.UUID(out["asset_ids"][1]))
        assert a.source == "canva" and a.kind == "image" and a.meta["canva_page"] == 2 and a.width == 40
        assert await s.scalar(select(DesignConnection).where(DesignConnection.provider == "canva")) is not None
    # A failed export is explained.
    mock.on("GET", r"/v1/exports/ex1$", (200, {"job": {"id": "ex1", "status": "failed", "error": {"message": "Design too big"}}}))
    with pytest.raises(Exception) as e:
        await run_inline("canva.import", org_of(org), {"design_id": "DAF1", "format": "png"})
    assert "Design too big" in str(e.value)
    assert (await client.delete("/canva/connection", headers=auth(org))).status_code == 204
    assert (await client.post("/canva/import", headers=auth(org), json={"design_id": "DAF1"})).status_code == 409


# ============================================================================ link in bio
async def test_link_in_bio_editor_public_page_clicks_and_tunnel(client, org):
    o = org_of(org)
    r = await client.get("/linkinbio", headers=auth(org))
    assert r.status_code == 200 and r.json()["exists"] is False and r.json()["slug"]
    r = await client.patch("/organization", headers=auth(org), json={"social_links": {
        "linkedin": "https://www.linkedin.com/company/hrpai", "x": "@hrpai"}})
    assert r.status_code == 200, r.text
    body = {"slug": "hrp-ai-links", "published": False, "title": "HRP <AI>", "tagline": "HR software & AI",
            "show_posts": True, "links": [
                {"title": "Book a demo", "url": "https://hrpai.in/demo", "highlight": True},
                {"title": "Bad", "url": "javascript:alert(1)"}]}
    r = await client.put("/linkinbio", headers=auth(org), json=body)
    assert r.status_code == 400 and "Bad" in r.json()["detail"]
    body["links"] = body["links"][:1] + [{"title": "Email us", "url": "mailto:hello@hrpai.in", "icon": "✉"}]
    r = await client.put("/linkinbio", headers=auth(org), json=body)
    assert r.status_code == 200, r.text
    page = r.json()
    link_id = page["links"][0]["id"]
    assert page["public_url"].endswith("/l/hrp-ai-links")

    prev = (await client.post("/linkinbio/preview", headers=auth(org), json=body)).json()["html"]
    assert "HRP &lt;AI&gt;" in prev and "<AI>" not in prev and "Book a demo" in prev

    # Not published yet: hidden.
    assert (await client.get(f"{ROOT}/l/hrp-ai-links", headers=BROWSER)).status_code == 404
    body["published"], body["links"] = True, page["links"]  # the editor sends the ids back
    assert (await client.put("/linkinbio", headers=auth(org), json=body)).status_code == 200

    # A published post with an image appears in the grid.
    acc = await make_account(o, "linkedin_org", "urn:li:organization:lib", {"access_token": "t"})
    img = await make_media(o, data=png_bytes())
    post = await make_post(o, acc, "image", [img])
    async with SessionLocal() as s:
        set_tenant(s, o)
        p = await s.get(Post, post.id)
        p.status, p.external_url, p.published_at = "published", "https://www.linkedin.com/feed/update/1", datetime.now(UTC)
        await s.commit()

    r = await client.get(f"{ROOT}/l/hrp-ai-links", headers=BROWSER)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    html = r.text
    assert "<script" not in html and "HRP &lt;AI&gt;" in html
    assert f"/l/hrp-ai-links/go/{link_id}" in html and f"/l/hrp-ai-links/go/{post.id}" in html
    assert f"/l/hrp-ai-links/img/{img.id}" in html
    assert "/l/hrp-ai-links/go/s-linkedin" in html and "/l/hrp-ai-links/go/s-x" in html
    r = await client.get(f"{ROOT}/l/hrp-ai-links/go/s-x", headers=BROWSER, follow_redirects=False)
    assert r.headers["location"] == "https://x.com/hrpai"

    r = await client.get(f"{ROOT}/l/hrp-ai-links/go/{link_id}", headers=BROWSER, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://hrpai.in/demo"
    r = await client.get(f"{ROOT}/l/hrp-ai-links/go/{post.id}", headers=BROWSER, follow_redirects=False)
    assert r.headers["location"] == "https://www.linkedin.com/feed/update/1"
    assert (await client.get(f"{ROOT}/l/hrp-ai-links/go/{uuid.uuid4()}", headers=BROWSER)).status_code == 404
    # Link-preview bots aren't counted.
    await client.get(f"{ROOT}/l/hrp-ai-links/go/{link_id}", headers={"user-agent": "facebookexternalhit/1.1"},
                     follow_redirects=False)
    img_r = await client.get(f"{ROOT}/l/hrp-ai-links/img/{img.id}")
    assert img_r.status_code == 200 and img_r.headers["content-type"] == "image/png"
    other = await make_media(o, data=png_bytes((200, 0, 0)))
    assert (await client.get(f"{ROOT}/l/hrp-ai-links/img/{other.id}")).status_code == 404  # not on the page

    s = (await client.get("/linkinbio/stats?days=7", headers=auth(org))).json()
    assert s["views"] == 1 and s["clicks"] == 3 and s["click_rate"] == 300.0
    top = {t["target"]: t for t in s["by_target"]}
    assert top[link_id]["clicks"] == 1 and top[link_id]["label"] == "Book a demo" and top[str(post.id)]["kind"] == "post"
    async with SessionLocal() as sess:
        set_tenant(sess, o)
        cols = set(LinkPageStat.__table__.columns.keys())
        assert cols == {"id", "organization_id", "page_id", "day", "target", "count"}  # nothing about visitors

    # Tunnel: the page, its redirects and images are reachable (GET only); nothing else under /l is.
    assert (await client.get(f"{ROOT}/l/hrp-ai-links", headers={**TUNNEL, **BROWSER})).status_code == 200
    r = await client.get(f"{ROOT}/l/hrp-ai-links/go/{link_id}", headers=TUNNEL, follow_redirects=False)
    assert r.status_code == 302
    assert (await client.get(f"{ROOT}/l/hrp-ai-links/img/{img.id}", headers=TUNNEL)).status_code == 200
    assert (await client.post(f"{ROOT}/l/hrp-ai-links", headers=TUNNEL)).status_code == 404
    assert (await client.get(f"{ROOT}/l/hrp-ai-links/admin", headers=TUNNEL)).status_code == 404
    assert (await client.get(f"{ROOT}/l/HRP_AI", headers=TUNNEL)).status_code == 404
    assert (await client.get("/linkinbio", headers={**auth(org), **TUNNEL})).status_code == 404

    # Leave no published post behind (other modules' analytics tests count this organization's posts).
    async with SessionLocal() as sess:
        set_tenant(sess, o)
        await sess.delete(await sess.get(Post, post.id))
        await sess.commit()


async def test_link_in_bio_slug_is_unique_and_needs_permission(client, org):
    r = await client.post("/platform/organizations", headers=auth(org),
                          json={"name": "Links Acme", "admin_email": "owner@links-acme.in", "admin_name": "Acme Owner"})
    assert r.status_code == 201, r.text
    acme = await login(client, "owner@links-acme.in", r.json()["temporary_password"])
    await client.put("/linkinbio", headers=auth(org), json={"slug": "taken-slug", "links": []})
    r = await client.put("/linkinbio", headers=auth(acme), json={"slug": "taken-slug", "links": []})
    assert r.status_code == 409
    r = await client.put("/linkinbio", headers=auth(acme), json={"slug": "-bad-", "links": []})
    assert r.status_code == 400
    # Acme can't see HRP's page or stats.
    assert (await client.get("/linkinbio", headers=auth(acme))).json()["exists"] is False
    roles = {x["name"]: x["id"] for x in (await client.get("/roles", headers=auth(org))).json()}
    r = await client.post("/users", headers=auth(org), json={
        "email": "lib-viewer@hrpai.in", "full_name": "Lib Viewer", "role_ids": [roles["Viewer"]]})
    v = await login(client, "lib-viewer@hrpai.in", r.json()["temporary_password"])
    assert (await client.get("/linkinbio", headers=auth(v))).status_code == 403
    assert (await client.put("/linkinbio", headers=auth(v), json={"slug": "x-y", "links": []})).status_code == 403
