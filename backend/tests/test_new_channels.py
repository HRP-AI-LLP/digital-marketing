"""New channels for India: Telegram channels (Bot API), Discord channels (webhooks), Reddit (OAuth), WhatsApp channels
(post by hand with a wa.me share link), and TikTok switched off (banned in India). No real network: adapters' HTTP
client is replaced with httpx.MockTransport."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant
from app.core.logsafe import redact
from app.models import Post, SocialAccount
from app.services import publishing
from app.services.publishing import PublishError, account_tokens
from app.services.social import discord, manual, reddit, telegram
from tests.conftest import auth, login
from tests.test_publishing import (  # noqa: F401 - fixtures
    do_publish, form, make_account, make_media, make_post, mock, org_of, public_media, query,
)

ADMIN_PW = "Str0ngPassw0rd!"
BOT = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
HOOK = "https://discord.com/api/webhooks/112233445566778899/Abc-DEF_ghiJKLmnoPQRstuVWXyz0123456789"


@pytest.fixture(scope="module", autouse=True)
def temp_storage(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage-channels"))
    yield
    s.storage_dir = old


@pytest.fixture(scope="module")
async def org(client, admin):
    t = await login(client, "admin@hrpai.in", ADMIN_PW)
    existing = {p["provider"] for p in (await client.get("/integrations/providers", headers=auth(t))).json()}
    if "reddit_app" not in existing:
        r = await client.post("/integrations/providers", headers=auth(t), json={
            "provider": "reddit_app",
            "secrets": {"client_id": "rd-id", "client_secret": "rd-secret", "developer_username": "hrpdev"}})
        assert r.status_code == 201, r.text
    return t


def body_json(req: httpx.Request) -> dict:
    return json.loads(req.content or b"{}")


def tg_ok(result):
    return 200, {"ok": True, "result": result}


def tg_channel(mock, *, member=None, username="hrpai_updates"):
    mock.on("POST", r"/bot\d+:[^/]+/getMe$", tg_ok({"id": 999, "is_bot": True, "username": "hrp_studio_bot"}))
    mock.on("POST", r"/getChat$", tg_ok({"id": -1001234567890, "type": "channel", "title": "HRP AI Updates",
                                         "username": username}))
    mock.on("POST", r"/getChatMember$", tg_ok(member or {"status": "administrator", "can_post_messages": True,
                                                          "user": {"id": 999}}))


# ============================================================================ TikTok switched off
async def test_tiktok_is_not_offered_anywhere(client, org):
    d = (await client.get("/social/platforms", headers=auth(org))).json()
    assert "tiktok" not in {c["id"] for c in d["connectors"]}
    assert "tiktok" not in {p["id"] for p in d["platforms"]}
    assert {"telegram", "discord", "reddit", "whatsapp"} <= {c["id"] for c in d["connectors"]}
    conn = {c["id"]: c for c in d["connectors"]}
    assert conn["telegram"]["method"] == "bot_token" and "BotFather" in conn["telegram"]["guide"]
    assert conn["discord"]["method"] == "webhook" and "Copy webhook URL" in conn["discord"]["guide"]
    assert conn["reddit"]["method"] == "oauth" and conn["reddit"]["redirect_uri"].endswith("/social/oauth/reddit/callback")
    assert conn["whatsapp"]["method"] == "manual"

    r = await client.post("/social/connect/tiktok", headers=auth(org))
    assert r.status_code == 400 and "India" in r.json()["detail"]

    cat = {p["id"]: p for p in (await client.get("/integrations/catalog", headers=auth(org))).json()["providers"]}
    assert cat["tiktok_app"]["offered"] is False and cat["reddit_app"]["offered"] is True

    media = (await client.get("/media/templates", headers=auth(org))).json()
    assert "tiktok" not in {p["id"] for p in media["platforms"]}
    links = (await client.get("/links/settings", headers=auth(org))).json()
    assert "tiktok" not in {p["id"] for p in links["platforms"]}
    meta = {p["id"]: p for p in (await client.get("/content/meta", headers=auth(org))).json()["platforms"]}
    assert meta["tiktok"]["offered"] is False and "India" in meta["tiktok"]["unavailable_note"]
    assert meta["telegram"]["offered"] is True and meta["whatsapp"]["manual"] is True


async def test_existing_tiktok_account_keeps_a_note(client, org):
    o = org_of(org)
    await make_account(o, "tiktok", "oid-legacy", {"access_token": "at"}, name="Old TikTok")
    rows = (await client.get("/social/accounts", headers=auth(org))).json()
    tt = next(a for a in rows if a["platform"] == "tiktok")
    assert "India" in tt["platform_note"]
    assert all(a["platform_note"] is None for a in rows if a["platform"] != "tiktok")


async def test_presence_and_linkinbio_drop_tiktok():
    from app.services import linkinbio, presence_profiles

    assert presence_profiles.IMPORTANCE["tiktok"] == 0.0
    assert "tiktok" not in linkinbio.SOCIAL_ORDER and "telegram" in linkinbio.SOCIAL_ORDER
    assert linkinbio.social_url("telegram", "@hrpai_updates") == "https://t.me/hrpai_updates"
    assert "tiktok" not in get_settings().oauth_https_providers.split(",")


async def test_secrets_in_urls_are_redacted():
    line = f"POST https://api.telegram.org/bot{BOT}/sendMessage then {HOOK}?wait=true"
    out = redact(line)
    assert BOT.split(":")[1] not in out and HOOK.rsplit("/", 1)[1] not in out
    assert "/bot[redacted]/sendMessage" in out and "/webhooks/112233445566778899/[redacted]" in out


# ============================================================================ Telegram
async def test_telegram_connect_checks_bot_is_admin(client, org, mock):
    r = await client.post("/social/accounts/telegram", headers=auth(org), json={"bot_token": "not-a-token-at-all-xx", "channel": "@x"})
    assert r.status_code == 400 and "BotFather" in r.json()["detail"]

    tg_channel(mock, member={"status": "member", "user": {"id": 999}})
    r = await client.post("/social/accounts/telegram", headers=auth(org), json={"bot_token": BOT, "channel": "@hrpai_updates"})
    assert r.status_code == 400 and "administrator" in r.json()["detail"]

    tg_channel(mock, member={"status": "administrator", "can_post_messages": False})
    r = await client.post("/social/accounts/telegram", headers=auth(org), json={"bot_token": BOT, "channel": "@hrpai_updates"})
    assert r.status_code == 400 and "Post messages" in r.json()["detail"]

    tg_channel(mock)
    r = await client.post("/social/accounts/telegram", headers=auth(org),
                          json={"bot_token": BOT, "channel": "https://t.me/hrpai_updates"})
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["platform"] == "telegram" and acc["handle"] == "@hrpai_updates" and acc["name"] == "HRP AI Updates"
    assert BOT not in r.text and BOT.split(":")[1] not in r.text
    assert body_json(mock.find("POST", r"/getChat$")[-1])["chat_id"] == "@hrpai_updates"
    assert body_json(mock.find("POST", r"/getChatMember$")[-1]) == {"chat_id": -1001234567890, "user_id": 999}
    async with SessionLocal() as s:
        set_tenant(s, org_of(org))
        a = await s.scalar(select(SocialAccount).where(SocialAccount.platform == "telegram"))
        assert account_tokens(a) == {"bot_token": BOT}
        assert BOT not in str(a.tokens_enc or "")


async def test_telegram_channel_formats():
    assert telegram.normalize_chat("@hrpai_updates") == "@hrpai_updates"
    assert telegram.normalize_chat("t.me/hrpai_updates") == "@hrpai_updates"
    assert telegram.normalize_chat("-1001234567890") == "-1001234567890"
    with pytest.raises(PublishError):
        telegram.normalize_chat("bad name!")
    assert telegram.escape("Tom & Jerry <b>hi</b>") == "Tom &amp; Jerry &lt;b&gt;hi&lt;/b&gt;"


async def _tg_account(o, username="hrpai_updates"):
    return await make_account(o, "telegram", f"-100{abs(hash(username)) % 10**10}", {"bot_token": BOT},
                              {"chat_id": "-1001234567890", "username": username, "chat_type": "channel"})


async def test_telegram_text_post_is_escaped_with_link_preview(org, mock):
    o = org_of(org)
    acc = await _tg_account(o)
    mock.on("POST", r"/sendMessage$", tg_ok({"message_id": 41}))
    post = await make_post(o, acc, "link", text="Tom & Jerry <script> are here", hashtags=["HRTech"],
                           link_url="https://hrpai.in/blog")
    ext, url = await do_publish(o, post, acc)
    assert (ext, url) == ("-1001234567890/41", "https://t.me/hrpai_updates/41")
    b = body_json(mock.find("POST", r"/sendMessage$")[-1])
    assert b["parse_mode"] == "HTML" and b["chat_id"] == "-1001234567890"
    assert b["text"].startswith("Tom &amp; Jerry &lt;script&gt; are here") and "#HRTech" in b["text"]
    assert "https://hrpai.in/blog" in b["text"]
    assert b["link_preview_options"] == {"is_disabled": False, "url": "https://hrpai.in/blog"}
    assert await telegram.fetch_metrics(None, post, acc) is None


async def test_telegram_photo_album_and_caption_overflow(org, mock):
    o = org_of(org)
    acc = await _tg_account(o, "hrpai_photos")
    mock.on("POST", r"/sendPhoto$", tg_ok({"message_id": 50}))
    mock.on("POST", r"/sendMediaGroup$", tg_ok([{"message_id": 60}, {"message_id": 61}, {"message_id": 62}]))
    mock.on("POST", r"/sendMessage$", tg_ok({"message_id": 51}))

    img = await make_media(o)
    post = await make_post(o, acc, "image", [img], text="New feature: <AI> & you")
    ext, url = await do_publish(o, post, acc)
    assert ext == "-1001234567890/50" and url == "https://t.me/hrpai_photos/50"
    req = mock.find("POST", r"/sendPhoto$")[-1]
    assert b'name="photo"' in req.content and b'name="caption"' in req.content
    assert b"New feature: &lt;AI&gt; &amp; you" in req.content and b"HTML" in req.content
    assert not mock.find("POST", r"/sendMessage$")

    imgs = [await make_media(o) for _ in range(3)]
    album = await make_post(o, acc, "carousel", imgs, text="Three slides")
    ext, _ = await do_publish(o, album, acc)
    assert ext == "-1001234567890/60,61,62"
    req = mock.find("POST", r"/sendMediaGroup$")[-1]
    raw = req.content.decode("latin-1")
    media_json = json.loads(raw.split('name="media"', 1)[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
    assert [m["media"] for m in media_json] == ["attach://file0", "attach://file1", "attach://file2"]
    assert media_json[0]["caption"] == "Three slides" and "caption" not in media_json[1]
    assert raw.count('name="file') == 3

    long = "A" * 1500
    over = await make_post(o, acc, "image", [await make_media(o)], text=long)
    ext, _ = await do_publish(o, over, acc)
    assert ext == "-1001234567890/50,51"  # photo first, then the full text as its own message
    assert b'name="caption"' not in mock.find("POST", r"/sendPhoto$")[-1].content
    assert body_json(mock.find("POST", r"/sendMessage$")[-1])["text"] == long


async def test_telegram_errors_and_delete(org, mock):
    o = org_of(org)
    acc = await _tg_account(o, "hrpai_err")
    mock.on("POST", r"/sendMessage$", (403, {"ok": False, "error_code": 403,
                                             "description": "Forbidden: bot is not a member of the channel chat"}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text"), acc)
    assert e.value.reconnect and "administrator" in e.value.message
    mock.on("POST", r"/sendMessage$", (429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 7}}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text"), acc)
    assert e.value.retryable and "7 s" in e.value.message
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", text="x" * 4100), acc)
    assert "4096" in e.value.message

    mock.on("POST", r"/deleteMessages$", tg_ok(True))
    post = await make_post(o, acc, "text", external_id="-1001234567890/70,71")
    async with SessionLocal() as s:
        set_tenant(s, o)
        await publishing.delete_remote(s, await s.get(Post, post.id), await s.get(SocialAccount, acc.id))
    assert body_json(mock.find("POST", r"/deleteMessages$")[-1]) == {"chat_id": "-1001234567890", "message_ids": [70, 71]}


# ============================================================================ Discord
async def test_discord_webhook_validation_and_connect(client, org, mock):
    for bad in ("https://example.com/api/webhooks/1/abc", "http://discord.com/api/webhooks/123456/abcdefghijklmnopqrstuvwxyz",
                "https://discord.com/channels/1/2"):
        r = await client.post("/social/accounts/discord", headers=auth(org), json={"webhook_url": bad + "x" * 10})
        assert r.status_code in (400, 422), bad
    assert discord.normalize_webhook(HOOK.replace("discord.com", "discordapp.com"))[0] == HOOK

    mock.on("GET", r"/api/webhooks/112233445566778899/", (404, {"message": "Unknown Webhook", "code": 10015}))
    r = await client.post("/social/accounts/discord", headers=auth(org), json={"webhook_url": HOOK})
    assert r.status_code == 400 and "new webhook" in r.json()["detail"]

    mock.on("GET", r"/api/webhooks/112233445566778899/", (200, {
        "id": "112233445566778899", "type": 1, "name": "HRP AI", "channel_id": "222", "guild_id": "111",
        "avatar": "abc"}))
    r = await client.post("/social/accounts/discord", headers=auth(org), json={"webhook_url": HOOK})
    assert r.status_code == 201, r.text
    acc = r.json()
    assert acc["platform"] == "discord" and acc["profile_url"] == "https://discord.com/channels/111/222"
    assert HOOK.rsplit("/", 1)[1] not in r.text
    assert acc["can_delete_posts"] is True


async def _dc_account(o, ext="dc-1", **settings):
    return await make_account(o, "discord", ext, {"webhook_url": HOOK},
                              {"guild_id": "111", "channel_id": "222", "max_file_mb": 25, **settings})


async def test_discord_text_link_embed_and_files(org, mock):
    o = org_of(org)
    acc = await _dc_account(o)
    mock.on("POST", r"/api/webhooks/112233445566778899/", (200, {"id": "900", "channel_id": "222"}))

    ext, url = await do_publish(o, await make_post(o, acc, "text", text="Hello @everyone"), acc)
    assert (ext, url) == ("900", "https://discord.com/channels/111/222/900")
    req = mock.find("POST", r"/api/webhooks/")[-1]
    assert query(req) == {"wait": "true"}
    b = body_json(req)
    assert b["content"] == "Hello @everyone" and b["allowed_mentions"] == {"parse": []}

    await do_publish(o, await make_post(o, acc, "link", text="Read our guide", link_url="https://hrpai.in/guide",
                                        title="Payroll guide", meta={"link_description": "All you need"}), acc)
    b = body_json(mock.find("POST", r"/api/webhooks/")[-1])
    assert b["embeds"] == [{"url": "https://hrpai.in/guide", "title": "Payroll guide", "description": "All you need"}]
    assert "https://hrpai.in/guide" not in b["content"]

    imgs = [await make_media(o, alt_text="Chart"), await make_media(o)]
    await do_publish(o, await make_post(o, acc, "carousel", imgs, text="Two images"), acc)
    req = mock.find("POST", r"/api/webhooks/")[-1]
    raw = req.content.decode("latin-1")
    assert 'name="files[0]"' in raw and 'name="files[1]"' in raw and 'name="payload_json"' in raw
    payload = json.loads(raw.split('name="payload_json"', 1)[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0])
    assert payload["content"] == "Two images" and payload["attachments"][0]["description"] == "Chart"

    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", text="y" * 2001), acc)
    assert "2000" in e.value.message


async def test_discord_file_limit_and_delete(org, mock):
    o = org_of(org)
    acc = await _dc_account(o, "dc-2", max_file_mb=1)
    big = await make_media(o, kind="video", mime="video/mp4", data=b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(PublishError) as e:  # not a real video: it can't be re-encoded, so it's refused clearly
        await do_publish(o, await make_post(o, acc, "video", [big], text="Big"), acc)
    assert "2.0 MB" in e.value.message

    mock.on("DELETE", r"/api/webhooks/112233445566778899/.+/messages/900$", lambda req: httpx.Response(204))
    post = await make_post(o, acc, "text", external_id="900")
    async with SessionLocal() as s:
        set_tenant(s, o)
        await publishing.delete_remote(s, await s.get(Post, post.id), await s.get(SocialAccount, acc.id))
    assert mock.find("DELETE", r"/messages/900$")

    mock.on("POST", r"/api/webhooks/", (404, {"message": "Unknown Webhook", "code": 10015}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text"), acc)
    assert e.value.reconnect


async def test_discord_shrinks_big_videos_and_learns_the_real_limit(org, mock, tmp_path):
    from app.services import media_render

    o = org_of(org)
    src = tmp_path / "noisy.mp4"
    await media_render.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4", "-vf", "noise=alls=50:allf=t",
                                   "-c:v", "libx264", "-b:v", "6000k", "-pix_fmt", "yuv420p", str(src)], timeout_s=120)
    data = src.read_bytes()
    assert len(data) > 1024 * 1024
    acc = await _dc_account(o, "dc-3", max_file_mb=1)
    mock.on("POST", r"/api/webhooks/112233445566778899/", (200, {"id": "901", "channel_id": "222"}))
    vid = await make_media(o, kind="video", mime="video/mp4", data=data)
    ext, _ = await do_publish(o, await make_post(o, acc, "video", [vid], text="Demo"), acc)
    assert ext == "901"
    sent = mock.find("POST", r"/api/webhooks/")[-1].content
    assert len(sent) < 1024 * 1024  # re-encoded to fit (the multipart body includes the file)

    # A server that refuses the file (no boosts: 10 MB) is retried once at 10 MB and remembered.
    acc2 = await _dc_account(o, "dc-4")  # account setting says 25 MB
    replies = iter([httpx.Response(413, json={"message": "Request entity too large", "code": 40005}),
                    httpx.Response(200, json={"id": "902", "channel_id": "222"})])
    mock.on("POST", r"/api/webhooks/112233445566778899/", lambda req: next(replies))
    ext, _ = await do_publish(o, await make_post(o, acc2, "video", [vid], text="Demo 2"), acc2)
    assert ext == "902"
    async with SessionLocal() as s:
        set_tenant(s, o)
        assert (await s.get(SocialAccount, acc2.id)).settings["max_file_mb"] == 10


# ============================================================================ Reddit
async def test_reddit_oauth_connect(client, org, mock):
    r = await client.post("/social/connect/reddit", headers=auth(org), json={})
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://www.reddit.com/api/v1/authorize")
    assert q["duration"] == ["permanent"] and "submit" in q["scope"][0] and q["client_id"] == ["rd-id"]
    state = q["state"][0]

    def token(req):
        assert req.headers["authorization"].startswith("Basic ")
        assert req.headers["user-agent"] == "web:hrp-digital-marketing:v1.0 (by /u/hrpdev)"
        f = form(req)
        assert f["grant_type"] == "authorization_code" and f["code"] == "rd-code"
        return httpx.Response(200, json={"access_token": "rd-at", "refresh_token": "rd-rt", "expires_in": 86400,
                                         "scope": "identity submit read edit flair"})

    mock.on("POST", r"reddit\.com/api/v1/access_token$", token)
    mock.on("GET", r"oauth\.reddit\.com/api/v1/me$", (200, {"id": "abc12", "name": "hrp_ai",
                                                             "icon_img": "https://styles.redditmedia.com/i.png?x=1"}))
    r = await client.get(f"/social/oauth/reddit/callback?code=rd-code&state={state}", follow_redirects=False)
    assert "connected=reddit" in r.headers["location"], r.headers["location"]
    async with SessionLocal() as s:
        set_tenant(s, org_of(org))
        acc = await s.scalar(select(SocialAccount).where(SocialAccount.platform == "reddit"))
        assert acc.handle == "u/hrp_ai" and acc.settings["subreddit"] == "u_hrp_ai"
        assert account_tokens(acc)["refresh_token"] == "rd-rt" and acc.token_expires_at is not None


async def _rd_account(o, ext="rd-1", **settings):
    return await make_account(o, "reddit", ext, {"access_token": "at", "refresh_token": "rt"},
                              {"subreddit": "HRTech", "username": "hrp_ai", **settings},
                              expires_at=datetime.now(UTC) + timedelta(hours=5))


def _submitted(name="t3_xyz", url="https://www.reddit.com/r/HRTech/comments/xyz/payroll/"):
    return 200, {"json": {"errors": [], "data": {"name": name, "id": name[3:], "url": url}}}


async def test_reddit_submit_self_and_link(org, mock):
    o = org_of(org)
    acc = await _rd_account(o, flair_id="fl-1")
    mock.on("POST", r"oauth\.reddit\.com/api/submit$", _submitted())
    ext, url = await do_publish(o, await make_post(o, acc, "text", title="How we cut payroll errors",
                                                   text="Here is what worked.", hashtags=["HR"]), acc)
    assert (ext, url) == ("t3_xyz", "https://www.reddit.com/r/HRTech/comments/xyz/payroll/")
    req = mock.find("POST", r"/api/submit$")[-1]
    f = form(req)
    assert f["kind"] == "self" and f["sr"] == "HRTech" and f["title"] == "How we cut payroll errors"
    assert f["text"] == "Here is what worked." and f["api_type"] == "json" and f["flair_id"] == "fl-1"
    assert req.headers["user-agent"] == "web:hrp-digital-marketing:v1.0 (by /u/hrp_ai)"
    assert req.headers["authorization"] == "Bearer at"

    mock.on("POST", r"oauth\.reddit\.com/api/submit$", _submitted("t3_lnk", "https://hrpai.in/guide"))
    ext, url = await do_publish(o, await make_post(o, acc, "link", title="Payroll guide", text="ignored body",
                                                   link_url="https://hrpai.in/guide"), acc)
    f = form(mock.find("POST", r"/api/submit$")[-1])
    assert f["kind"] == "link" and f["url"] == "https://hrpai.in/guide" and "text" not in f
    assert url == "https://www.reddit.com/r/HRTech/comments/lnk/"


async def test_reddit_image_posts_as_link_to_public_media(org, mock, public_media):
    o = org_of(org)
    acc = await _rd_account(o, "rd-img", subreddit="u_hrp_ai")
    mock.on("POST", r"oauth\.reddit\.com/api/submit$", _submitted("t3_img", "https://cdn.example.com/x.jpeg"))
    img = await make_media(o)
    ext, url = await do_publish(o, await make_post(o, acc, "image", [img], title="Our new dashboard"), acc)
    f = form(mock.find("POST", r"/api/submit$")[-1])
    assert f["kind"] == "link" and f["url"].startswith("https://cdn.example.com/") and f["sr"] == "u_hrp_ai"
    assert url == "https://www.reddit.com/user/hrp_ai/comments/img/"


async def test_reddit_errors_refresh_and_metrics(org, mock):
    o = org_of(org)
    acc = await _rd_account(o, "rd-err")
    mock.on("POST", r"oauth\.reddit\.com/api/submit$",
            (200, {"json": {"errors": [["SUBREDDIT_NOEXIST", "that subreddit doesn't exist", "sr"]]}}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", title="T"), acc)
    assert "doesn't exist" in e.value.message and not e.value.retryable
    mock.on("POST", r"oauth\.reddit\.com/api/submit$",
            (200, {"json": {"errors": [["RATELIMIT", "you are doing that too much. try again in 9 minutes.", "ratelimit"]]}}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", title="T"), acc)
    assert e.value.retryable and "9 minutes" in e.value.message
    mock.on("POST", r"oauth\.reddit\.com/api/submit$", (429, {"message": "Too Many Requests"}, {"x-ratelimit-reset": "40"}))
    with pytest.raises(PublishError) as e:
        await do_publish(o, await make_post(o, acc, "text", title="T"), acc)
    assert e.value.retryable

    expired = await make_account(o, "reddit", "rd-old", {"access_token": "old", "refresh_token": "rt"},
                                 {"subreddit": "HRTech", "username": "hrp_ai"},
                                 expires_at=datetime.now(UTC) - timedelta(minutes=1))

    def refresh(req):
        assert form(req) == {"grant_type": "refresh_token", "refresh_token": "rt"}
        return httpx.Response(200, json={"access_token": "new-at", "expires_in": 86400})

    mock.on("POST", r"reddit\.com/api/v1/access_token$", refresh)
    mock.on("GET", r"oauth\.reddit\.com/api/info$", (200, {"data": {"children": [{"kind": "t3", "data": {
        "score": 42, "num_comments": 7, "num_crossposts": 1, "upvote_ratio": 0.97}}]}}))
    post = await make_post(o, expired, "text", external_id="t3_abc")
    async with SessionLocal() as s:
        set_tenant(s, o)
        a = await s.get(SocialAccount, expired.id)
        m = await publishing.fetch_metrics(s, await s.get(Post, post.id), a)
        assert account_tokens(a)["access_token"] == "new-at" and account_tokens(a)["refresh_token"] == "rt"
    assert (m["likes"], m["comments"], m["shares"]) == (42, 7, 1)
    assert query(mock.find("GET", r"/api/info$")[-1])["id"] == "t3_abc"
    assert mock.find("GET", r"/api/info$")[-1].headers["authorization"] == "Bearer new-at"


async def test_reddit_subreddit_setting_forms():
    acc = SocialAccount(platform="reddit", settings={"subreddit": "r/HRTech"}, handle="u/hrp_ai")
    assert reddit.subreddit(acc) == "HRTech"
    acc.settings = {"subreddit": "u/hrp_ai"}
    assert reddit.subreddit(acc) == "u_hrp_ai"
    acc.settings = {"username": "hrp_ai"}
    assert reddit.subreddit(acc) == "u_hrp_ai"


# ============================================================================ WhatsApp (by hand)
async def test_whatsapp_share_link_encodes_text():
    url = manual.whatsapp_share_url("Hello *team* & friends\nhttps://hrpai.in/?a=1&b=2")
    assert url.startswith("https://wa.me/?text=")
    assert " " not in url and "&b" not in url and "\n" not in url
    assert unquote(url.split("=", 1)[1]) == "Hello *team* & friends\nhttps://hrpai.in/?a=1&b=2"


async def test_whatsapp_channel_is_a_manual_account_with_share_url(client, org):
    r = await client.post("/social/accounts/manual", headers=auth(org),
                          json={"platform": "whatsapp", "name": "HRP AI channel",
                                "profile_url": "https://whatsapp.com/channel/0029Va"})
    assert r.status_code == 201, r.text
    assert r.json()["platform_label"] == "WhatsApp channel (post by hand)" and r.json()["status"] == "manual"
    o = org_of(org)
    async with SessionLocal() as s:
        set_tenant(s, o)
        acc = await s.get(SocialAccount, r.json()["id"])
    post = await make_post(o, acc, "link", text="New guide is out", link_url="https://hrpai.in/guide")
    d = (await client.get(f"/content/posts/{post.id}", headers=auth(org))).json()
    assert unquote(d["share_url"].split("=", 1)[1]) == "New guide is out\n\nhttps://hrpai.in/guide"
    assert publishing.is_manual(acc)
    with pytest.raises(PublishError) as e:
        await do_publish(o, post, acc)
    assert "WhatsApp" in e.value.message and "by hand" in e.value.message
    r = await client.post("/social/accounts/manual", headers=auth(org), json={"platform": "telegram", "name": "x"})
    assert r.status_code == 400


async def test_new_platform_specs_and_best_times():
    from app.services import best_time
    from app.services.platforms import PLATFORMS

    assert PLATFORMS["telegram"].max_chars == 4096 and PLATFORMS["telegram"].max_images == 10
    assert PLATFORMS["discord"].max_chars == 2000 and PLATFORMS["discord"].max_hashtags == 0
    assert PLATFORMS["reddit"].title_chars == 300 and PLATFORMS["reddit"].oauth_app == "reddit_app"
    assert PLATFORMS["whatsapp"].manual and "hashtags" in PLATFORMS["whatsapp"].style.lower()
    for p in ("telegram", "discord", "reddit", "whatsapp"):
        assert best_time.FAMILY[p] in best_time.DEFAULTS and PLATFORMS[p].style
