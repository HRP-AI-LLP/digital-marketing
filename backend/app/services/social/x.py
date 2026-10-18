"""X (Twitter) API v2: OAuth 2.0 with PKCE, refresh-token rotation, posts with images / GIF / video
(chunked media upload v2), long text split into a thread, delete, and public metrics where the plan allows.

X's free tier (where still offered) is write-limited (a handful of posts a day, no reading of metrics) and
pay-per-use plans refuse requests once credits run out. Those answers (402, some 403s, daily 429s) are reported as plan
limits, never as "reconnect the account".
"""

import re
import unicodedata
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens

AUTHORIZE = "https://x.com/i/oauth2/authorize"
API = "https://api.x.com/2"
TOKEN = f"{API}/oauth2/token"
SCOPES = ["tweet.read", "tweet.write", "users.read", "offline.access", "media.write"]

MAX_CHARS = 280
LONG_POST_CHARS = 25_000  # X Premium accounts
MAX_THREAD = 25
URL_WEIGHT = 23
CHUNK = 4 * 1024 * 1024
MEDIA_POLLS = 60
MEDIA_POLL_SECONDS = 3

PLAN_HINT = ("X's API plans limit what apps can do: the free tier (where still offered) allows only a few posts a day "
             "and no reading of post numbers, and pay-per-use plans stop when credits run out. Check your plan and "
             "credits at developer.x.com.")


# ------------------------------------------------------------------ errors
def _detail(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return (r.text or r.reason_phrase or f"HTTP {r.status_code}")[:300]
    if isinstance(d, dict):
        errs = d.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict):
            m = errs[0].get("message") or errs[0].get("detail")
            if m:
                return str(m)[:300]
        for k in ("detail", "error_description", "title", "error"):
            if d.get(k):
                return str(d[k])[:300]
    return str(d)[:300]


def raise_for(r: httpx.Response, what: str) -> None:
    if r.is_success:
        return
    code = r.status_code
    msg = _detail(r)
    low = msg.lower()
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {}
    reason = str(body.get("reason") or body.get("type") or "") if isinstance(body, dict) else ""
    if code == 401 or "invalid_grant" in low or "invalid_token" in low:
        raise PublishError(f"{what}: X no longer accepts this connection ({msg}). Connect X again.", reconnect=True)
    if code == 402 or "credits" in reason or "credits" in low:
        raise PublishError(f"{what}: your X developer plan doesn't allow this right now (payment required). {PLAN_HINT}")
    if code == 403:
        if "duplicate" in low:
            raise PublishError(f"{what}: X refuses duplicate posts. This text was already posted on this account.")
        raise PublishError(
            f"{what}: X refused this ({msg}). Usually your X developer plan doesn't include it, or the app isn't set to "
            f"'Read and write' under User authentication settings. {PLAN_HINT}")
    if code == 429:
        h = r.headers
        if h.get("x-user-limit-24hour-remaining") == "0" or h.get("x-app-limit-24hour-remaining") == "0":
            raise PublishError(f"{what}: the daily posting limit of your X plan has been reached. Try again tomorrow. {PLAN_HINT}")
        raise PublishError(f"{what}: X is rate limiting requests. It will be retried.", retryable=True)
    raise PublishError(f"{what}: {msg}", retryable=code >= 500)


def ok_json(r: httpx.Response, what: str) -> dict:
    raise_for(r, what)
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


# ------------------------------------------------------------------ OAuth
def _basic(creds: dict) -> httpx.BasicAuth:
    return httpx.BasicAuth(creds["client_id"], creds["client_secret"])


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"response_type": "code", "client_id": creds["client_id"], "redirect_uri": redirect_uri,
         "scope": " ".join(SCOPES), "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256"}
    return f"{AUTHORIZE}?{urlencode(q)}"


def _tokens(t: dict, old: dict | None = None) -> dict:
    old = old or {}
    return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token") or old.get("refresh_token"),
            "scope": t.get("scope") or old.get("scope")}


async def _me(c: httpx.AsyncClient, tok: str) -> dict:
    return ok_json(await c.get(f"{API}/users/me", params={"user.fields": "profile_image_url,username,name"},
                               headers=_bearer(tok)), "X profile").get("data") or {}


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    async with net.client() as c:
        t = ok_json(await c.post(TOKEN, auth=_basic(creds), data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "code_verifier": data.get("code_verifier") or "", "client_id": creds["client_id"]}), "X sign-in")
        if "media.write" not in (t.get("scope") or "media.write"):
            session.info["connect_warning"] = ("X didn't grant media upload (media.write), so posts with images or "
                                               "videos will fail. Check the app's permissions and connect again.")
        me = await _me(c, t["access_token"])
    if not me.get("id"):
        raise PublishError("X didn't return the account details. Try connecting again.")
    username = me.get("username")
    acc = await common.upsert_account(
        session, org_id, user_id, "x", str(me["id"]), name=me.get("name") or username or "X",
        handle=f"@{username}" if username else None,
        avatar_url=me.get("profile_image_url"), profile_url=f"https://x.com/{username}" if username else None,
        tokens=_tokens(t), expires_at=common.expires_from(t.get("expires_in")),
        settings={"long_posts": False, "collect_metrics": False, "allow_links": False, "monthly_post_limit": 15},
    )
    return [acc]


async def _refresh_tokens(session, account: SocialAccount, c: httpx.AsyncClient) -> str:
    tokens = account_tokens(account)
    if not tokens.get("refresh_token"):
        raise PublishError("X access expired and there is no refresh token. Connect X again.", reconnect=True)
    creds = await common.app_credentials(session, "x")
    if not creds:
        raise PublishError("The X app credentials are missing in Integrations.")
    t = ok_json(await c.post(TOKEN, auth=_basic(creds), data={
        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": creds["client_id"]}),
        "X token refresh")
    new = _tokens(t, tokens)
    # X rotates refresh tokens: save the new pair before anything else can fail.
    await common.persist_tokens(account, new, common.expires_from(t.get("expires_in")))
    return new["access_token"]


async def access_token(session, account: SocialAccount, c: httpx.AsyncClient) -> str:
    """A working access token; X's last only two hours, so refresh when one is about to run out."""
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect X again.", reconnect=True)
    if account.token_expires_at is None or common.expiring(account, timedelta(minutes=5)):
        tok = await _refresh_tokens(session, account, c)
    return tok


async def refresh(session, account: SocialAccount) -> None:
    async with net.client() as c:
        tok = await access_token(session, account, c)
        try:
            await _me(c, tok)
        except PublishError as e:
            if e.reconnect:
                raise
            # A plan limit on reading the profile doesn't mean the connection is broken.
            account.status_message = "Connected. X's plan limits stop this app reading the profile, which is fine for posting."


# ------------------------------------------------------------------ text: weighted length & threads
_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def _char_weight(ch: str) -> int:
    o = ord(ch)
    if o <= 4351 or 8192 <= o <= 8205 or 8208 <= o <= 8223 or 8242 <= o <= 8247:
        return 1
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):  # combining marks / joiners ride on the previous char
        return 0
    return 2


def weighted_len(text: str) -> int:
    """Length as X counts it: links are 23, most CJK characters and emoji 2, everything else 1."""
    total, pos = 0, 0
    for m in _URL.finditer(text):
        total += sum(_char_weight(ch) for ch in text[pos:m.start()]) + URL_WEIGHT
        pos = m.end()
    return total + sum(_char_weight(ch) for ch in text[pos:])


def _hard_cut(word: str, limit: int) -> list[str]:
    out, cur = [], ""
    for ch in word:
        if weighted_len(cur + ch) > limit:
            out.append(cur)
            cur = ""
        cur += ch
    return out + ([cur] if cur else [])


def _units(text: str, budget: int) -> list[tuple[str, str]]:
    """(piece, separator before it): paragraphs, then sentences, then words for anything still too long."""
    out: list[tuple[str, str]] = []
    for pi, para in enumerate(p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()):
        sentences = [s for s in re.split(r"(?<=[.!?…])\s+", para) if s]
        for si, sent in enumerate(sentences):
            sep = "\n\n" if si == 0 and pi > 0 else ("" if si == 0 else " ")
            if weighted_len(sent) <= budget:
                out.append((sent, sep))
                continue
            for wi, word in enumerate(sent.split(" ")):
                wsep = sep if wi == 0 else " "
                if weighted_len(word) <= budget:
                    out.append((word, wsep))
                else:
                    for ci, part in enumerate(_hard_cut(word, budget)):
                        out.append((part, wsep if ci == 0 else ""))
    return out


def _pack(text: str, budget: int) -> list[str]:
    parts: list[str] = []
    cur = ""
    for piece, sep in _units(text, budget):
        cand = f"{cur}{sep}{piece}" if cur else piece
        if cur and weighted_len(cand) > budget:
            parts.append(cur.strip())
            cur = piece
        else:
            cur = cand
    if cur.strip():
        parts.append(cur.strip())
    return parts


def split_thread(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split long text into numbered posts at paragraph / sentence / word boundaries (never mid-word unless
    a single word is longer than a post)."""
    text = (text or "").strip()
    if weighted_len(text) <= limit:
        return [text]
    for reserve in (5, 7):  # " 1/9" or " 12/25" (plus a space)
        parts = _pack(text, limit - reserve)
        if len(parts) < 10 or reserve == 7:
            break
    parts = parts[:MAX_THREAD]
    n = len(parts)
    return [f"{p} {i}/{n}" for i, p in enumerate(parts, 1)]


# ------------------------------------------------------------------ media
def _category(asset: MediaAsset) -> str:
    if common.is_video(asset):
        return "tweet_video"
    if common.is_gif(asset):
        return "tweet_gif"
    return "tweet_image"


async def _upload(c: httpx.AsyncClient, tok: str, asset: MediaAsset) -> str:
    data = common.read_bytes(asset)
    mime = asset.mime or ("video/mp4" if common.is_video(asset) else "image/png")
    init = ok_json(await c.post(f"{API}/media/upload/initialize", headers=_bearer(tok), json={
        "media_type": mime, "total_bytes": len(data), "media_category": _category(asset)}), "X media upload")
    mid = str((init.get("data") or {}).get("id") or "")
    if not mid:
        raise PublishError("X didn't start the media upload. Try again.", retryable=True)
    for i, start in enumerate(range(0, len(data), CHUNK)):
        raise_for(await c.post(f"{API}/media/upload/{mid}/append", headers=_bearer(tok),
                               data={"segment_index": str(i)},
                               files={"media": (common.filename(asset), data[start:start + CHUNK], mime)}),
                  "X media upload")
    fin = ok_json(await c.post(f"{API}/media/upload/{mid}/finalize", headers=_bearer(tok)), "X media upload")
    info = (fin.get("data") or {}).get("processing_info")
    for _ in range(MEDIA_POLLS):
        if not info or info.get("state") == "succeeded":
            break
        if info.get("state") == "failed":
            err = (info.get("error") or {}).get("message") or "processing failed"
            raise PublishError(f"X couldn't process the media: {err}")
        await common.sleep(max(1, min(int(info.get("check_after_secs") or MEDIA_POLL_SECONDS), 30)))
        st = ok_json(await c.get(f"{API}/media/upload", params={"command": "STATUS", "media_id": mid},
                                 headers=_bearer(tok)), "X media status")
        info = (st.get("data") or {}).get("processing_info")
    else:
        raise PublishError("X is still processing the video. It will be retried.", retryable=True)
    if asset.alt_text and not common.is_video(asset):
        try:
            await c.post(f"{API}/media/metadata", headers=_bearer(tok),
                         json={"id": mid, "metadata": {"alt_text": {"text": asset.alt_text[:1000]}}})
        except httpx.HTTPError:
            pass  # alt text is nice to have
    return mid


def _pick_media(media: list[MediaAsset]) -> list[MediaAsset]:
    """X takes up to 4 images, or one video, or one GIF per post."""
    usable = [m for m in media if not common.is_pdf(m)]
    if not usable and media:
        raise PublishError("X doesn't take PDF documents. Attach images or a video instead.")
    for m in usable:
        if common.is_video(m) or common.is_gif(m):
            return [m]
    return usable[:4]


_URL = re.compile(r"(?:https?://|www\.)[^\s)]+", re.I)


def strip_links(text: str) -> str:
    """X charges much more for posts with a link, so links are left out unless the account allows them.
    Removed links become 'Link in bio'."""
    if not _URL.search(text or ""):
        return text
    t = _URL.sub("", text)
    t = re.sub(r"[ \t]+([.,!?:;])", r"\1", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"(?im)[ \t]*\b(?:read more|learn more|more details|details|visit us|visit|see more|explore(?: more)?|find out more|check it out)\b[^\n.!?]{0,20}:[ \t]*$", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return (t + "\n\nLink in bio.").strip()


# ------------------------------------------------------------------ publish / delete / metrics
async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    allow_links = bool((account.settings or {}).get("allow_links"))
    text = common.compose_text(post, link=allow_links)
    if not allow_links:
        text = strip_links(text)
    long_posts = bool((account.settings or {}).get("long_posts"))
    parts = [text[:LONG_POST_CHARS]] if long_posts and len(text) <= LONG_POST_CHARS else split_thread(text)
    if not parts or not parts[0]:
        if not media:
            raise PublishError("This X post has no text.")
        parts = [""]
    chosen = _pick_media(media)
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        tok = await access_token(session, account, c)
        media_ids = [await _upload(c, tok, m) for m in chosen]
        ids: list[str] = []
        for i, part in enumerate(parts):
            body: dict = {"text": part}
            if i == 0 and media_ids:
                body["media"] = {"media_ids": media_ids}
            if ids:
                body["reply"] = {"in_reply_to_tweet_id": ids[-1]}
            try:
                d = ok_json(await c.post(f"{API}/tweets", headers=_bearer(tok), json=body), "X post")
            except (PublishError, httpx.HTTPError) as e:
                if not ids:
                    raise
                # The start of the thread is live: report success with a note rather than risk a duplicate.
                note = getattr(e, "message", None) or type(e).__name__
                post.meta = {**(post.meta or {}), "x_thread_ids": ids,
                             "publish_note": f"Only {len(ids)} of {len(parts)} parts of the thread were posted: {note}"}
                break
            ids.append(str((d.get("data") or {}).get("id")))
        else:
            if len(ids) > 1:
                post.meta = {**(post.meta or {}), "x_thread_ids": ids}
    handle = (account.handle or "").lstrip("@") or "i"
    return ids[0], f"https://x.com/{handle}/status/{ids[0]}"


async def delete(session, post: Post, account: SocialAccount) -> None:
    ids = [str(i) for i in ((post.meta or {}).get("x_thread_ids") or [])] or [post.external_id]
    async with net.client() as c:
        tok = await access_token(session, account, c)
        for tid in reversed([i for i in ids if i]):
            r = await c.delete(f"{API}/tweets/{tid}", headers=_bearer(tok))
            if r.status_code == 404:
                continue  # already gone
            raise_for(r, "Deleting the X post")


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    # Reading posts costs credits on X's paid plans and isn't allowed on the free tier: only when switched on.
    if not post.external_id or not (account.settings or {}).get("collect_metrics"):
        return None
    async with net.client() as c:
        tok = await access_token(session, account, c)
        try:
            d = ok_json(await c.get(f"{API}/tweets/{post.external_id}", params={"tweet.fields": "public_metrics"},
                                    headers=_bearer(tok)), "X metrics")
        except PublishError as e:
            if e.reconnect or e.retryable:
                raise
            # Plan doesn't include reading posts: switch collection off and say so, instead of failing every 6 hours.
            account.settings = {**(account.settings or {}), "collect_metrics": False}
            account.status_message = ("Connected. Your X plan doesn't include reading post numbers, so collecting "
                                      "them was switched off. Posting still works.")
            return None
    pm = (d.get("data") or {}).get("public_metrics") or {}
    return common.metrics(d, impressions=pm.get("impression_count"), likes=pm.get("like_count"),
                          comments=pm.get("reply_count"),
                          shares=(pm.get("retweet_count") or 0) + (pm.get("quote_count") or 0),
                          saves=pm.get("bookmark_count"))
