"""Canva Connect API: OAuth 2.0 with PKCE, browsing / searching the user's designs, and importing designs
into the media library through the export API (POST /v1/exports -> poll -> download).

The connection is stored as a DesignConnection (not a SocialAccount: nothing is posted to Canva).
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json, encrypt_json
from app.models import DesignConnection, MediaAsset
from app.services.social import common, net
from app.services.social.common import PublishError

AUTHORIZE = "https://www.canva.com/api/oauth/authorize"
API = "https://api.canva.com/rest/v1"
SCOPES = ["design:meta:read", "design:content:read", "asset:read", "profile:read"]

EXPORT_POLLS = 60
EXPORT_POLL_SECONDS = 2
MAX_PAGES = 20
FORMATS = {"png": "png", "jpg": "jpg", "pdf": "pdf", "mp4": "mp4", "gif": "gif"}
EXT = {"png": "png", "jpg": "jpg", "pdf": "pdf", "mp4": "mp4", "gif": "gif"}


class CanvaError(PublishError):
    pass


# ------------------------------------------------------------------ errors
def check(r: httpx.Response, what: str) -> dict:
    if r.is_success:
        try:
            return r.json() if r.content else {}
        except ValueError:
            return {}
    try:
        d = r.json()
    except ValueError:
        d = {}
    code = str(d.get("code") or d.get("error") or "") if isinstance(d, dict) else ""
    msg = str((d.get("message") or d.get("error_description") or code) if isinstance(d, dict) else "") or f"HTTP {r.status_code}"
    if r.status_code == 401 or code in ("invalid_access_token", "invalid_grant", "revoked_access_token"):
        raise CanvaError(f"{what}: Canva no longer accepts this connection ({msg[:200]}). Connect Canva again.",
                         reconnect=True)
    if r.status_code == 403:
        raise CanvaError(f"{what}: Canva refused this ({msg[:200]}). Check that your Canva integration has the "
                         "design:content:read and design:meta:read scopes, then connect again.")
    raise CanvaError(f"{what}: {msg[:300]}", retryable=r.status_code == 429 or r.status_code >= 500)


# ------------------------------------------------------------------ OAuth
def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"code_challenge": code_challenge, "code_challenge_method": "s256", "scope": " ".join(SCOPES),
         "response_type": "code", "client_id": creds["client_id"], "state": state, "redirect_uri": redirect_uri}
    return f"{AUTHORIZE}?{urlencode(q)}"


def _basic(creds: dict) -> httpx.BasicAuth:
    return httpx.BasicAuth(creds["client_id"], creds["client_secret"])


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list:
    async with net.client() as c:
        t = check(await c.post(f"{API}/oauth/token", auth=_basic(creds), data={
            "grant_type": "authorization_code", "code": code, "code_verifier": data.get("code_verifier") or "",
            "redirect_uri": redirect_uri}), "Canva sign-in")
        if not t.get("access_token"):
            raise CanvaError("Canva didn't return an access token. Press Connect again.")
        tok = t["access_token"]
        name, ext_id = None, None
        try:
            name = (check(await c.get(f"{API}/users/me/profile", headers=_bearer(tok)), "Canva profile")
                    .get("profile") or {}).get("display_name")
            ext_id = (check(await c.get(f"{API}/users/me", headers=_bearer(tok)), "Canva user")
                      .get("team_user") or {}).get("user_id")
        except (CanvaError, httpx.HTTPError):
            pass
    conn = await session.scalar(select(DesignConnection).where(DesignConnection.provider == "canva"))
    if conn is None:
        conn = DesignConnection(organization_id=org_id, provider="canva")
        session.add(conn)
    conn.external_id, conn.display_name = ext_id, (name or "Canva")[:200]
    conn.status, conn.status_message, conn.connected_by = "connected", None, user_id
    conn.tokens_enc = encrypt_json({"access_token": tok, "refresh_token": t.get("refresh_token"), "scope": t.get("scope")})
    conn.token_expires_at = common.expires_from(t.get("expires_in"))
    await session.flush()
    session.info["connect_summary"] = f"Connected Canva ({conn.display_name})"
    return []


async def connection(session: AsyncSession) -> DesignConnection | None:
    return await session.scalar(select(DesignConnection).where(DesignConnection.provider == "canva"))


def _expiring(conn: DesignConnection, within: timedelta) -> bool:
    exp = conn.token_expires_at
    if exp is None:
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return exp - datetime.now(UTC) < within


async def _save(conn: DesignConnection, tokens: dict, expires_at) -> None:
    """Canva refresh tokens are single use: store the new pair in its own transaction straight away."""
    from app.core.db import SessionLocal, set_tenant

    conn.tokens_enc, conn.token_expires_at = encrypt_json(tokens), expires_at
    try:
        async with SessionLocal() as s:
            set_tenant(s, conn.organization_id)
            await s.execute(text("SET LOCAL lock_timeout = '5s'"))
            await s.execute(update(DesignConnection).where(DesignConnection.id == conn.id)
                            .values(tokens_enc=conn.tokens_enc, token_expires_at=expires_at))
            await s.commit()
    except Exception as e:  # noqa: BLE001
        common.log.warning("couldn't save rotated Canva tokens separately: %s", type(e).__name__)


async def access_token(session: AsyncSession, conn: DesignConnection, c: httpx.AsyncClient) -> str:
    tokens = decrypt_json(conn.tokens_enc)
    if conn.status != "connected" or not tokens.get("access_token"):
        raise CanvaError("Connect Canva first.", reconnect=True)
    if not _expiring(conn, timedelta(minutes=5)):
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise CanvaError("Canva access expired. Connect Canva again.", reconnect=True)
    creds = await common.app_credentials(session, "canva")
    if not creds:
        raise CanvaError("The Canva app credentials are missing in Settings → Integrations.")
    try:
        t = check(await c.post(f"{API}/oauth/token", auth=_basic(creds), data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}), "Canva token refresh")
    except CanvaError as e:
        if e.reconnect:
            conn.status, conn.status_message = "expired", e.message[:500]
        raise
    new = {**tokens, "access_token": t["access_token"], "refresh_token": t.get("refresh_token") or tokens["refresh_token"]}
    await _save(conn, new, common.expires_from(t.get("expires_in")))
    return new["access_token"]


# ------------------------------------------------------------------ designs
def _design_out(d: dict) -> dict:
    th = d.get("thumbnail") or {}
    return {"id": d.get("id"), "title": d.get("title") or "Untitled design", "thumbnail_url": th.get("url"),
            "width": th.get("width"), "height": th.get("height"), "page_count": d.get("page_count") or 1,
            "edit_url": (d.get("urls") or {}).get("edit_url"), "updated_at": d.get("updated_at")}


async def list_designs(session: AsyncSession, query: str | None = None, continuation: str | None = None) -> dict:
    conn = await connection(session)
    if conn is None:
        raise CanvaError("Connect Canva first.", reconnect=True)
    params = {"ownership": "any", "sort_by": "modified_descending"}
    if query:
        params["query"] = query[:200]
    if continuation:
        params["continuation"] = continuation[:2000]
    async with net.client() as c:
        tok = await access_token(session, conn, c)
        d = check(await c.get(f"{API}/designs", params=params, headers=_bearer(tok)), "Canva designs")
    return {"items": [_design_out(x) for x in d.get("items") or [] if x.get("id")], "continuation": d.get("continuation")}


def _format(fmt: str, pages: list[int] | None, design: dict) -> dict:
    f: dict = {"type": FORMATS[fmt]}
    if pages:
        f["pages"] = pages
    if fmt == "jpg":
        f["quality"] = 92
    if fmt == "mp4":
        w, h = design.get("width") or 0, design.get("height") or 0
        f["quality"] = "vertical_1080p" if h > w else "horizontal_1080p"
    return f


_SAFE = re.compile(r"[^A-Za-z0-9 _-]+")


async def import_design(session: AsyncSession, org_id: uuid.UUID, design_id: str, fmt: str = "png",
                        pages: list[int] | None = None, progress=None) -> list[MediaAsset]:
    """Export a design (selected pages) and store every exported file in the media library."""
    from app.services import media as media_svc

    if fmt not in FORMATS:
        raise CanvaError("Choose PNG, JPG, PDF, MP4 or GIF.")
    pages = sorted({int(p) for p in (pages or []) if 1 <= int(p) <= 500})[:MAX_PAGES] or None
    conn = await connection(session)
    if conn is None:
        raise CanvaError("Connect Canva first.", reconnect=True)
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        tok = await access_token(session, conn, c)
        design = _design_out(check(await c.get(f"{API}/designs/{design_id}", headers=_bearer(tok)),
                                   "Canva design").get("design") or {"id": design_id})
        job = check(await c.post(f"{API}/exports", headers=_bearer(tok),
                                 json={"design_id": design_id, "format": _format(fmt, pages, design)}),
                    "Canva export").get("job") or {}
        for _ in range(EXPORT_POLLS):
            if job.get("status") == "success":
                break
            if job.get("status") == "failed":
                err = job.get("error") or {}
                raise CanvaError(f"Canva couldn't export the design: {err.get('message') or err.get('code') or 'export failed'}")
            if progress:
                await progress("Canva is exporting the design…")
            await common.sleep(EXPORT_POLL_SECONDS)
            job = check(await c.get(f"{API}/exports/{job.get('id')}", headers=_bearer(tok)), "Canva export").get("job") or {}
        else:
            raise CanvaError("Canva is still exporting the design. Try again in a minute.", retryable=True)
        urls = [u for u in job.get("urls") or [] if isinstance(u, str) and urlsplit(u).scheme == "https"]
        if not urls:
            raise CanvaError("Canva finished the export but returned no files.")
        title = _SAFE.sub("", design.get("title") or "Canva design").strip()[:60] or "Canva design"
        assets: list[MediaAsset] = []
        for i, url in enumerate(urls[:MAX_PAGES]):
            if progress:
                await progress(f"Saving page {i + 1} of {len(urls)}…")
            r = await c.get(url)
            if not r.is_success:
                raise CanvaError(f"Couldn't download the exported file from Canva (HTTP {r.status_code}).",
                                 retryable=r.status_code >= 500)
            page_no = pages[i] if pages and i < len(pages) else i + 1
            name = f"{title} p{page_no}.{EXT[fmt]}" if len(urls) > 1 else f"{title}.{EXT[fmt]}"
            try:
                a = await media_svc.save_upload(session, org_id, r.content, name)
            except media_svc.MediaUnavailable as e:
                raise CanvaError(str(e))
            a.source = "canva"
            a.alt_text = a.alt_text or design.get("title")
            a.meta = {**(a.meta or {}), "canva_design_id": design_id, "canva_title": design.get("title"),
                      "canva_page": page_no, "canva_edit_url": design.get("edit_url")}
            assets.append(a)
        await session.flush()
    return assets
