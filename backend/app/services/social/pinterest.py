"""Pinterest API v5: OAuth, boards, image / carousel (base64) and video pins, token refresh, analytics."""

import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens, ok_json

AUTHORIZE = "https://www.pinterest.com/oauth/"
API = "https://api.pinterest.com/v5"
SANDBOX_API = "https://api-sandbox.pinterest.com/v5"
SCOPES = ["boards:read", "pins:read", "pins:write", "user_accounts:read"]

TRIAL_NOTE = ("If your Pinterest app has Trial access, new pins may be visible only to you. "
              "Apply for Standard access to publish publicly.")

MEDIA_POLLS = 60
MEDIA_POLL_SECONDS = 5


def api_base(account: SocialAccount | None) -> str:
    """Trial-access apps may have to write to the sandbox; switch per account with settings.sandbox."""
    return SANDBOX_API if account is not None and (account.settings or {}).get("sandbox") else API


def _basic(creds: dict) -> dict:
    raw = f"{creds['client_id']}:{creds['client_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"client_id": creds["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
         "scope": ",".join(SCOPES), "state": state}
    return f"{AUTHORIZE}?{urlencode(q)}"


def _tokens(t: dict, old: dict | None = None) -> dict:
    old = old or {}
    rexp = common.expires_from(t.get("refresh_token_expires_in"))
    return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token") or old.get("refresh_token"),
            "refresh_expires_at": rexp.isoformat() if rexp else old.get("refresh_expires_at"), "scope": t.get("scope")}


async def list_boards(account: SocialAccount) -> list[dict]:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    out: list[dict] = []
    bookmark = None
    async with net.client() as c:
        for _ in range(10):
            params = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            d = ok_json(await c.get(f"{api_base(account)}/boards", params=params, headers=_bearer(tok)), "Pinterest boards")
            out += [{"id": b["id"], "name": b.get("name"), "privacy": b.get("privacy")} for b in d.get("items", [])]
            bookmark = d.get("bookmark")
            if not bookmark:
                break
    return out


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    async with net.client() as c:
        t = ok_json(await c.post(f"{API}/oauth/token", headers=_basic(creds), data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}), "Pinterest sign-in")
        tok = t["access_token"]
        u = ok_json(await c.get(f"{API}/user_account", headers=_bearer(tok)), "Pinterest profile")
        boards = ok_json(await c.get(f"{API}/boards", params={"page_size": 100}, headers=_bearer(tok)),
                         "Pinterest boards").get("items", [])
    username = u.get("username") or "pinterest"
    settings = {"board_id": boards[0]["id"], "board_name": boards[0].get("name")} if boards else {}
    acc = await common.upsert_account(
        session, org_id, user_id, "pinterest", str(u.get("id") or username), name=u.get("business_name") or username,
        handle=username, avatar_url=u.get("profile_image"), profile_url=f"https://www.pinterest.com/{username}/",
        tokens=_tokens(t), expires_at=common.expires_from(t.get("expires_in")), settings=settings,
        status_message=TRIAL_NOTE if boards else "Create a board on Pinterest, then pick it in this account's settings.",
    )
    return [acc]


async def refresh(session, account: SocialAccount) -> None:
    tokens = account_tokens(account)
    if not tokens.get("access_token"):
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    async with net.client() as c:
        if common.expiring(account) and tokens.get("refresh_token"):
            creds = await common.app_credentials(session, "pinterest")
            if not creds:
                raise PublishError("The Pinterest app credentials are missing in Integrations.")
            t = ok_json(await c.post(f"{API}/oauth/token", headers=_basic(creds), data={
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}), "Pinterest token refresh")
            tokens = _tokens(t, tokens)
            common.save_tokens(account, tokens, common.expires_from(t.get("expires_in")))
        ok_json(await c.get(f"{API}/user_account", headers=_bearer(tokens["access_token"])), "Checking the account")


def _b64(asset: MediaAsset) -> dict:
    mime = asset.mime if asset.mime in ("image/jpeg", "image/png") else "image/png"
    return {"content_type": mime, "data": base64.b64encode(common.read_bytes(asset)).decode()}


async def _upload_video(c: httpx.AsyncClient, base: str, tok: str, asset: MediaAsset) -> str:
    reg = ok_json(await c.post(f"{base}/media", headers=_bearer(tok), json={"media_type": "video"}),
                  "Pinterest video upload")
    files = {"file": (common.filename(asset), common.read_bytes(asset), asset.mime or "video/mp4")}
    r = await c.post(reg["upload_url"], data=reg.get("upload_parameters") or {}, files=files)
    common.raise_for(r, "Pinterest video upload")
    mid = str(reg["media_id"])
    for _ in range(MEDIA_POLLS):
        st = ok_json(await c.get(f"{base}/media/{mid}", headers=_bearer(tok)), "Pinterest video status").get("status")
        if st == "succeeded":
            return mid
        if st == "failed":
            raise PublishError("Pinterest couldn't process the video.")
        await common.sleep(MEDIA_POLL_SECONDS)
    raise PublishError("Pinterest is still processing the video. It will be retried.", retryable=True)


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    board = (account.settings or {}).get("board_id")
    if not board:
        raise PublishError("Choose a default Pinterest board in Settings → Social accounts.")
    if not media:
        raise PublishError("Pinterest pins need an image or a video.")
    base = api_base(account)
    body = {"board_id": board, "title": (post.title or post.topic or "")[:100],
            "description": common.compose_text(post)[:500]}
    if post.link_url:
        body["link"] = post.link_url
    if media[0].alt_text:
        body["alt_text"] = media[0].alt_text[:500]
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        videos = [m for m in media if common.is_video(m)]
        images = [m for m in media if not common.is_video(m) and not common.is_pdf(m)]
        if videos:
            mid = await _upload_video(c, base, tok, videos[0])
            src = {"source_type": "video_id", "media_id": mid}
            if images:
                cover = _b64(images[0])
                src["cover_image_content_type"] = cover["content_type"]
                src["cover_image_data"] = cover["data"]
            else:
                src["cover_image_key_frame_time"] = 1
            body["media_source"] = src
        elif len(images) > 1:
            body["media_source"] = {"source_type": "multiple_image_base64", "items": [_b64(m) for m in images[:5]]}
        else:
            body["media_source"] = {"source_type": "image_base64", **_b64(images[0])}
        d = ok_json(await c.post(f"{base}/pins", headers=_bearer(tok), json=body), "Pinterest pin")
    pid = str(d["id"])
    return pid, f"https://www.pinterest.com/pin/{pid}/"


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    tok = account_tokens(account).get("access_token")
    if not tok or not post.external_id:
        return None
    today = datetime.now(UTC).date()
    start = (post.published_at.date() if post.published_at else today - timedelta(days=30))
    start = max(start, today - timedelta(days=89))
    async with net.client() as c:
        d = ok_json(await c.get(f"{api_base(account)}/pins/{post.external_id}/analytics", headers=_bearer(tok), params={
            "start_date": start.isoformat(), "end_date": today.isoformat(),
            "metric_types": "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK,VIDEO_MRC_VIEW"}), "Pinterest analytics")
    s = (d.get("all") or {}).get("summary_metrics") or {}
    return common.metrics(d, impressions=s.get("IMPRESSION"), saves=s.get("SAVE"),
                          clicks=(s.get("PIN_CLICK") or 0) + (s.get("OUTBOUND_CLICK") or 0),
                          video_views=s.get("VIDEO_MRC_VIEW"))
