"""Stock photos and clips: Pexels (photos + videos), Pixabay (images + videos), Unsplash (photos).

- `search(...)` returns normalised preview items without downloading anything.
- `import_item(session, provider, type, item_id)` re-fetches the item from the provider's API by id (never a
  client-supplied URL), downloads it into storage and creates a MediaAsset (source `stock:<provider>`,
  attribution in meta). Unsplash downloads are reported to its download endpoint as its API guidelines require.
- `find_image(...)` / `find_video(...)` = search + import of the best match, for the automatic pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import MediaAsset, ProviderConfig
from app.services.image_gen import http_client
from app.services.llm.catalog import BY_ID

log = logging.getLogger("media.stock")

UTM = "utm_source=hrp_digital_marketing_studio&utm_medium=referral"
MAX_VIDEO_BYTES = 150 * 1024 * 1024


class StockError(Exception):
    pass


@dataclass
class _Cfg:
    cfg: ProviderConfig
    key: str
    base: str

    @property
    def provider(self) -> str:
        return self.cfg.provider


async def providers(session: AsyncSession, provider: str | None = None) -> list[_Cfg]:
    q = select(ProviderConfig).where(ProviderConfig.kind == "stock", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at)
    out = []
    for c in (await session.scalars(q)).all():
        if provider and c.provider != provider:
            continue
        key = decrypt_json(c.secrets_enc).get("api_key") or ""
        spec = BY_ID.get(c.provider)
        if key:
            out.append(_Cfg(c, key, (c.base_url or (spec.base_url if spec else "")).rstrip("/")))
    return out


def _orient(provider: str, orientation: str | None) -> str | None:
    o = (orientation or "").lower()
    if o not in ("landscape", "portrait", "square"):
        return None
    if provider == "pixabay":
        return {"landscape": "horizontal", "portrait": "vertical"}.get(o)
    if provider == "unsplash":
        return {"square": "squarish"}.get(o, o)
    return o


def orientation_for(width: int, height: int) -> str:
    r = width / max(1, height)
    return "landscape" if r > 1.15 else "portrait" if r < 0.87 else "square"


# ----------------------------------------------------------------------------- normalisers
def _pexels_photo(p: dict) -> dict:
    src = p.get("src") or {}
    return {"provider": "pexels", "type": "photo", "id": str(p.get("id")), "width": p.get("width"), "height": p.get("height"),
            "preview_url": src.get("medium") or src.get("small"), "download_url": src.get("original") or src.get("large2x"),
            "author": p.get("photographer"), "author_url": p.get("photographer_url"), "page_url": p.get("url"),
            "alt": p.get("alt") or "", "duration": None,
            "attribution": f"Photo by {p.get('photographer')} on Pexels"}


def _best_video_file(files: list[dict], target_h: int = 1920) -> dict | None:
    mp4 = [f for f in files if (f.get("file_type") or "video/mp4") == "video/mp4" and f.get("link")]
    if not mp4:
        return None
    good = [f for f in mp4 if max(f.get("width") or 0, f.get("height") or 0) <= max(target_h, 1920)]
    pool = good or mp4
    return max(pool, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))


def _pexels_video(v: dict) -> dict:
    f = _best_video_file(v.get("video_files") or []) or {}
    user = v.get("user") or {}
    return {"provider": "pexels", "type": "video", "id": str(v.get("id")), "width": v.get("width"), "height": v.get("height"),
            "preview_url": v.get("image"), "download_url": f.get("link"), "duration": v.get("duration"),
            "author": user.get("name"), "author_url": user.get("url"), "page_url": v.get("url"), "alt": "",
            "attribution": f"Video by {user.get('name')} on Pexels"}


def _pixabay_photo(h: dict) -> dict:
    return {"provider": "pixabay", "type": "photo", "id": str(h.get("id")), "width": h.get("imageWidth"), "height": h.get("imageHeight"),
            "preview_url": h.get("webformatURL") or h.get("previewURL"), "download_url": h.get("largeImageURL") or h.get("webformatURL"),
            "author": h.get("user"), "author_url": f"https://pixabay.com/users/{h.get('user')}-{h.get('user_id')}/",
            "page_url": h.get("pageURL"), "alt": h.get("tags") or "", "duration": None,
            "attribution": f"Image by {h.get('user')} from Pixabay"}


def _pixabay_video(h: dict) -> dict:
    vids = h.get("videos") or {}
    f = vids.get("large") if (vids.get("large") or {}).get("url") else vids.get("medium") or vids.get("small") or {}
    thumb = (vids.get("medium") or {}).get("thumbnail") or (vids.get("large") or {}).get("thumbnail")
    return {"provider": "pixabay", "type": "video", "id": str(h.get("id")), "width": f.get("width"), "height": f.get("height"),
            "preview_url": thumb, "download_url": f.get("url"), "duration": h.get("duration"),
            "author": h.get("user"), "author_url": f"https://pixabay.com/users/{h.get('user')}-{h.get('user_id')}/",
            "page_url": h.get("pageURL"), "alt": h.get("tags") or "", "attribution": f"Video by {h.get('user')} from Pixabay"}


def _unsplash_photo(p: dict) -> dict:
    urls = p.get("urls") or {}
    user = p.get("user") or {}
    links = p.get("links") or {}
    raw = urls.get("raw")
    return {"provider": "unsplash", "type": "photo", "id": str(p.get("id")), "width": p.get("width"), "height": p.get("height"),
            "preview_url": urls.get("small") or urls.get("thumb"),
            "download_url": (f"{raw}&w=2400&fm=jpg&q=85" if raw else urls.get("full") or urls.get("regular")),
            "download_location": links.get("download_location"),
            "author": user.get("name"), "author_url": f"{(user.get('links') or {}).get('html', '')}?{UTM}",
            "page_url": f"{links.get('html', '')}?{UTM}", "alt": p.get("alt_description") or p.get("description") or "",
            "duration": None, "attribution": f"Photo by {user.get('name')} on Unsplash"}


# ----------------------------------------------------------------------------- search
async def _search_one(c: _Cfg, query: str, type_: str, orientation: str | None, per_page: int) -> list[dict]:
    o = _orient(c.provider, orientation)
    async with http_client(20) as http:
        if c.provider == "pexels":
            path = "/videos/search" if type_ == "video" else "/v1/search"
            params = {"query": query, "per_page": per_page, **({"orientation": o} if o else {})}
            r = await http.get(f"{c.base}{path}", params=params, headers={"Authorization": c.key})
            _check(r, "Pexels")
            data = r.json()
            return [_pexels_video(v) for v in data.get("videos", [])] if type_ == "video" else [_pexels_photo(p) for p in data.get("photos", [])]
        if c.provider == "pixabay":
            path = "/videos/" if type_ == "video" else "/"
            params = {"key": c.key, "q": query[:100], "per_page": max(3, min(per_page, 200)), "safesearch": "true",
                      **({"orientation": o} if o and type_ != "video" else {}), **({"image_type": "photo"} if type_ != "video" else {})}
            r = await http.get(f"{c.base}{path}", params=params)
            _check(r, "Pixabay")
            hits = r.json().get("hits", [])
            return [_pixabay_video(h) for h in hits] if type_ == "video" else [_pixabay_photo(h) for h in hits]
        if c.provider == "unsplash":
            if type_ == "video":
                return []
            params = {"query": query, "per_page": per_page, "content_filter": "high", **({"orientation": o} if o else {})}
            r = await http.get(f"{c.base}/search/photos", params=params, headers=_unsplash_h(c))
            _check(r, "Unsplash")
            return [_unsplash_photo(p) for p in r.json().get("results", [])]
    return []


def _unsplash_h(c: _Cfg) -> dict:
    return {"Authorization": f"Client-ID {c.key}", "Accept-Version": "v1"}


def _check(r, label: str) -> None:
    if r.status_code >= 400:
        raise StockError(f"{label}: {r.status_code} {r.text[:200]}")


async def search(session: AsyncSession, query: str, type_: str = "photo", orientation: str | None = None,
                 per_page: int = 12, provider: str | None = None) -> dict:
    """Search all configured stock providers. Returns {"items": [...], "errors": [...], "providers": [...]}."""
    query = (query or "").strip()[:200]
    if not query:
        return {"items": [], "errors": [], "providers": []}
    cfgs = await providers(session, provider)
    items: list[dict] = []
    errors: list[str] = []
    for c in cfgs:
        try:
            items += await _search_one(c, query, type_, orientation, per_page)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{c.cfg.name}: {e}")
    items = [i for i in items if i.get("download_url") or i.get("provider") == "unsplash"]
    return {"items": items, "errors": errors, "providers": [c.provider for c in cfgs]}


# ----------------------------------------------------------------------------- import
async def _fetch_item(c: _Cfg, type_: str, item_id: str) -> dict:
    async with http_client(20) as http:
        if c.provider == "pexels":
            path = f"/videos/videos/{item_id}" if type_ == "video" else f"/v1/photos/{item_id}"
            r = await http.get(f"{c.base}{path}", headers={"Authorization": c.key})
            _check(r, "Pexels")
            return _pexels_video(r.json()) if type_ == "video" else _pexels_photo(r.json())
        if c.provider == "pixabay":
            path = "/videos/" if type_ == "video" else "/"
            r = await http.get(f"{c.base}{path}", params={"key": c.key, "id": item_id})
            _check(r, "Pixabay")
            hits = r.json().get("hits") or []
            if not hits:
                raise StockError("That Pixabay item no longer exists.")
            return _pixabay_video(hits[0]) if type_ == "video" else _pixabay_photo(hits[0])
        if c.provider == "unsplash":
            r = await http.get(f"{c.base}/photos/{item_id}", headers=_unsplash_h(c))
            _check(r, "Unsplash")
            return _unsplash_photo(r.json())
    raise StockError(f"Unsupported stock provider: {c.provider}")


async def import_item(session: AsyncSession, provider: str, type_: str, item_id: str, *, query: str | None = None) -> MediaAsset:
    from app.services import media

    if type_ not in ("photo", "video"):
        raise StockError("Type must be photo or video.")
    cfgs = await providers(session, provider)
    if not cfgs:
        raise StockError(f"{provider} isn't set up. Add it in Settings → Integrations → Stock media.")
    c = cfgs[0]
    item = await _fetch_item(c, type_, str(item_id)[:64])
    url = item.get("download_url")
    if not url:
        raise StockError("This item has no downloadable file.")
    async with http_client(180) as http:
        if c.provider == "unsplash" and item.get("download_location"):
            try:  # Unsplash API guideline: trigger the download endpoint when a photo is used
                await http.get(item["download_location"], headers=_unsplash_h(c))
            except Exception as e:  # noqa: BLE001
                log.info("unsplash download ping failed: %s", e)
        r = await http.get(url)
        _check(r, c.cfg.name)
        data = r.content
    if len(data) > MAX_VIDEO_BYTES:
        raise StockError("That file is too large to import.")
    meta = {"attribution": item.get("attribution"), "author": item.get("author"), "author_url": item.get("author_url"),
            "page_url": item.get("page_url"), "stock_id": item.get("id"), "stock_provider": c.provider}
    if query:
        meta["query"] = query
    ext = "mp4" if type_ == "video" else _img_ext(data)
    return await media.store_bytes(
        session, c.cfg.organization_id, data, ext, source=f"stock:{c.provider}", prompt=query,
        alt_text=(item.get("alt") or query or "")[:500] or None, meta=meta, area="stock")


def _img_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "jpg"


async def find_image(session: AsyncSession, query: str, orientation: str | None = None) -> MediaAsset | None:
    res = await search(session, query, "photo", orientation, per_page=6)
    for item in res["items"][:3]:
        try:
            return await import_item(session, item["provider"], "photo", item["id"], query=query)
        except Exception as e:  # noqa: BLE001
            log.info("stock image import failed: %s", e)
    return None


async def find_video(session: AsyncSession, query: str, orientation: str | None = None, min_duration: float = 0) -> MediaAsset | None:
    res = await search(session, query, "video", orientation, per_page=8)
    items = [i for i in res["items"] if (i.get("duration") or 0) >= min_duration] or res["items"]
    for item in items[:3]:
        try:
            return await import_item(session, item["provider"], "video", item["id"], query=query)
        except Exception as e:  # noqa: BLE001
            log.info("stock video import failed: %s", e)
    return None


async def has_stock(session: AsyncSession) -> bool:
    return bool(await providers(session))


# ----------------------------------------------------------------------------- connection tests
from app.api.integrations import tester  # noqa: E402


def _cfg_for_test(cfg: ProviderConfig) -> _Cfg:
    key = decrypt_json(cfg.secrets_enc).get("api_key") or ""
    if not key:
        raise StockError("Add the API key.")
    spec = BY_ID.get(cfg.provider)
    return _Cfg(cfg, key, (cfg.base_url or (spec.base_url if spec else "")).rstrip("/"))


@tester("pexels", "pixabay", "unsplash")
async def _test_stock(cfg: ProviderConfig) -> str:
    c = _cfg_for_test(cfg)
    items = await _search_one(c, "office", "photo", None, 3)
    return f"Connected. A test search returned {len(items)} photos."
