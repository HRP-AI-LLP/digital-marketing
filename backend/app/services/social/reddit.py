"""Reddit through the official OAuth API (a free "web app" registered at reddit.com/prefs/apps).

Connect: OAuth code flow with duration=permanent (scopes identity, submit, read, edit, flair). Access tokens last a
day and are renewed with the refresh token. Every request carries the descriptive User-Agent Reddit's API rules ask
for: "web:hrp-digital-marketing:v1.0 (by /u/<username>)".

Account settings: subreddit (default: the account's own profile, u_<username>), optional flair_id / flair_text.

Publish: text posts (title + markdown body, kind=self), link posts (title + URL, kind=link) and image posts. Reddit's
native image upload (media/asset.json + S3 + websocket) is not used: images are posted as a link post to the image's
public address (so a public media address is needed), which Reddit shows as an image preview.
Metrics: score (likes) and comment count from /api/info. Delete: /api/del.
"""

import base64
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens, ok_json

AUTHORIZE = "https://www.reddit.com/api/v1/authorize"
TOKEN = "https://www.reddit.com/api/v1/access_token"
API = "https://oauth.reddit.com"
SCOPES = ["identity", "submit", "read", "edit", "flair"]
TITLE_LIMIT = 300
BODY_LIMIT = 40000

ERRORS = {
    "SUBREDDIT_NOEXIST": "That subreddit doesn't exist. Check the name in the account settings.",
    "SUBREDDIT_NOTALLOWED": "You aren't allowed to post in that subreddit (it may be private or restricted).",
    "SUBREDDIT_REQUIRED": "Choose a subreddit in the account settings.",
    "NO_SELFS": "That subreddit doesn't allow text posts. Use a link post or another subreddit.",
    "NO_LINKS": "That subreddit doesn't allow link or image posts. Use a text post or another subreddit.",
    "SUBMIT_VALIDATION_FLAIR_REQUIRED": "That subreddit requires a post flair. Add a flair in the account settings.",
    "ALREADY_SUB": "That link was already posted in this subreddit.",
    "TOO_LONG": "The title or text is too long for Reddit (titles 300 characters, text 40,000).",
    "NO_TEXT": "Reddit needs a title for every post.",
    "BAD_URL": "Reddit didn't accept the link. Check it starts with https://.",
    "USER_REQUIRED": "Reddit signed this account out. Connect Reddit again.",
    "BANNED_FROM_SUBREDDIT": "This account is banned from that subreddit.",
    "SPAM": "Reddit's spam filter refused this post. Post less often, or check the subreddit's rules.",
}


def user_agent(username: str | None = None, creds: dict | None = None) -> str:
    who = (username or (creds or {}).get("developer_username") or "hrp-studio").strip().lstrip("/").removeprefix("u/")
    return f"web:hrp-digital-marketing:v1.0 (by /u/{who})"


def _basic(creds: dict) -> str:
    return "Basic " + base64.b64encode(f"{creds['client_id']}:{creds['client_secret']}".encode()).decode()


def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"client_id": creds["client_id"], "response_type": "code", "state": state, "redirect_uri": redirect_uri,
         "duration": "permanent", "scope": " ".join(SCOPES)}
    return f"{AUTHORIZE}?{urlencode(q)}"


def _tokens(t: dict, old: dict | None = None) -> dict:
    if t.get("error"):
        msg = {"invalid_grant": "The Reddit sign-in expired. Connect Reddit again."}.get(t["error"], str(t["error"]))
        raise PublishError(msg, reconnect=True)
    old = old or {}
    return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token") or old.get("refresh_token"),
            "scope": t.get("scope") or old.get("scope")}


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    async with net.client() as c:
        ua = user_agent(None, creds)
        t = ok_json(await c.post(TOKEN, headers={"Authorization": _basic(creds), "User-Agent": ua}, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}), "Reddit sign-in")
        tokens = _tokens(t)
        me = ok_json(await c.get(f"{API}/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}",
                                                               "User-Agent": ua}), "Reddit profile")
    name = me.get("name") or "reddit"
    icon = (me.get("icon_img") or "").split("?", 1)[0] or None
    acc = await common.upsert_account(
        session, org_id, user_id, "reddit", str(me.get("id") or name), name=f"u/{name}", handle=f"u/{name}",
        avatar_url=icon, profile_url=f"https://www.reddit.com/user/{name}/", tokens=tokens,
        expires_at=common.expires_from(t.get("expires_in")), settings={"subreddit": f"u_{name}", "username": name},
    )
    return [acc]


async def _token(c: httpx.AsyncClient, session, account: SocialAccount) -> str:
    tokens = account_tokens(account)
    if not tokens.get("access_token"):
        raise PublishError("No Reddit access stored. Connect Reddit again.", reconnect=True)
    if common.expiring(account, timedelta(minutes=10)) or account.token_expires_at is None:
        if not tokens.get("refresh_token"):
            raise PublishError("The Reddit sign-in expired. Connect Reddit again.", reconnect=True)
        creds = await common.app_credentials(session, "reddit")
        if not creds:
            raise PublishError("The Reddit app credentials are missing in Integrations.")
        r = await c.post(TOKEN, headers={"Authorization": _basic(creds), "User-Agent": _ua(account)},
                         data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        if r.status_code in (400, 401):
            raise PublishError("Reddit refused to renew access. Connect Reddit again.", reconnect=True)
        t = ok_json(r, "Reddit token refresh")
        tokens = _tokens(t, tokens)
        common.save_tokens(account, tokens, common.expires_from(t.get("expires_in")))
    return tokens["access_token"]


def _ua(account: SocialAccount) -> str:
    return user_agent((account.settings or {}).get("username") or (account.handle or "").removeprefix("u/") or None)


def _h(account: SocialAccount, tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "User-Agent": _ua(account)}


def _check_rate(r: httpx.Response) -> None:
    """Reddit allows about 100 requests a minute per client. A 429 means wait for the window to reset."""
    if r.status_code == 429:
        reset = r.headers.get("x-ratelimit-reset")
        raise PublishError("Reddit's rate limit was reached" + (f" (it resets in {reset} s)" if reset else "")
                           + ". It will be retried.", retryable=True)


async def refresh(session, account: SocialAccount) -> None:
    async with net.client() as c:
        tok = await _token(c, session, account)
        r = await c.get(f"{API}/api/v1/me", headers=_h(account, tok))
        _check_rate(r)
        me = ok_json(r, "Checking the account")
    if me.get("name"):
        account.settings = {**(account.settings or {}), "username": me["name"]}


def subreddit(account: SocialAccount) -> str:
    s = account.settings or {}
    sr = str(s.get("subreddit") or "").strip()
    sr = sr.removeprefix("https://www.reddit.com/").removeprefix("https://reddit.com/").strip("/")
    sr = sr.removeprefix("r/").removeprefix("/r/")
    if sr.startswith(("u/", "user/")):
        sr = "u_" + sr.split("/", 1)[1]
    if not sr:
        user = s.get("username") or (account.handle or "").removeprefix("u/")
        sr = f"u_{user}" if user else ""
    if not sr:
        raise PublishError("Choose a subreddit in the account settings.")
    return sr


def _submit_error(errors: list) -> PublishError:
    first = errors[0] if errors else ["", "", ""]
    code = str(first[0] if len(first) > 0 else "")
    detail = str(first[1] if len(first) > 1 else "")
    if code == "RATELIMIT":
        return PublishError(f"Reddit says to wait before posting again: {detail}", retryable=True)
    return PublishError(ERRORS.get(code) or f"Reddit refused the post: {detail or code}",
                        reconnect=code == "USER_REQUIRED")


def _title(post: Post) -> str:
    title = (post.title or "").strip()
    if not title:
        first = (post.text or "").strip().split("\n", 1)[0].strip()
        title = first or (post.topic or "").strip()
    if not title:
        raise PublishError("Reddit posts need a title. Add one to the post.")
    return title[:TITLE_LIMIT]


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    sr = subreddit(account)
    s = account.settings or {}
    form: dict = {"sr": sr, "title": _title(post), "api_type": "json", "resubmit": "true", "sendreplies": "true"}
    if s.get("flair_id"):
        form["flair_id"] = str(s["flair_id"])[:64]
    if s.get("flair_text"):
        form["flair_text"] = str(s["flair_text"])[:64]
    images = [m for m in media if not common.is_video(m) and not common.is_pdf(m)]
    if images:
        form.update(kind="link", url=await common.public_url(session, images[0], jpeg=True))
    elif post.post_type == "link" and post.link_url:
        form.update(kind="link", url=post.link_url)
    else:
        body = common.compose_text(post, hashtags=False, link=bool(post.link_url))
        if not post.title:  # the first line became the title
            first, _, rest = body.partition("\n")
            body = rest.strip() if first.strip() == form["title"] else body
        if len(body) > BODY_LIMIT:
            raise PublishError(f"Reddit text posts are limited to {BODY_LIMIT:,} characters. Shorten it.")
        form.update(kind="self", text=body)
    async with net.client() as c:
        tok = await _token(c, session, account)
        r = await c.post(f"{API}/api/submit", headers=_h(account, tok), data=form)
        _check_rate(r)
        d = ok_json(r, "Reddit post")
    j = d.get("json") or {}
    if j.get("errors"):
        raise _submit_error(j["errors"])
    data = j.get("data") or {}
    name = data.get("name") or (f"t3_{data['id']}" if data.get("id") else None)
    if not name:
        raise PublishError("Reddit didn't return the new post.")
    url = data.get("url")
    if form["kind"] == "link" or not url or "reddit.com" not in url:
        url = f"https://www.reddit.com/r/{sr}/comments/{name.removeprefix('t3_')}/" if not sr.startswith("u_") else \
            f"https://www.reddit.com/user/{sr[2:]}/comments/{name.removeprefix('t3_')}/"
    return name, url


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    if not post.external_id:
        return None
    async with net.client() as c:
        tok = await _token(c, session, account)
        r = await c.get(f"{API}/api/info", params={"id": post.external_id, "raw_json": 1}, headers=_h(account, tok))
        _check_rate(r)
        d = ok_json(r, "Reddit post")
    kids = (d.get("data") or {}).get("children") or []
    if not kids:
        return None
    p = kids[0].get("data") or {}
    return common.metrics({k: p.get(k) for k in ("score", "ups", "upvote_ratio", "num_comments", "num_crossposts")},
                          likes=p.get("score"), comments=p.get("num_comments"), shares=p.get("num_crossposts"))


async def delete(session, post: Post, account: SocialAccount) -> None:
    if not post.external_id:
        raise PublishError("This post has no Reddit id.")
    async with net.client() as c:
        tok = await _token(c, session, account)
        ok_json(await c.post(f"{API}/api/del", headers=_h(account, tok), data={"id": post.external_id}),
                "Deleting on Reddit")
