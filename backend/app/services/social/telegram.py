"""Telegram channel through the Bot API: a bot (from @BotFather) that is an admin of the channel posts for you.

Connect: bot token + channel (@username, t.me link or numeric chat id). We check getMe, getChat and that the bot is an
administrator allowed to post (getChatMember). The token is stored encrypted and never logged: it is part of every
Bot API URL (https://api.telegram.org/bot<token>/...), which app.core.logsafe redacts.

Publish: text (HTML parse mode, escaped; 4,096 characters), one photo / GIF / video / document uploaded as a file,
or an album of up to 10 photos and videos (sendMediaGroup). With media the caption holds 1,024 characters; longer
text goes in a separate message straight after the media. Link posts keep Telegram's link preview.

Metrics: the Bot API doesn't expose view counts for channel posts, so fetch_metrics returns None.
Delete: deleteMessages (Telegram allows it only within 48 hours for bots without delete rights).
"""

import html
import json
import re

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens

API = "https://api.telegram.org"
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
ALBUM_MAX = 10
PHOTO_MAX_BYTES = 10_000_000
FILE_MAX_BYTES = 50_000_000  # bots can upload files up to 50 MB

TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,64}$")
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def _url(token: str, method: str) -> str:
    return f"{API}/bot{token}/{method}"


def escape(text: str) -> str:
    """Escape for parse_mode=HTML: only <, > and & are special."""
    return html.escape(text or "", quote=False)


def tg_len(text: str) -> int:
    """Telegram counts message length in UTF-16 code units (emoji count as 2)."""
    return len((text or "").encode("utf-16-le")) // 2


def normalize_chat(value: str) -> str:
    """@channel, channel, t.me/channel, https://t.me/channel or -100… → what the Bot API takes as chat_id."""
    v = (value or "").strip()
    v = re.sub(r"^(https?://)?(www\.)?(t\.me|telegram\.me)/", "", v, flags=re.I).strip("/")
    if v.startswith("s/"):
        v = v[2:]
    if re.fullmatch(r"-?\d{5,20}", v):
        return v
    v = v.lstrip("@").split("/")[0]
    if not USERNAME_RE.fullmatch(v):
        raise PublishError("Enter the channel as @username (for example @hrpai_updates), its t.me link, or its "
                           "numeric chat id (starts with -100).")
    return "@" + v


def _raise(d: dict, status: int, what: str) -> None:
    desc = str(d.get("description") or f"HTTP {status}")
    code = int(d.get("error_code") or status)
    low = desc.lower()
    if code == 429:
        wait = (d.get("parameters") or {}).get("retry_after")
        raise PublishError(f"{what}: Telegram asks to slow down" + (f" (try again in {wait} s)." if wait else "."),
                           retryable=True)
    if code >= 500:
        raise PublishError(f"{what}: Telegram had a problem ({desc}). It will be retried.", retryable=True)
    if code == 401 or "unauthorized" in low:
        raise PublishError("Telegram didn't accept the bot token. It may have been revoked in @BotFather: "
                           "add the channel again with the current token.", reconnect=True)
    if code == 403 or "not a member" in low or "kicked" in low or "not enough rights" in low \
            or "have no rights" in low:
        raise PublishError("The bot can't post in this channel any more. Make it an administrator with "
                           "\"Post messages\" permission, then press Check now.", reconnect=True)
    if "chat not found" in low:
        raise PublishError("Telegram can't find that channel. Check the @username (public channels) or add the "
                           "bot as an admin first and use the numeric chat id (private channels).")
    if "message is too long" in low or "caption is too long" in low:
        raise PublishError(f"{what}: the text is too long for Telegram. Shorten it to {TEXT_LIMIT} characters.")
    if "wrong file" in low or "failed to get http url content" in low or "photo_invalid" in low \
            or "image_process_failed" in low:
        raise PublishError(f"{what}: Telegram couldn't use this media file ({desc}). Try a JPG/PNG image or an MP4 video.")
    raise PublishError(f"{what}: {desc}")


async def _call(c: httpx.AsyncClient, token: str, method: str, what: str, *, json: dict | None = None,
                data: dict | None = None, files: dict | None = None):
    if files is not None:
        r = await c.post(_url(token, method), data=data or {}, files=files)
    else:
        r = await c.post(_url(token, method), json=json or {})
    return _result(r, what)


def _result(r: httpx.Response, what: str):
    try:
        d = r.json()
    except ValueError:
        d = {}
    if not isinstance(d, dict) or not d.get("ok"):
        _raise(d if isinstance(d, dict) else {}, r.status_code, what)
    return d.get("result")


def _can_post(chat: dict, member: dict) -> tuple[bool, str]:
    status = member.get("status")
    if chat.get("type") == "channel":
        if status == "creator":
            return True, ""
        if status != "administrator":
            return False, ("The bot isn't an administrator of this channel. In Telegram open the channel → "
                           "Administrators → Add admin, pick your bot and allow \"Post messages\".")
        if member.get("can_post_messages") is False:
            return False, ("The bot is an administrator but isn't allowed to post. Edit its admin rights and switch on "
                           "\"Post messages\".")
        return True, ""
    if status in ("creator", "administrator"):
        return True, ""
    if status == "member" or (status == "restricted" and member.get("can_send_messages")):
        return True, ""
    return False, "The bot isn't a member of this group. Add it to the group (as an admin) and try again."


async def _check(c: httpx.AsyncClient, token: str, chat_id: str) -> tuple[dict, dict]:
    me = await _call(c, token, "getMe", "Checking the bot")
    chat = await _call(c, token, "getChat", "Finding the channel", json={"chat_id": chat_id})
    if chat.get("type") == "private":
        raise PublishError("That's a person, not a channel. Enter your channel's @username or chat id.")
    member = await _call(c, token, "getChatMember", "Checking the bot's rights",
                         json={"chat_id": chat["id"], "user_id": me["id"]})
    ok, why = _can_post(chat, member)
    if not ok:
        raise PublishError(why, reconnect=True)
    return me, chat


async def connect(session, org_id, user_id, bot_token: str, channel: str) -> SocialAccount:
    token = (bot_token or "").strip()
    if token.lower().startswith("bot"):
        token = token[3:]
    if not TOKEN_RE.fullmatch(token):
        raise PublishError("That doesn't look like a bot token. In Telegram, open @BotFather → /mybots → your bot → "
                           "API Token, and copy the whole value (numbers, a colon, then letters).")
    chat_id = normalize_chat(channel)
    async with net.client() as c:
        me, chat = await _check(c, token, chat_id)
    username = chat.get("username")
    return await common.upsert_account(
        session, org_id, user_id, "telegram", str(chat["id"]), name=chat.get("title") or username or str(chat["id"]),
        handle=f"@{username}" if username else None, avatar_url=None,
        profile_url=f"https://t.me/{username}" if username else None,
        tokens={"bot_token": token}, expires_at=None,
        settings={"chat_id": str(chat["id"]), "username": username, "chat_type": chat.get("type"),
                  "bot_username": me.get("username")},
        status_message=None if username else "Private channel: post links open only for channel members.",
    )


def _ctx(account: SocialAccount) -> tuple[str, str]:
    tok = account_tokens(account).get("bot_token")
    if not tok:
        raise PublishError("No bot token stored. Add the Telegram channel again.", reconnect=True)
    chat_id = (account.settings or {}).get("chat_id") or account.external_id
    return tok, str(chat_id)


async def refresh(session, account: SocialAccount) -> None:
    tok, chat_id = _ctx(account)
    async with net.client() as c:
        _, chat = await _check(c, tok, chat_id)
    s = dict(account.settings or {})
    if chat.get("username") != s.get("username"):  # the channel's public link changed
        s["username"] = chat.get("username")
        account.handle = f"@{chat['username']}" if chat.get("username") else None
        account.profile_url = f"https://t.me/{chat['username']}" if chat.get("username") else None
        account.settings = s
    if chat.get("title"):
        account.name = chat["title"][:200]


def message_url(account: SocialAccount, message_id) -> str | None:
    s = account.settings or {}
    if s.get("username"):
        return f"https://t.me/{s['username']}/{message_id}"
    cid = str(s.get("chat_id") or account.external_id or "")
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{message_id}"  # opens for channel members
    return None


def _kind(m: MediaAsset) -> str:
    if common.is_pdf(m):
        return "document"
    if common.is_video(m):
        return "video"
    if common.is_gif(m):
        return "animation"
    return "photo"


async def _file(m: MediaAsset, kind: str) -> tuple[str, bytes, str]:
    data, mime = common.read_bytes(m), m.mime or "application/octet-stream"
    name = common.filename(m)
    if kind == "photo":
        data, mime = common.fit_image(data, mime, PHOTO_MAX_BYTES, max_side=4096)
        if len(data) > PHOTO_MAX_BYTES:
            raise PublishError("This image is too large for Telegram (10 MB) even after compressing it.")
        if mime == "image/jpeg" and not name.lower().endswith((".jpg", ".jpeg")):
            name = name.rsplit(".", 1)[0] + ".jpg"
    elif kind == "video" and len(data) > FILE_MAX_BYTES:
        data, mime = await common.fit_video(data, int(FILE_MAX_BYTES * 0.97), name=name), "video/mp4"
        name = name.rsplit(".", 1)[0] + ".mp4"
    if len(data) > FILE_MAX_BYTES:
        mb = len(data) // 1_000_000
        raise PublishError(f"This file is {mb} MB; Telegram bots can upload files up to 50 MB. Use a shorter or "
                           "smaller video.")
    return name, data, mime


SEND = {"photo": ("sendPhoto", "photo"), "video": ("sendVideo", "video"), "animation": ("sendAnimation", "animation"),
        "document": ("sendDocument", "document")}


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    tok, chat_id = _ctx(account)
    text = common.compose_text(post, link=bool(post.link_url))
    if post.title and post.title.strip() and post.title.strip() not in text:
        text = f"{post.title.strip()}\n\n{text}"
    if tg_len(text) > TEXT_LIMIT:
        raise PublishError(f"Telegram messages are limited to {TEXT_LIMIT} characters; this one has {tg_len(text)}. "
                           "Shorten it.")
    body = escape(text)
    preview = {"is_disabled": not post.link_url}
    if post.link_url:
        preview["url"] = post.link_url
    media = [m for m in media][:ALBUM_MAX]
    ids: list[int] = []
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        fits = tg_len(text) <= CAPTION_LIMIT
        caption = {"caption": body, "parse_mode": "HTML"} if fits and text else {}
        if not media:
            if not text:
                raise PublishError("This post has no text to send.")
            msg = await _call(c, tok, "sendMessage", "Telegram post", json={
                "chat_id": chat_id, "text": body, "parse_mode": "HTML", "link_preview_options": preview})
            ids.append(msg["message_id"])
        elif len(media) == 1 or any(common.is_pdf(m) or common.is_gif(m) for m in media):
            # One file. Documents and GIFs can't share an album with photos, so the document (or GIF) is sent.
            m = next((x for x in media if common.is_pdf(x)), None) or next((x for x in media if common.is_gif(x)), media[0])
            kind = _kind(m)
            method, field = SEND[kind]
            name, data, mime = await _file(m, kind)
            msg = await _call(c, tok, method, "Telegram post", data={"chat_id": chat_id, **caption},
                              files={field: (name, data, mime)})
            ids.append(msg["message_id"])
        else:
            items, files = [], {}
            for i, m in enumerate(media):
                kind = "video" if common.is_video(m) else "photo"
                key = f"file{i}"
                files[key] = await _file(m, kind)
                item = {"type": kind, "media": f"attach://{key}"}
                if i == 0 and caption:
                    item.update(caption)
                items.append(item)
            msgs = await _call(c, tok, "sendMediaGroup", "Telegram album",
                               data={"chat_id": chat_id, "media": json.dumps(items)}, files=files)
            ids += [x["message_id"] for x in msgs or []]
        if media and text and not fits:
            # Too long for a caption: the full text follows as its own message.
            msg = await _call(c, tok, "sendMessage", "Telegram post text", json={
                "chat_id": chat_id, "text": body, "parse_mode": "HTML", "link_preview_options": preview})
            ids.append(msg["message_id"])
    if not ids:
        raise PublishError("Telegram didn't return the new message.")
    return f"{chat_id}/{','.join(str(i) for i in ids)}", message_url(account, ids[0])


def _message_ids(post: Post) -> tuple[str, list[int]]:
    raw = post.external_id or ""
    chat, _, rest = raw.rpartition("/")
    try:
        return chat, [int(x) for x in rest.split(",") if x]
    except ValueError:
        return chat, []


async def delete(session, post: Post, account: SocialAccount) -> None:
    tok, chat_id = _ctx(account)
    chat, ids = _message_ids(post)
    if not ids:
        raise PublishError("This post has no Telegram message id, so it can't be deleted from here.")
    async with net.client() as c:
        try:
            await _call(c, tok, "deleteMessages", "Deleting on Telegram",
                        json={"chat_id": chat or chat_id, "message_ids": ids})
        except PublishError as e:
            if "can't be deleted" in e.message.lower() or "message to delete not found" in e.message.lower():
                raise PublishError("Telegram didn't let the bot delete this message (bots can delete posts only "
                                   "within 48 hours unless they have the \"Delete messages\" right). Delete it in "
                                   "Telegram.") from e
            raise


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    # The Bot API has no endpoint for channel post views or reactions counts, so there's nothing to collect.
    return None
