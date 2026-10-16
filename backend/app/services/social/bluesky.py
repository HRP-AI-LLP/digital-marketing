"""Bluesky (AT Protocol): handle + app password sessions, posts with link/hashtag facets, images,
link cards and video."""

import re
from datetime import UTC, datetime

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens, ok_json

DEFAULT_SERVICE = "https://bsky.social"
MAX_IMAGE_BYTES = 1_000_000
MAX_VIDEO_BYTES = 50_000_000

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+[^\s<>\"')\].,;:!?]", re.IGNORECASE)
TAG_RE = re.compile(r"(?<![\w#&])#([^\s#.,;:!?()\[\]{}\"'<>]*[^\d\s#.,;:!?()\[\]{}\"'<>][^\s#.,;:!?()\[\]{}\"'<>]*)")


def _service(account_or_url) -> str:
    if isinstance(account_or_url, SocialAccount):
        url = account_tokens(account_or_url).get("service") or (account_or_url.settings or {}).get("service")
    else:
        url = account_or_url
    return (url or DEFAULT_SERVICE).rstrip("/")


def _xrpc(service: str, method: str) -> str:
    return f"{service}/xrpc/{method}"


async def create_session(service: str, identifier: str, password: str) -> dict:
    async with net.client() as c:
        r = await c.post(_xrpc(service, "com.atproto.server.createSession"),
                         json={"identifier": identifier, "password": password})
    if r.status_code in (400, 401):
        raise PublishError("Bluesky didn't accept that handle and app password. Check both and try again.",
                           reconnect=True)
    return ok_json(r, "Bluesky sign-in")


async def connect(session, org_id, user_id, handle: str, app_password: str, service: str | None = None) -> SocialAccount:
    svc = _service(service)
    if service:
        from app.services.crawler import UnsafeURLError, check_public_url

        try:  # a custom PDS must be a public address
            svc = (await check_public_url(svc)).rstrip("/")
        except UnsafeURLError as e:
            raise PublishError(f"That server address isn't allowed: {e}") from e
    s = await create_session(svc, handle.strip().lstrip("@"), app_password.strip())
    profile = {}
    try:
        async with net.client() as c:
            r = await c.get(_xrpc(svc, "app.bsky.actor.getProfile"), params={"actor": s["did"]},
                            headers={"Authorization": f"Bearer {s['accessJwt']}"})
            if r.is_success:
                profile = r.json()
    except httpx.HTTPError:
        pass
    return await common.upsert_account(
        session, org_id, user_id, "bluesky", s["did"], name=profile.get("displayName") or s.get("handle") or handle,
        handle=s.get("handle"), avatar_url=profile.get("avatar"),
        profile_url=f"https://bsky.app/profile/{s.get('handle') or s['did']}",
        tokens={"access_jwt": s["accessJwt"], "refresh_jwt": s["refreshJwt"], "app_password": app_password.strip(),
                "identifier": s.get("handle") or handle, "service": svc, "did": s["did"]},
        expires_at=None,
    )


async def _session(c: httpx.AsyncClient, account: SocialAccount) -> dict:
    """Fresh tokens: refreshSession, falling back to a new login with the stored app password."""
    tokens = account_tokens(account)
    svc = _service(account)
    if tokens.get("refresh_jwt"):
        r = await c.post(_xrpc(svc, "com.atproto.server.refreshSession"),
                         headers={"Authorization": f"Bearer {tokens['refresh_jwt']}"})
        if r.is_success:
            d = r.json()
            tokens.update(access_jwt=d["accessJwt"], refresh_jwt=d["refreshJwt"], did=d.get("did", tokens.get("did")))
            common.save_tokens(account, tokens, None)
            return tokens
        if r.status_code >= 500 or r.status_code == 429:
            common.raise_for(r, "Bluesky session")
    if not tokens.get("app_password"):
        raise PublishError("The Bluesky session expired. Add the account again with an app password.", reconnect=True)
    r = await c.post(_xrpc(svc, "com.atproto.server.createSession"),
                     json={"identifier": tokens.get("identifier") or account.handle, "password": tokens["app_password"]})
    if r.status_code in (400, 401):
        raise PublishError("Bluesky rejected the saved app password. Add the account again.", reconnect=True)
    d = ok_json(r, "Bluesky sign-in")
    tokens.update(access_jwt=d["accessJwt"], refresh_jwt=d["refreshJwt"], did=d["did"])
    common.save_tokens(account, tokens, None)
    return tokens


async def refresh(session, account: SocialAccount) -> None:
    async with net.client() as c:
        await _session(c, account)


def _byte_range(text: str, start: int, end: int) -> dict:
    return {"byteStart": len(text[:start].encode("utf-8")), "byteEnd": len(text[:end].encode("utf-8"))}


def facets(text: str) -> list[dict]:
    """Rich-text facets (UTF-8 byte offsets) for links and hashtags."""
    out = []
    for m in URL_RE.finditer(text):
        out.append({"index": _byte_range(text, m.start(), m.end()),
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}]})
    for m in TAG_RE.finditer(text):
        tag = m.group(1)
        if not tag or len(tag) > 64:
            continue
        out.append({"index": _byte_range(text, m.start(), m.end()),
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]})
    return out


async def _upload_blob(c: httpx.AsyncClient, svc: str, jwt: str, data: bytes, mime: str) -> dict:
    d = ok_json(await c.post(_xrpc(svc, "com.atproto.repo.uploadBlob"), content=data,
                             headers={"Authorization": f"Bearer {jwt}", "Content-Type": mime}), "Bluesky upload")
    return d["blob"]


def _aspect(m: MediaAsset) -> dict | None:
    return {"width": m.width, "height": m.height} if m.width and m.height else None


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    text = common.compose_text(post, link=bool(post.link_url and (media or post.post_type != "link")))
    if len(text) > 300:
        raise PublishError(f"Bluesky posts are limited to 300 characters; this one has {len(text)}. Shorten it.")
    record: dict = {"$type": "app.bsky.feed.post", "text": text,
                    "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "langs": ["en"]}
    f = facets(text)
    if f:
        record["facets"] = f
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        tokens = await _session(c, account)
        svc, jwt, did = _service(account), tokens["access_jwt"], tokens.get("did") or account.external_id
        videos = [m for m in media if common.is_video(m)]
        images = [m for m in media if not common.is_video(m) and not common.is_pdf(m)]
        if videos:
            v = videos[0]
            data = common.read_bytes(v)
            if len(data) > MAX_VIDEO_BYTES:
                raise PublishError("Bluesky videos must be under 50 MB.")
            # Uploading the blob straight to the PDS is accepted for video embeds; the dedicated
            # video service (app.bsky.video.uploadVideo) adds pre-processing. Swap it in here if needed.
            blob = await _upload_blob(c, svc, jwt, data, v.mime or "video/mp4")
            emb = {"$type": "app.bsky.embed.video", "video": blob, "alt": (v.alt_text or "")[:1000]}
            if _aspect(v):
                emb["aspectRatio"] = _aspect(v)
            record["embed"] = emb
        elif images:
            items = []
            for m in images[:4]:
                data = common.read_bytes(m)
                if len(data) > MAX_IMAGE_BYTES:
                    raise PublishError("Bluesky images must be under 1 MB. Use a smaller or compressed image.")
                item = {"alt": (m.alt_text or "")[:1000], "image": await _upload_blob(c, svc, jwt, data, m.mime or "image/png")}
                if _aspect(m):
                    item["aspectRatio"] = _aspect(m)
                items.append(item)
            record["embed"] = {"$type": "app.bsky.embed.images", "images": items}
        elif post.link_url:
            record["embed"] = {"$type": "app.bsky.embed.external", "external": {
                "uri": post.link_url, "title": (post.title or post.topic or post.link_url)[:300],
                "description": (post.meta or {}).get("link_description", "")[:1000]}}
        d = ok_json(await c.post(_xrpc(svc, "com.atproto.repo.createRecord"), headers={"Authorization": f"Bearer {jwt}"},
                                 json={"repo": did, "collection": "app.bsky.feed.post", "record": record}),
                    "Bluesky post")
    uri = d.get("uri", "")
    rkey = uri.rsplit("/", 1)[-1] if uri else None
    return uri or None, (f"https://bsky.app/profile/{account.handle or did}/post/{rkey}" if rkey else None)


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    if not post.external_id:
        return None
    async with net.client() as c:
        tokens = await _session(c, account)
        d = ok_json(await c.get(_xrpc(_service(account), "app.bsky.feed.getPosts"), params={"uris": post.external_id},
                                headers={"Authorization": f"Bearer {tokens['access_jwt']}"}), "Bluesky post")
    p = (d.get("posts") or [{}])[0]
    return common.metrics(p, likes=p.get("likeCount"), comments=p.get("replyCount"),
                          shares=(p.get("repostCount") or 0) + (p.get("quoteCount") or 0))
