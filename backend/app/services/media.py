"""Media studio: builds post media in each platform's format, stores uploads, serves public URLs.

Contract (docs/ARCHITECTURE.md):
  async generate_for_post(session, post, brand) -> list[MediaAsset]
  async ensure_public_url(session, asset) -> str
  asset_out(asset) -> dict
  async save_upload(session, org_id, data, filename) -> MediaAsset
  class MediaUnavailable(Exception)

What gets built per post type (post.meta from the content engine: image_brief, alt_text, slides, video_script):
  image      branded template (photo_headline / stat / quote / tips) over an AI image, a stock photo, or a
             generated brand gradient — in that order of preference, whichever is configured
  carousel   cover + 1–6 content slides + closing slide (max 8 and the platform's max_images), PNGs
  document   the same slides as a PDF (LinkedIn document posts)
  gif        animated text card, <= 6 s and <= 8 MB, with an MP4 twin in meta (mp4_path)
  animation  animated branded template as H.264 MP4
  video      15–45 s slideshow from video_script: stock clips/photos or AI images with Ken Burns, burned-in
             captions, Edge/OpenAI voice-over, optional background music from storage/music/ ducked under voice
Extra files of an asset (poster frame, GIF's MP4 twin) live in meta (*_path) and are served as
`/media/file/{id}?...&v=poster|mp4`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import re
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.config import get_settings
from app.core.crypto import decrypt_json
from app.core.signing import media_url
from app.models import BrandProfile, MediaAsset, Organization, Post, ProviderConfig
from app.services import media_render as render
from app.services.media_templates import (
    TEMPLATES,
    BrandStyle,
    Page,
    default_logo_uri,
    render_html,
)
from app.services.media_templates.base import as_list, data_uri, plain
from app.services.platforms import PLATFORMS

log = logging.getLogger("media")


class MediaUnavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# Motion settings (tests shorten these).
MOTION = {
    "gif": {"seconds": 5.0, "fps": 15, "max_width": 720},
    "animation": {"seconds": 7.0, "fps": 24},
}
VIDEO_FPS = 30
VIDEO_MIN_S, VIDEO_MAX_S = 15.0, 45.0
MAX_SLIDES = 8
DOC_SIZE = (1080, 1350)

UPLOAD_TYPES = {  # ext -> (kind, mime, max bytes)
    "png": ("image", "image/png", 20 * 1024 * 1024),
    "jpg": ("image", "image/jpeg", 20 * 1024 * 1024),
    "webp": ("image", "image/webp", 20 * 1024 * 1024),
    "gif": ("gif", "image/gif", 15 * 1024 * 1024),
    "mp4": ("video", "video/mp4", 300 * 1024 * 1024),
    "mov": ("video", "video/quicktime", 300 * 1024 * 1024),
    "pdf": ("document", "application/pdf", 50 * 1024 * 1024),
}
MIME_BY_EXT = {ext: v[1] for ext, v in UPLOAD_TYPES.items()} | {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav"}


# ============================================================================ storing
def sniff(data: bytes) -> str | None:
    """File type from magic bytes (never trust the extension alone)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        return "mov" if brand == b"qt  " else "mp4"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "mp3"
    return None


def _image_info(data: bytes) -> tuple[int | None, int | None]:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None, None


def _pdf_pages(data: bytes) -> int:
    return max(1, len(re.findall(rb"/Type\s*/Page[^s]", data)))


async def store_bytes(session: AsyncSession, org_id, data: bytes, ext: str, *, source: str, kind: str | None = None,
                      mime: str | None = None, prompt: str | None = None, alt_text: str | None = None, meta: dict | None = None,
                      area: str = "media", width: int | None = None, height: int | None = None,
                      duration: float | None = None) -> MediaAsset:
    ext = ext.lower().lstrip(".")
    kind = kind or (UPLOAD_TYPES.get(ext, ("image",))[0] if ext in UPLOAD_TYPES else "audio" if ext in ("mp3", "m4a", "wav") else "image")
    mime = mime or MIME_BY_EXT.get(ext) or storage.guess_mime(f"x.{ext}")
    rel, sha = await asyncio.to_thread(storage.save_bytes, org_id, area, data, ext)
    if width is None and kind in ("image", "gif"):
        width, height = await asyncio.to_thread(_image_info, data)
    if kind in ("video", "audio") and (width is None or duration is None):
        info = await render.probe(storage.abs_path(rel))
        width = width or info.get("width")
        height = height or info.get("height")
        duration = duration or info.get("duration")
    asset = MediaAsset(organization_id=org_id, kind=kind, mime=mime, path=rel, width=width, height=height,
                       duration_s=round(duration, 3) if duration else None, size_bytes=len(data), sha256=sha,
                       source=source[:60], prompt=prompt, alt_text=alt_text, meta=meta or {})
    session.add(asset)
    await session.flush()
    return asset


async def store_file(session: AsyncSession, org_id, path: Path, ext: str, **kw) -> MediaAsset:
    data = await asyncio.to_thread(Path(path).read_bytes)
    return await store_bytes(session, org_id, data, ext, **kw)


async def _side_file(org_id, data: bytes, ext: str, area: str = "media") -> str:
    rel, _ = await asyncio.to_thread(storage.save_bytes, org_id, area, data, ext)
    return rel


def _abs(rel: str | None) -> Path | None:
    if not rel:
        return None
    try:
        p = storage.abs_path(rel)
    except ValueError:
        return None
    return p if p.exists() else None


def asset_file(asset: MediaAsset, variant: str | None = None) -> tuple[Path | None, str]:
    """(absolute path, mime) of an asset or one of its side files (poster, mp4)."""
    meta = asset.meta or {}
    if variant == "poster":
        rel = meta.get("poster_path")
        if rel:
            return _abs(rel), "image/jpeg" if rel.endswith(".jpg") else "image/png"
        if asset.kind in ("image", "gif"):
            return _abs(asset.path), asset.mime
        return None, ""
    if variant == "mp4":
        rel = meta.get("mp4_path")
        return (_abs(rel), "video/mp4") if rel else (None, "")
    if variant == "jpg":
        if asset.mime == "image/jpeg":
            return _abs(asset.path), asset.mime
        rel = meta.get("jpg_path")
        return (_abs(rel), "image/jpeg") if rel else (None, "")
    return _abs(asset.path), asset.mime


def _to_jpeg(data: bytes) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue()


async def ensure_jpeg(session: AsyncSession, asset: MediaAsset) -> str:
    """Instagram only accepts JPEG images. Returns the variant to link to ("jpg", or "" for JPEG originals).

    The JPEG copy itself is made by the public link the first time it's fetched (make_jpeg), in that request's
    own committed transaction: platforms fetch the link while the publishing transaction is still open, so a copy
    recorded there wouldn't be visible to them yet.
    """
    return "" if asset.mime == "image/jpeg" else "jpg"


async def make_jpeg(session: AsyncSession, asset: MediaAsset) -> None:
    """Create and record the JPEG copy of a PNG/WebP image (caller commits)."""
    if asset.mime == "image/jpeg" or asset_file(asset, "jpg")[0] is not None:
        return
    src = _abs(asset.path)
    if src is None:
        raise MediaUnavailable("The media file is missing; generate it again.")
    data = await asyncio.to_thread(_to_jpeg, src.read_bytes())
    rel = await _side_file(asset.organization_id, data, "jpg")
    asset.meta = {**(asset.meta or {}), "jpg_path": rel}
    await session.flush()


# ============================================================================ API shapes
def asset_out(asset: MediaAsset) -> dict:
    meta = dict(asset.meta or {})
    url = media_url(asset.id, asset.organization_id)
    thumb = url if asset.kind in ("image", "gif") else (f"{url}&v=poster" if meta.get("poster_path") else None)
    out = {
        "id": asset.id, "kind": asset.kind, "mime": asset.mime, "width": asset.width, "height": asset.height,
        "duration_s": asset.duration_s, "size_bytes": asset.size_bytes, "source": asset.source, "prompt": asset.prompt,
        "alt_text": asset.alt_text, "created_at": asset.created_at, "url": url, "thumb_url": thumb,
        "public_url": asset.public_url,
        "meta": {k: v for k, v in meta.items() if not k.endswith("_path")},
    }
    if meta.get("mp4_path"):
        out["mp4_url"] = f"{url}&v=mp4"
    return out


# ============================================================================ uploads
async def save_upload(session: AsyncSession, org_id, data: bytes, filename: str) -> MediaAsset:
    if not data:
        raise MediaUnavailable("The file is empty.")
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    ext = {"jpeg": "jpg", "m4v": "mp4"}.get(ext, ext)
    real = sniff(data)
    if real is None or real not in UPLOAD_TYPES:
        raise MediaUnavailable("Upload PNG, JPG, WebP, GIF, MP4, MOV or PDF files.")
    if ext in UPLOAD_TYPES and {ext, real} not in ({"mp4", "mov"},) and ext != real:
        raise MediaUnavailable(f"“{filename}” looks like a {real.upper()} file, not {ext.upper()}. Rename it or export it again.")
    kind, mime, limit = UPLOAD_TYPES[real]
    if len(data) > limit:
        raise MediaUnavailable(f"“{filename}” is {len(data) / 1048576:.0f} MB; the limit for {real.upper()} is {limit // 1048576} MB.")
    meta: dict = {"filename": filename[:200]}
    width = height = None
    duration = None
    if kind in ("image", "gif"):
        width, height = await asyncio.to_thread(_image_info, data)
        if not width:
            raise MediaUnavailable(f"“{filename}” couldn't be read as an image.")
        if width * height > 60_000_000:
            raise MediaUnavailable("That image is too large (over 60 megapixels).")
    elif kind == "document":
        meta["pages"] = _pdf_pages(data)
    asset = await store_bytes(session, org_id, data, real, source="upload", kind=kind, mime=mime, meta=meta,
                              area="uploads", width=width, height=height, duration=duration)
    if kind == "video":
        if not asset.width:
            raise MediaUnavailable(f"“{filename}” couldn't be read as a video.")
        try:
            tmp = render.tmp_dir("poster-")
            try:
                jpg = await render.poster_frame(storage.abs_path(asset.path), tmp / "poster.jpg", at=min(1.0, (asset.duration_s or 1) / 2))
                asset.meta = {**asset.meta, "poster_path": await _side_file(org_id, jpg.read_bytes(), "jpg", "uploads")}
            finally:
                render.rm_tree(tmp)
        except Exception as e:  # noqa: BLE001
            log.info("poster frame failed: %s", e)
        await session.flush()
    return asset


# ============================================================================ public URLs
def _public_sig(asset_id: str, org_id: str, exp: int, variant: str) -> str:
    msg = f"public:{asset_id}:{org_id}:{exp}:{variant}".encode()
    return hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def public_token(asset_id, org_id, variant: str = "", ttl: int = 90 * 86400) -> str:
    exp = int(time.time()) + ttl
    return f"{uuid.UUID(str(org_id)).hex}.{exp}.{_public_sig(str(asset_id), str(org_id), exp, variant)}"


def verify_public_token(asset_id: str, token: str, variant: str = "") -> uuid.UUID | None:
    try:
        org_hex, exp_s, sig = token.split(".")
        org_id = uuid.UUID(org_hex)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return None
    if exp < time.time():
        return None
    if not hmac.compare_digest(_public_sig(str(asset_id), str(org_id), exp, variant), sig):
        return None
    return org_id


async def _media_hosts(session: AsyncSession) -> list[ProviderConfig]:
    q = select(ProviderConfig).where(ProviderConfig.kind == "media_host", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at)
    return list((await session.scalars(q)).all())


NO_HOST = ("Instagram/Threads/Google need a public media host — add Cloudinary or a public URL in Settings → Integrations.")


async def ensure_public_url(session: AsyncSession, asset: MediaAsset, variant: str | None = None) -> str:
    """Public https URL for an asset (or its mp4 twin with variant='mp4'), cached in the asset."""
    variant = variant or ""
    meta = dict(asset.meta or {})
    cached = (meta.get("public_urls") or {}).get(variant) if variant else asset.public_url
    # Cloudinary URLs are permanent. Self-hosted URLs carry an expiring token and a base URL that may
    # change (tunnels), so they're rebuilt every time (cheap, no network).
    if cached and "cloudinary.com/" in cached:
        return cached
    path, mime = asset_file(asset, variant or None)
    hosts = await _media_hosts(session)
    if path is None and variant == "jpg" and _abs(asset.path) is not None:
        if any(h.provider == "cloudinary" for h in hosts):
            await make_jpeg(session, asset)  # Cloudinary uploads the file now, so it has to exist
            path, mime = asset_file(asset, "jpg")
        else:
            path, mime = _abs(asset.path), "image/jpeg"  # self-hosted link makes the copy on first fetch
    if path is None:
        raise MediaUnavailable("The media file is missing; generate it again.")
    url = None
    for h in hosts:
        sec = decrypt_json(h.secrets_enc)
        if h.provider == "cloudinary" and sec.get("cloud_name") and sec.get("api_key") and sec.get("api_secret"):
            url = await _cloudinary_upload(sec, asset, path, mime, variant)
            break
        if h.provider == "public_base_url":
            base = (sec.get("public_base_url") or (h.settings or {}).get("public_base_url") or h.base_url or "").strip()
            if base:
                url = _self_hosted_url(base, asset, variant)
                break
    if url is None and (get_settings().public_base_url or "").strip():
        url = _self_hosted_url(get_settings().public_base_url, asset, variant)
    if url is None:
        raise MediaUnavailable(NO_HOST)
    if variant:
        meta["public_urls"] = {**(meta.get("public_urls") or {}), variant: url}
        asset.meta = meta
    else:
        asset.public_url = url
    await session.flush()
    return url


def _self_hosted_url(base: str, asset: MediaAsset, variant: str) -> str:
    base = base.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    base = base.removesuffix("/api/v1")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif", "video/mp4": "mp4",
           "video/quicktime": "mov", "application/pdf": "pdf"}.get(
        "video/mp4" if variant == "mp4" else "image/jpeg" if variant == "jpg" else asset.mime, "")
    q = f"?v={variant}" if variant else ""
    tok = public_token(asset.id, asset.organization_id, variant)
    return f"{base}/api/v1/media/public/{asset.id}/{tok}{('.' + ext) if ext else ''}{q}"


async def _cloudinary_upload(sec: dict, asset: MediaAsset, path: Path, mime: str, variant: str) -> str:
    from app.services.image_gen import http_client

    rtype = "video" if mime.startswith("video/") else "image" if (mime.startswith("image/") or mime == "application/pdf") else "raw"
    ts = int(time.time())
    public_id = f"hrp-dm/{asset.organization_id}/{asset.id}{('-' + variant) if variant else ''}"
    params = {"public_id": public_id, "timestamp": str(ts), "overwrite": "true"}
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params))
    signature = hashlib.sha1((to_sign + sec["api_secret"]).encode()).hexdigest()
    data = await asyncio.to_thread(path.read_bytes)
    async with http_client(300) as http:
        r = await http.post(f"https://api.cloudinary.com/v1_1/{sec['cloud_name']}/{rtype}/upload",
                            data={**params, "api_key": sec["api_key"], "signature": signature},
                            files={"file": (path.name, data, mime)})
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message")
            except Exception:  # noqa: BLE001
                msg = r.text[:200]
            raise MediaUnavailable(f"Cloudinary upload failed: {msg}. Check the Cloudinary keys in Settings → Integrations.")
        return r.json()["secure_url"]


# ============================================================================ brand
def _site(url: str | None) -> str | None:
    if not url:
        return None
    return re.sub(r"^https?://(www\.)?", "", url.strip()).rstrip("/")[:60] or None


async def brand_style(session: AsyncSession, brand: BrandProfile | None, org_id) -> BrandStyle:
    org = await session.get(Organization, org_id)
    settings = (brand.settings if brand else None) or {}
    name = settings.get("display_name") or (org.name if org else "")
    name = re.sub(r"\s+(LLP|Pvt\.? Ltd\.?|Private Limited|Ltd\.?|LLC|Inc\.?)$", "", name or "", flags=re.I).strip() or "Brand"
    logo = None
    if brand and brand.logo_asset_id:
        la = await session.get(MediaAsset, brand.logo_asset_id)
        p = _abs(la.path) if la and la.kind in ("image", "gif") else None
        if p:
            logo = data_uri(await asyncio.to_thread(p.read_bytes), la.mime)
    if logo is None and org and org.slug == get_settings().default_org_slug:
        logo = default_logo_uri()  # HRP AI's own mark; other tenants get a monogram until they upload a logo
    return BrandStyle.from_profile(
        colors=(brand.colors if brand else None), fonts=(brand.fonts if brand else None), name=name,
        website=_site(settings.get("website") or (org.website if org else None)), handle=settings.get("handle"), logo_uri=logo)


async def load_brand(session: AsyncSession) -> BrandProfile | None:
    return await session.scalar(select(BrandProfile))


# ============================================================================ helpers
def _size(meta: dict, default: tuple[int, int]) -> tuple[int, int]:
    s = meta.get("size")
    if isinstance(s, str) and re.fullmatch(r"\d{2,4}x\d{2,4}", s):
        w, h = (int(x) for x in s.split("x"))
        if 64 <= w <= 4096 and 64 <= h <= 4096:
            return w, h
    if isinstance(s, (list, tuple)) and len(s) == 2:
        try:
            w, h = int(s[0]), int(s[1])
            if 64 <= w <= 4096 and 64 <= h <= 4096:
                return w, h
        except (TypeError, ValueError):
            pass
    return default


def _first_sentence(text: str | None, limit: int = 90) -> str:
    t = re.sub(r"#\w+", "", plain(text or "")).strip()
    if not t:
        return ""
    first = re.split(r"(?<=[.!?])\s+|\n", t, maxsplit=1)[0].strip()
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0] + "…"
    return first


_STOP = set("a an the and or of to for in on with at by from is are be this that your our we you it as how why what into about "
            "can will more most best new using use".split())


def keywords(text: str, n: int = 5) -> str:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", plain(text).lower()) if w not in _STOP]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    return " ".join(seen[:n]) or "technology office"


def _jpeg_data_uri(data: bytes, width: int, height: int) -> str:
    from PIL import Image

    from app.services.image_gen import cover_fit

    png = cover_fit(data, width, height)
    im = Image.open(io.BytesIO(png)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return data_uri(buf.getvalue(), "image/jpeg")


async def _asset_bytes(asset: MediaAsset) -> bytes | None:
    p = _abs(asset.path)
    return await asyncio.to_thread(p.read_bytes) if p else None


async def background(session: AsyncSession, org_id, brief: str, width: int, height: int, mode: str = "auto",
                     asset_id: str | None = None) -> tuple[str | None, MediaAsset | None, str]:
    """Background photo for templates -> (data URI | None, asset | None, note).

    mode: auto (AI if configured, else stock, else gradient) | ai | stock | gradient.
    """
    from app.services import image_gen, stock

    if asset_id:
        try:
            a = await session.get(MediaAsset, uuid.UUID(str(asset_id)))
        except ValueError:
            a = None
        if a and a.kind == "image":
            raw = await _asset_bytes(a)
            if raw:
                return await asyncio.to_thread(_jpeg_data_uri, raw, width, height), a, "library"
    mode = (mode or "auto").lower()
    if mode in ("gradient", "none", "brand"):
        return None, None, "gradient"
    notes = []
    if mode in ("auto", "ai") and await image_gen.has_image_provider(session):
        try:
            orient = "portrait" if height > width * 1.1 else "landscape" if width > height * 1.1 else "square"
            prompt, negative = await image_gen.build_prompt(session, brief, orientation=orient)
            img = await image_gen.generate(session, prompt, width, height, negative=negative)
            a = await store_bytes(session, org_id, img.data, "png", source=f"ai:{img.provider}", prompt=prompt,
                                  alt_text=plain(brief)[:300] or None, meta={"model": img.model, "negative": negative}, area="ai")
            return await asyncio.to_thread(_jpeg_data_uri, img.data, width, height), a, "ai"
        except Exception as e:  # noqa: BLE001
            notes.append(f"AI image failed: {e}")
            log.info("AI background failed: %s", e)
    if mode in ("auto", "stock", "ai") and await stock.has_stock(session):
        try:
            orient = stock.orientation_for(width, height)
            a = await stock.find_image(session, keywords(brief), orient)
            if a:
                raw = await _asset_bytes(a)
                if raw:
                    return await asyncio.to_thread(_jpeg_data_uri, raw, width, height), a, "stock"
        except Exception as e:  # noqa: BLE001
            notes.append(f"Stock photo failed: {e}")
    return None, None, "gradient" + (f" ({'; '.join(notes)[:300]})" if notes else "")


async def render_template(name: str, fields: dict, width: int, height: int, style: BrandStyle,
                          photo: str | None = None, theme: str | None = None) -> bytes:
    html = render_html(name, fields, width, height, style, photo=photo, theme=theme)
    spec = TEMPLATES[name]
    return await render.render_png(html, width, height, transparent=spec.name == "video_caption_overlay")


def _alt(post: Post, meta: dict, fallback: str) -> str:
    return (meta.get("alt_text") or fallback or post.title or "")[:500]


# ============================================================================ builders
def _image_template(meta: dict) -> str:
    t = meta.get("template")
    if t in TEMPLATES and "image" in TEMPLATES[t].kinds:
        return t
    if meta.get("stat"):
        return "stat_card"
    if meta.get("quote"):
        return "quote_card"
    if as_list(meta.get("bullets")):
        return "tip_card"
    return "photo_headline"


def _image_fields(post: Post, meta: dict) -> dict:
    headline = meta.get("headline") or post.title or _first_sentence(post.text)
    return {
        "headline": headline, "title": headline, "body": meta.get("subheadline") or meta.get("body") or "",
        "eyebrow": meta.get("eyebrow") or "", "cta": meta.get("cta") or "", "bullets": meta.get("bullets"),
        "stat": meta.get("stat"), "stat_label": meta.get("stat_label") or headline, "source": meta.get("source") or "",
        "quote": meta.get("quote"), "author": meta.get("author"), "role": meta.get("role"), "date": meta.get("date"),
        "theme": meta.get("theme"),
    }


async def _make_image(session, post: Post, meta: dict, style: BrandStyle, size: tuple[int, int]) -> MediaAsset:
    w, h = size
    tname = _image_template(meta)
    fields = _image_fields(post, meta)
    photo, bg_asset, note = None, None, "gradient"
    if TEMPLATES[tname].photo:
        brief = meta.get("image_brief") or fields["headline"] or post.topic or ""
        photo, bg_asset, note = await background(session, post.organization_id, brief, w, h,
                                                 meta.get("background") or "auto", meta.get("background_asset_id"))
    png = await render_template(tname, fields, w, h, style, photo=photo, theme=fields.get("theme"))
    m = {"template": tname, "background": note.split(" ")[0], "platform": post.platform}
    if bg_asset:
        m["background_asset_id"] = str(bg_asset.id)
    if note.startswith("gradient ("):
        m["background_note"] = note
    return await store_bytes(session, post.organization_id, png, "png", source=f"template:{tname}", kind="image",
                             alt_text=_alt(post, meta, plain(fields["headline"] or fields.get("quote") or fields.get("stat_label") or "")), meta=m, width=w, height=h,
                             prompt=bg_asset.prompt if bg_asset else None)


def _slides(post: Post, meta: dict, max_total: int) -> tuple[dict, list[dict], dict]:
    raw = meta.get("slides") or []
    items: list[dict] = []
    for s in raw if isinstance(raw, list) else []:
        if isinstance(s, dict):
            heading = s.get("heading") or s.get("title") or ""
            body = s.get("body") or s.get("text") or ""
            if heading or body:
                items.append({"heading": heading, "body": body, "bullets": s.get("bullets")})
        elif isinstance(s, str) and s.strip():
            items.append({"heading": s.strip(), "body": ""})
    if not items:  # derive from the post text: one slide per paragraph
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n", plain(post.text or "")) if p.strip() and not p.strip().startswith("#")]
        for p in paras[1:7]:
            head = _first_sentence(p, 70)
            rest = p[len(head):].strip() if p.startswith(head.rstrip("…")) else ""
            items.append({"heading": head, "body": rest})
    max_content = max(1, min(6, max_total - 2))
    items = items[:max_content]
    cover = {"headline": meta.get("headline") or post.title or _first_sentence(post.text) or (items[0]["heading"] if items else ""),
             "body": meta.get("subtitle") or meta.get("subheadline") or "", "eyebrow": meta.get("eyebrow") or ""}
    cta = {"heading": meta.get("cta_heading") or "Found this useful?",
           "body": meta.get("cta_body") or "Save it for later and share it with your team.", "cta": meta.get("cta") or ""}
    return cover, items, cta


async def _render_slides(post: Post, meta: dict, style: BrandStyle, size: tuple[int, int], max_total: int,
                         cover_photo: str | None = None) -> list[tuple[str, bytes, str]]:
    cover, items, cta = _slides(post, meta, max_total)
    total = len(items) + 2
    w, h = size
    jobs = [("carousel_cover", {**cover, "total": total}, cover_photo, plain(cover["headline"]))]
    for i, s in enumerate(items):
        jobs.append(("carousel_slide", {**s, "index": i + 2, "total": total, "number": i + 1, "theme": meta.get("slide_theme")},
                     None, plain(f"{s['heading']}. {s.get('body') or ''}")))
    jobs.append(("carousel_cta", {**cta, "index": total, "total": total}, None, plain(cta["heading"])))
    pngs = await asyncio.gather(*[render_template(n, f, w, h, style, photo=p, theme=f.get("theme")) for n, f, p, _ in jobs])
    return [(n, png, alt) for (n, _, _, alt), png in zip(jobs, pngs)]


async def _make_carousel(session, post: Post, meta: dict, style: BrandStyle, size: tuple[int, int], max_images: int) -> list[MediaAsset]:
    w, h = size
    photo = None
    if meta.get("image_brief") and (meta.get("background") or "auto") != "gradient":
        photo, _, _ = await background(session, post.organization_id, meta["image_brief"], w, h, meta.get("background") or "auto",
                                       meta.get("background_asset_id"))
    slides = await _render_slides(post, meta, style, size, min(MAX_SLIDES, max(3, max_images)), cover_photo=photo)
    group = uuid.uuid4().hex[:12]
    alts = meta.get("slide_alt_texts") or []
    out = []
    for i, (name, png, alt) in enumerate(slides):
        a = alts[i] if i < len(alts) and alts[i] else (meta.get("alt_text") if i == 0 and meta.get("alt_text") else alt)
        out.append(await store_bytes(session, post.organization_id, png, "png", source=f"template:{name}", kind="image",
                                     alt_text=str(a)[:500], width=w, height=h,
                                     meta={"group": group, "index": i + 1, "total": len(slides), "template": name, "platform": post.platform}))
    return out


async def _make_document(session, post: Post, meta: dict, style: BrandStyle) -> MediaAsset:
    size = _size(meta, DOC_SIZE)
    slides = await _render_slides(post, meta, style, size, MAX_SLIDES)
    pdf = await render.pngs_to_pdf([png for _, png, _ in slides])
    poster = await _side_file(post.organization_id, slides[0][1], "png")
    title = plain(meta.get("headline") or post.title or _first_sentence(post.text))
    return await store_bytes(session, post.organization_id, pdf, "pdf", source="template:document", kind="document",
                             mime="application/pdf", alt_text=_alt(post, meta, title), width=size[0], height=size[1],
                             meta={"pages": len(slides), "poster_path": poster, "title": title[:200], "platform": post.platform})


def _motion_fields(post: Post, meta: dict) -> dict:
    lines = as_list(meta.get("lines"), 5)
    if not lines:
        head = meta.get("headline") or post.title or _first_sentence(post.text, 70)
        lines = [head] if head else []
        extra = [s.get("heading") for s in (meta.get("slides") or []) if isinstance(s, dict) and s.get("heading")]
        extra = extra or as_list(meta.get("bullets"))
        if not extra and post.text:
            sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", plain(post.text)) if 8 < len(x.strip()) < 90]
            extra = sentences[1:3]
        lines += [x for x in extra[:2] if x and x != head]
    return {"lines": lines, "eyebrow": meta.get("eyebrow") or "", "cta": meta.get("cta") or ""}


async def _make_motion(session, post: Post, meta: dict, style: BrandStyle, size: tuple[int, int], gif: bool) -> MediaAsset:
    cfg = MOTION["gif" if gif else "animation"]
    w, h = size
    if gif and w > cfg["max_width"]:
        h = int(round(h * cfg["max_width"] / w))
        w = cfg["max_width"]
    w, h = w - w % 2, h - h % 2
    seconds = min(6.0, cfg["seconds"]) if gif else cfg["seconds"]
    fields = {**_motion_fields(post, meta), "duration": seconds}
    photo = None
    if meta.get("background_asset_id"):
        photo, _, _ = await background(session, post.organization_id, "", w, h, "gradient", meta.get("background_asset_id"))
    html = render_html("animated_text", fields, w, h, style, photo=photo, theme=meta.get("theme"))
    tmp = render.tmp_dir("motion-")
    try:
        mp4 = await render.render_video(html, w, h, seconds, tmp / "anim.mp4", fps=cfg["fps"])
        poster = await render.poster_frame(mp4, tmp / "poster.jpg", at=max(0.1, seconds - 2.2))
        poster_rel = await _side_file(post.organization_id, poster.read_bytes(), "jpg")
        alt = _alt(post, meta, " ".join(plain(x) for x in fields["lines"]))
        if gif:
            g = await render.mp4_to_gif(mp4, tmp / "anim.gif", max_width=cfg["max_width"], fps=cfg["fps"], max_seconds=seconds)
            mp4_rel = await _side_file(post.organization_id, mp4.read_bytes(), "mp4")
            return await store_file(session, post.organization_id, g, "gif", source="template:animated_text", kind="gif",
                                    mime="image/gif", alt_text=alt, duration=seconds,
                                    meta={"mp4_path": mp4_rel, "poster_path": poster_rel, "template": "animated_text",
                                          "platform": post.platform, "fps": cfg["fps"]})
        return await store_file(session, post.organization_id, mp4, "mp4", source="template:animated_text", kind="video",
                                mime="video/mp4", alt_text=alt, width=w, height=h, duration=seconds,
                                meta={"poster_path": poster_rel, "template": "animated_text", "animated": True,
                                      "platform": post.platform, "fps": cfg["fps"]})
    finally:
        render.rm_tree(tmp)


# ---------------------------------------------------------------------------- video
def _script(post: Post, meta: dict) -> list[dict]:
    scenes = []
    for s in meta.get("video_script") or []:
        if isinstance(s, dict) and (s.get("caption") or s.get("voiceover")):
            scenes.append({"caption": str(s.get("caption") or "")[:220], "voiceover": str(s.get("voiceover") or "")[:600],
                           "query": s.get("query") or s.get("visual") or s.get("broll") or ""})
    if not scenes:
        for s in (meta.get("slides") or [])[:6]:
            if isinstance(s, dict) and s.get("heading"):
                scenes.append({"caption": s["heading"], "voiceover": f"{s['heading']}. {s.get('body') or ''}".strip(), "query": ""})
    if not scenes and post.text:
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", plain(re.sub(r"#\w+", "", post.text))) if len(x.strip()) > 8]
        for x in sentences[:5]:
            scenes.append({"caption": _first_sentence(x, 90), "voiceover": x, "query": ""})
    return scenes[:8]


def music_files() -> list[Path]:
    d = storage.root() / "music"
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".aac", ".wav", ".ogg") and p.is_file())


async def _mesh_png(style: BrandStyle, w: int, h: int, key: str, path: Path) -> Path:
    page = Page(w, h, style, theme="dark")
    html = page.document(f"<div class='canvas'>{page.mesh(key)}</div>")
    path.write_bytes(await render.render_png(html, w, h))
    return path


async def _scene_visual(session, org_id, query: str, w: int, h: int, mode: str, tmp: Path, i: int,
                        min_duration: float) -> tuple[Path | None, str, str]:
    """(file, 'image'|'video', source) for a scene."""
    from app.services import image_gen, stock

    orient = stock.orientation_for(w, h)
    if mode in ("auto", "stock") and await stock.has_stock(session):
        try:
            a = await stock.find_video(session, query, orient, min_duration=min_duration)
            if a and _abs(a.path):
                return _abs(a.path), "video", a.source
        except Exception as e:  # noqa: BLE001
            log.info("stock clip failed: %s", e)
    if mode in ("auto", "ai") and await image_gen.has_image_provider(session):
        try:
            prompt, negative = await image_gen.build_prompt(session, query, orientation="portrait" if h > w else "landscape")
            img = await image_gen.generate(session, prompt, w, h, negative=negative)
            a = await store_bytes(session, org_id, img.data, "png", source=f"ai:{img.provider}", prompt=prompt,
                                  alt_text=query[:300], meta={"model": img.model}, area="ai")
            return _abs(a.path), "image", a.source
        except Exception as e:  # noqa: BLE001
            log.info("AI scene image failed: %s", e)
    if mode in ("auto", "stock", "ai") and await stock.has_stock(session):
        try:
            a = await stock.find_image(session, query, orient)
            if a and _abs(a.path):
                return _abs(a.path), "image", a.source
        except Exception as e:  # noqa: BLE001
            log.info("stock photo failed: %s", e)
    return None, "image", "gradient"


async def _make_video(session, post: Post, meta: dict, style: BrandStyle, size: tuple[int, int]) -> MediaAsset:
    from app.services import tts, video

    w, h = size
    w, h = w - w % 2, h - h % 2
    script = _script(post, meta)
    if not script:
        raise MediaUnavailable("This video has no script. Add scenes with captions and voice-over text.")
    tmp = render.tmp_dir("video-")
    try:
        # 1. voice-over per scene (all or nothing, so pacing stays consistent)
        voice = meta.get("voice") or None
        voice_on = meta.get("voiceover", True) is not False
        clips: list[tts.TtsResult | None] = [None] * len(script)
        voice_note = None
        if voice_on and any(s["voiceover"] for s in script):
            try:
                for i, s in enumerate(script):
                    if s["voiceover"]:
                        clips[i] = await tts.synthesize(session, s["voiceover"], tmp / f"vo_{i:02d}.mp3", voice=voice)
            except Exception as e:  # noqa: BLE001
                voice_note = f"Voice-over unavailable: {str(e)[:200]}"
                log.info(voice_note)
                clips = [None] * len(script)
        durations = []
        for s, c in zip(script, clips):
            d = (c.duration + 0.6) if c else min(6.0, max(2.8, len(plain(s["caption"])) / 13 + 1.2))
            durations.append(d)
        outro_d = 2.6
        total = sum(durations) + outro_d
        if total < VIDEO_MIN_S:  # stretch to the minimum length
            k = (VIDEO_MIN_S - outro_d) / max(0.1, sum(durations))
            durations = [d * k for d in durations]
        elif total > VIDEO_MAX_S and not any(clips):
            k = (VIDEO_MAX_S - outro_d) / sum(durations)
            durations = [max(2.0, d * k) for d in durations]
        # 2. visuals + caption overlays
        mode = meta.get("visuals") or meta.get("background") or "auto"
        title = plain(meta.get("headline") or post.title or "")
        scenes: list[video.Scene] = []
        sources = []
        ai_clip_budget = 3 if meta.get("ai_video") else 0
        for i, (s, d) in enumerate(zip(script, durations)):
            query = s["query"] or keywords(f"{s['caption']} {s['voiceover']} {meta.get('image_brief') or ''}")
            vis, vkind, src = None, "image", "gradient"
            if ai_clip_budget and await video.video_provider(session):
                try:
                    vis = await video.replicate_generate(session, f"{query}. Cinematic, no text.", tmp / f"ai_{i:02d}.mp4",
                                                         aspect_ratio="9:16" if h > w else "16:9")
                    vkind, src = "video", "ai:replicate"
                    ai_clip_budget -= 1
                except Exception as e:  # noqa: BLE001
                    log.info("AI clip failed: %s", e)
                    vis = None
            if vis is None:
                vis, vkind, src = await _scene_visual(session, post.organization_id, query, w, h, mode, tmp, i, d)
            if vis is None:
                vis = await _mesh_png(style, w, h, f"{post.id}-{i}", tmp / f"mesh_{i:02d}.png")
            sources.append(src)
            overlay = tmp / f"ov_{i:02d}.png"
            overlay.write_bytes(await render_template(
                "video_caption_overlay", {"caption": s["caption"], "title": title if i == 0 else "", "index": i + 1,
                                          "total": len(script)}, w, h, style))
            scenes.append(video.Scene(duration=d, visual=vis, visual_kind=vkind, overlay=overlay,
                                      zoom=("in", "right", "out", "left")[i % 4]))
        # outro: branded closing card
        outro = tmp / "outro.png"
        outro.write_bytes(await render_template("carousel_cta", {
            "heading": meta.get("cta_heading") or title or style.name, "body": meta.get("cta_body") or (style.website or ""),
            "cta": meta.get("cta") or "", "index": 1, "total": 1}, w, h, style))
        scenes.append(video.Scene(duration=outro_d, visual=outro, visual_kind="image", zoom="in"))
        # 3. audio: voice clips at scene starts (+ crossfade offsets), optional music
        starts, t = [], 0.0
        for d in durations:
            starts.append(t + 0.25)
            t += d
        voice_clips = [(c.path, st) for c, st in zip(clips, starts) if c]
        music = None
        if meta.get("music") != "none":
            files = music_files()
            if meta.get("music") and (storage.root() / "music" / Path(str(meta["music"])).name).exists():
                music = storage.root() / "music" / Path(str(meta["music"])).name
            elif files:
                music = files[int(hashlib.sha1(str(post.id or post.title).encode()).hexdigest(), 16) % len(files)]
        out = tmp / "video.mp4"
        info = await video.build_video(scenes, out, w, h, work=tmp, voice_clips=voice_clips, music=music, fps=VIDEO_FPS)
        poster = await render.poster_frame(out, tmp / "poster.jpg", at=min(1.2, info["duration"] / 3))
        poster_rel = await _side_file(post.organization_id, poster.read_bytes(), "jpg")
        captions = [s["caption"] for s in script]
        m = {"poster_path": poster_rel, "scenes": len(script), "visual_sources": sources, "platform": post.platform,
             "voice": clips[0].voice if any(clips) else None, "voice_provider": next((c.provider for c in clips if c), None),
             "music": music.name if music else None, "captions": captions}
        if voice_note:
            m["voice_note"] = voice_note
        return await store_file(session, post.organization_id, out, "mp4", source="template:video", kind="video",
                                mime="video/mp4", alt_text=_alt(post, meta, " ".join(plain(c) for c in captions)[:500]),
                                width=info["width"], height=info["height"], duration=info["duration"], meta=m)
    finally:
        render.rm_tree(tmp)


# ============================================================================ contract entry point
async def generate_for_post(session: AsyncSession, post: Post, brand: BrandProfile | None) -> list[MediaAsset]:
    """Build the media for `post.post_type` in the platform's format. Returns flushed MediaAsset rows."""
    kind = (post.post_type or "text").lower()
    if kind in ("text", "link"):
        return []
    plat = PLATFORMS.get(post.platform) or PLATFORMS["manual"]
    meta = dict(post.meta or {})
    style = await brand_style(session, brand, post.organization_id)
    try:
        if kind == "image":
            return [await _make_image(session, post, meta, style, _size(meta, plat.image_size))]
        if kind == "carousel":
            return await _make_carousel(session, post, meta, style, _size(meta, plat.image_size), plat.max_images)
        if kind == "document":
            return [await _make_document(session, post, meta, style)]
        if kind == "gif":
            return [await _make_motion(session, post, meta, style, _size(meta, plat.image_size), gif=True)]
        if kind == "animation":
            default = plat.image_size if "image" in plat.post_types else plat.video_size
            return [await _make_motion(session, post, meta, style, _size(meta, default), gif=False)]
        if kind == "video":
            return [await _make_video(session, post, meta, style, _size(meta, plat.video_size))]
    except MediaUnavailable:
        raise
    except render.RenderError as e:
        raise MediaUnavailable(str(e)) from e
    raise MediaUnavailable(f"Media for '{kind}' posts isn't supported.")


async def generate_ai_image(session: AsyncSession, org_id, prompt: str, width: int, height: int, *, enhance: bool = True) -> MediaAsset:
    """Media studio 'AI image' tab: one image from a prompt, stored in the library."""
    from app.services import image_gen

    if not await image_gen.has_image_provider(session):
        raise MediaUnavailable("No image provider is set up. Add one in Settings → Integrations → Images.")
    if enhance:
        full, negative = await image_gen.build_prompt(session, prompt, orientation="portrait" if height > width else "landscape")
    else:
        full, negative = f"{prompt}, no text, no watermark", image_gen.NEGATIVE
    try:
        img = await image_gen.generate(session, full, width, height, negative=negative)
    except image_gen.ImageGenError as e:
        raise MediaUnavailable(str(e)) from e
    return await store_bytes(session, org_id, img.data, "png", source=f"ai:{img.provider}", prompt=full, kind="image",
                             alt_text=plain(prompt)[:300], meta={"model": img.model, "negative": negative, "user_prompt": prompt[:1000]},
                             area="ai", width=width, height=height)


async def capabilities(session: AsyncSession) -> dict:
    """Which optional providers are configured (for the UI's empty states)."""
    from app.services import image_gen, stock, video

    hosts = await _media_hosts(session)
    tts_cfgs = (await session.scalars(select(ProviderConfig.provider).where(
        ProviderConfig.kind == "tts", ProviderConfig.enabled.is_(True)))).all()
    stock_cfgs = await stock.providers(session)
    return {
        "image": bool(await image_gen.image_chain(session)),
        "stock": [c.provider for c in stock_cfgs],
        "tts": sorted(set(tts_cfgs) | {"edge_tts"}),
        "video": bool(await video.video_provider(session)),
        "media_host": bool(hosts) or bool((get_settings().public_base_url or "").strip()),
        "renderer": render.chromium_path() or "playwright",
        "music": [p.name for p in music_files()],
    }
