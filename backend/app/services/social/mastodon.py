"""Mastodon: instance URL + access token (created under Preferences → Development)."""

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens, ok_json

MEDIA_POLLS = 60
MEDIA_POLL_SECONDS = 3


def _instance(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def connect(session, org_id, user_id, instance_url: str, access_token: str) -> SocialAccount:
    from app.services.crawler import UnsafeURLError, check_public_url

    try:  # never let a user-supplied address point the server at internal services
        inst = (await check_public_url(_instance(instance_url))).rstrip("/")
    except UnsafeURLError as e:
        raise PublishError(f"That instance address isn't allowed: {e}") from e
    async with net.client() as c:
        r = await c.get(f"{inst}/api/v1/accounts/verify_credentials", headers=_auth(access_token.strip()))
    if r.status_code in (401, 403):
        raise PublishError("The instance didn't accept that access token. Check it has the write:statuses, "
                           "write:media and read:accounts scopes.", reconnect=True)
    me = ok_json(r, "Mastodon sign-in")
    host = inst.split("://", 1)[-1]
    return await common.upsert_account(
        session, org_id, user_id, "mastodon", f"{host}:{me['id']}", name=me.get("display_name") or me.get("username"),
        handle=f"@{me.get('username')}@{host}", avatar_url=me.get("avatar"), profile_url=me.get("url"),
        tokens={"access_token": access_token.strip(), "instance": inst}, expires_at=None,
        settings={"instance": inst},
    )


def _ctx(account: SocialAccount) -> tuple[str, str]:
    t = account_tokens(account)
    if not t.get("access_token"):
        raise PublishError("No access token stored. Add the account again.", reconnect=True)
    return _instance(t.get("instance") or (account.settings or {}).get("instance", "")), t["access_token"]


async def refresh(session, account: SocialAccount) -> None:
    inst, tok = _ctx(account)
    async with net.client() as c:
        ok_json(await c.get(f"{inst}/api/v1/accounts/verify_credentials", headers=_auth(tok)), "Checking the account")


async def _upload(c: httpx.AsyncClient, inst: str, tok: str, m: MediaAsset) -> str:
    data = {"description": m.alt_text[:1500]} if m.alt_text else {}
    r = await c.post(f"{inst}/api/v2/media", headers=_auth(tok), data=data,
                     files={"file": (common.filename(m), common.read_bytes(m), m.mime or "application/octet-stream")})
    d = ok_json(r, "Mastodon media upload")
    mid = str(d["id"])
    if r.status_code == 202 or not d.get("url"):
        for _ in range(MEDIA_POLLS):
            await common.sleep(MEDIA_POLL_SECONDS)
            pr = await c.get(f"{inst}/api/v1/media/{mid}", headers=_auth(tok))
            if pr.status_code == 206:
                continue
            pd = ok_json(pr, "Mastodon media status")
            if pd.get("url"):
                break
        else:
            raise PublishError("Mastodon is still processing the media. It will be retried.", retryable=True)
    return mid


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    inst, tok = _ctx(account)
    text = common.compose_text(post, link=True)
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        ids = [await _upload(c, inst, tok, m) for m in media[:4] if not common.is_pdf(m)]
        body = {"status": text, "visibility": (account.settings or {}).get("visibility", "public"), "media_ids": ids}
        d = ok_json(await c.post(f"{inst}/api/v1/statuses", json=body,
                                 headers={**_auth(tok), "Idempotency-Key": str(post.id)}), "Mastodon post")
    return str(d["id"]), d.get("url")


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    if not post.external_id:
        return None
    inst, tok = _ctx(account)
    async with net.client() as c:
        d = ok_json(await c.get(f"{inst}/api/v1/statuses/{post.external_id}", headers=_auth(tok)), "Mastodon post")
    return common.metrics(d, likes=d.get("favourites_count"), comments=d.get("replies_count"),
                          shares=d.get("reblogs_count"))
