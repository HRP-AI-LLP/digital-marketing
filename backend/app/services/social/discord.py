"""Discord channel through a channel webhook (Server settings → Integrations → Webhooks → New webhook → Copy URL).

The webhook URL is the credential (anyone with it can post), so it is stored encrypted, never returned by the API
and never logged (app.core.logsafe masks /api/webhooks/<id>/<token>).

Publish: content up to 2,000 characters, an embed card for link posts, and up to 10 files (images, GIFs, videos,
PDFs) uploaded as multipart; 10 MB per file by default (videos and images are shrunk to fit) (boosted servers allow more: account setting
max_file_mb). We post with ?wait=true so Discord returns the message, and link to it when the webhook's server and
channel are known. Mentions (@everyone, roles, users) are never triggered.
Delete: DELETE on the webhook message. Metrics: webhooks can't read reactions, so fetch_metrics returns None.
"""

import json
import re

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens

CONTENT_LIMIT = 2000
EMBED_DESCRIPTION_LIMIT = 4096
MAX_FILES = 10
DEFAULT_FILE_MB = 10  # Discord servers without boosts take 10 MB; boosted servers can raise it in the account settings

WEBHOOK_RE = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?(?:discord\.com|discordapp\.com)/api(?:/v\d{1,2})?/webhooks/(\d{5,25})/([A-Za-z0-9_.-]{20,200})/?$")


def normalize_webhook(url: str) -> tuple[str, str]:
    """Validate a channel webhook URL; returns (canonical url, webhook id)."""
    m = WEBHOOK_RE.match((url or "").strip().split("?", 1)[0].split("#", 1)[0])
    if not m:
        raise PublishError("That isn't a Discord webhook address. In Discord open the channel's settings → "
                           "Integrations → Webhooks → New webhook → Copy webhook URL. It starts with "
                           "https://discord.com/api/webhooks/.")
    return f"https://discord.com/api/webhooks/{m.group(1)}/{m.group(2)}", m.group(1)


def _error(r: httpx.Response, what: str) -> PublishError:
    try:
        d = r.json()
    except ValueError:
        d = {}
    msg = str(d.get("message") or r.reason_phrase or f"HTTP {r.status_code}") if isinstance(d, dict) else str(d)
    code = r.status_code
    if code == 429:
        wait = d.get("retry_after") if isinstance(d, dict) else None
        return PublishError(f"{what}: Discord asks to slow down" + (f" (try again in {wait} s)." if wait else "."),
                            retryable=True)
    if code >= 500:
        return PublishError(f"{what}: Discord had a problem. It will be retried.", retryable=True)
    if code in (401, 403, 404) or (isinstance(d, dict) and d.get("code") in (10015, 50027)):
        return PublishError("Discord doesn't know this webhook any more (it was deleted or reset). Create a new "
                            "webhook in the channel and add the channel again.", reconnect=True)
    if code == 413 or (isinstance(d, dict) and d.get("code") == 40005):
        return PublishError(f"{what}: the files are too large for this Discord server. Use smaller files or raise "
                            "the file size limit in the account settings if the server is boosted.")
    return PublishError(f"{what}: {msg[:300]}")


def _ok(r: httpx.Response, what: str) -> dict:
    if not r.is_success:
        raise _error(r, what)
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


async def _info(c: httpx.AsyncClient, url: str) -> dict:
    return _ok(await c.get(url), "Checking the webhook")


async def connect(session, org_id, user_id, webhook_url: str, name: str | None = None) -> SocialAccount:
    url, wid = normalize_webhook(webhook_url)
    async with net.client() as c:
        info = await _info(c, url)
    if info.get("type") not in (None, 1):
        raise PublishError("That webhook can't post messages. Create an ordinary (incoming) webhook in the channel.")
    guild, channel = info.get("guild_id"), info.get("channel_id")
    avatar = f"https://cdn.discordapp.com/avatars/{wid}/{info['avatar']}.png" if info.get("avatar") else None
    return await common.upsert_account(
        session, org_id, user_id, "discord", str(info.get("id") or wid),
        name=(name or "").strip() or info.get("name") or "Discord channel", handle=info.get("name"),
        avatar_url=avatar, profile_url=f"https://discord.com/channels/{guild}/{channel}" if guild and channel else None,
        tokens={"webhook_url": url}, expires_at=None,
        settings={"guild_id": guild, "channel_id": channel, "max_file_mb": DEFAULT_FILE_MB},
    )


def _ctx(account: SocialAccount) -> str:
    url = account_tokens(account).get("webhook_url")
    if not url:
        raise PublishError("No webhook stored. Add the Discord channel again.", reconnect=True)
    return url


async def refresh(session, account: SocialAccount) -> None:
    url = _ctx(account)
    async with net.client() as c:
        info = await _info(c, url)
    s = dict(account.settings or {})
    if info.get("channel_id") and info.get("channel_id") != s.get("channel_id"):  # the webhook was moved
        s.update(guild_id=info.get("guild_id"), channel_id=info.get("channel_id"))
        account.settings = s
        account.profile_url = f"https://discord.com/channels/{s['guild_id']}/{s['channel_id']}"


def message_url(account: SocialAccount, msg: dict) -> str | None:
    s = account.settings or {}
    guild = msg.get("guild_id") or s.get("guild_id")
    channel = msg.get("channel_id") or s.get("channel_id")
    return f"https://discord.com/channels/{guild}/{channel}/{msg['id']}" if guild and channel and msg.get("id") else None


SMALL_LIMIT = 10 * 1024 * 1024
_MARGIN = 0.97  # leave room for the multipart wrapper


def _too_large(r: httpx.Response) -> bool:
    if r.status_code == 413:
        return True
    try:
        d = r.json()
    except ValueError:
        return False
    return isinstance(d, dict) and d.get("code") == 40005


async def _files(media: list[MediaAsset], limit: int) -> dict:
    """The attachments, with videos re-encoded and images recompressed to fit the server's upload limit."""
    files = {}
    cap = int(limit * _MARGIN)
    for i, m in enumerate(media):
        data, mime, name = common.read_bytes(m), m.mime or "application/octet-stream", common.filename(m)
        if len(data) > cap:
            if common.is_video(m):
                data, mime = await common.fit_video(data, cap, name=name), "video/mp4"
                name = name.rsplit(".", 1)[0] + ".mp4"
            elif mime.startswith("image/") and mime != "image/gif":
                data, mime = common.fit_image(data, mime, cap, max_side=4096)
                if mime == "image/jpeg" and not name.lower().endswith((".jpg", ".jpeg")):
                    name = name.rsplit(".", 1)[0] + ".jpg"
        if len(data) > cap:
            raise PublishError(f"{name} is {len(data) / 1_048_576:.1f} MB; this Discord server takes files up to "
                               f"{limit // 1_048_576} MB and it couldn't be made smaller. Use a smaller file (or raise "
                               "the limit in the account settings if the server is boosted).")
        files[f"files[{i}]"] = (name, data, mime)
    return files


def _limit_bytes(account: SocialAccount) -> int:
    try:
        mb = int((account.settings or {}).get("max_file_mb") or DEFAULT_FILE_MB)
    except (TypeError, ValueError):
        mb = DEFAULT_FILE_MB
    return max(1, min(500, mb)) * 1024 * 1024


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    url = _ctx(account)
    link_card = bool(post.link_url) and not media
    text = common.compose_text(post, link=bool(post.link_url) and not link_card)
    if post.title and post.title.strip() and post.title.strip() not in text and not link_card:
        text = f"**{post.title.strip()}**\n\n{text}"
    if len(text) > CONTENT_LIMIT:
        raise PublishError(f"Discord messages are limited to {CONTENT_LIMIT} characters; this one has {len(text)}. "
                           "Shorten it.")
    payload: dict = {"content": text, "allowed_mentions": {"parse": []}}
    if link_card:
        embed = {"url": post.link_url, "title": (post.title or post.topic or post.link_url)[:256]}
        desc = ((post.meta or {}).get("link_description") or "")[:EMBED_DESCRIPTION_LIMIT]
        if desc:
            embed["description"] = desc
        payload["embeds"] = [embed]
    if not text and not media and not link_card:
        raise PublishError("This post has no text to send.")
    limit = _limit_bytes(account)
    sent = media[:MAX_FILES]
    files = await _files(sent, limit)

    async def send(files: dict):
        if files:
            payload["attachments"] = [{"id": i, "filename": f[0], **({"description": m.alt_text[:1024]} if m.alt_text else {})}
                                      for i, (f, m) in enumerate(zip(files.values(), sent))]
        async with net.client(net.UPLOAD_TIMEOUT) as c:
            if files:
                return await c.post(url, params={"wait": "true"}, data={"payload_json": json.dumps(payload)}, files=files)
            return await c.post(url, params={"wait": "true"}, json=payload)

    r = await send(files)
    if files and _too_large(r) and limit > SMALL_LIMIT:
        # The server's real limit is lower than the account setting (servers without boosts take 10 MB):
        # make the files fit that, remember it for next time, and send again.
        account.settings = {**(account.settings or {}), "max_file_mb": SMALL_LIMIT // 1_048_576}
        r = await send(await _files(sent, SMALL_LIMIT))
    msg = _ok(r, "Discord post")
    if not msg.get("id"):
        raise PublishError("Discord didn't return the new message.")
    return str(msg["id"]), message_url(account, msg)


async def delete(session, post: Post, account: SocialAccount) -> None:
    url = _ctx(account)
    if not post.external_id:
        raise PublishError("This post has no Discord message id.")
    async with net.client() as c:
        r = await c.delete(f"{url}/messages/{post.external_id}")
    if r.status_code == 404:
        try:
            gone = (r.json() or {}).get("code") == 10008  # Unknown Message: already deleted
        except ValueError:
            gone = False
        if gone:
            return
    if not r.is_success:
        raise _error(r, "Deleting on Discord")


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    """Reactions on our message (a webhook can read back the messages it sent). Discord has no view counts."""
    if not post.external_id:
        return None
    url = _ctx(account)
    try:
        async with net.client() as c:
            r = await c.get(f"{url}/messages/{post.external_id}")
    except httpx.HTTPError:
        return None
    if not r.is_success:
        return None
    msg = r.json() if r.content else {}
    reactions = msg.get("reactions") or []
    total = sum(int(x.get("count") or 0) for x in reactions)
    return common.metrics({"reactions": [{"emoji": (x.get("emoji") or {}).get("name"), "count": x.get("count")} for x in reactions]},
                          likes=total)
