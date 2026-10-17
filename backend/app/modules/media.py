"""Media background module: the `media.generate` job, connection testers for image/stock/voice/video
providers, and a periodic clean-up of stale temp files."""

from __future__ import annotations

import logging
import shutil
import time
import uuid

from app.core import storage
from app.models import Post
from app.services import (  # noqa: F401 - importing registers their @tester functions
    image_gen,
    stock,
    tts,
    video,
)
from app.services import media as media_svc
from app.services.jobs import JobContext, PermanentError, handler, periodic

log = logging.getLogger("media")

TEXT_KEYS = ("headline", "body")


def _post_from_fields(org_id, kind: str, platform: str, fields: dict, size: str | None, base: Post | None) -> Post:
    """A transient (unsaved) Post carrying studio fields into the same pipeline used for real posts."""
    meta = {k: v for k, v in (fields or {}).items() if k not in TEXT_KEYS and v not in (None, "", [])}
    headline = (fields or {}).get("headline") or ""
    body = (fields or {}).get("body") or ""
    if headline:
        meta["headline"] = headline
    if body:
        meta["body"] = body
        meta.setdefault("subtitle", body)
    if size:
        meta["size"] = size
    return Post(id=base.id if base else None, organization_id=org_id, platform=platform, post_type=kind,
                title=headline or (base.title if base else None), text=body or (base.text if base else None),
                topic=base.topic if base else None, meta={**((base.meta or {}) if base else {}), **meta})


@handler("media.generate")
async def generate_job(ctx: JobContext) -> dict:
    p = ctx.payload or {}
    kind = p.get("kind")
    s = ctx.session
    try:
        if kind == "ai_image":
            await ctx.progress("Generating the image…")
            w, h = (int(x) for x in (p.get("size") or "1024x1024").split("x"))
            a = await media_svc.generate_ai_image(s, ctx.org_id, p.get("prompt") or "", w, h, enhance=p.get("enhance", True))
            await s.flush()
            return {"asset_ids": [str(a.id)], "kind": kind}

        if kind == "3d":
            await ctx.progress("Rendering the 3D scene…")
            w, h = (int(x) for x in (p.get("size") or "1080x1080").split("x"))
            brand = await media_svc.load_brand(s)
            style = await media_svc.brand_style(s, brand, ctx.org_id)
            a = await media_svc.generate_3d(s, ctx.org_id, style, p.get("scene") or "neural", p.get("output") or "image",
                                            p.get("fields") or {}, (w, h), model_asset_id=p.get("model_asset_id"),
                                            seconds=p.get("seconds"))
            await s.flush()
            return {"asset_ids": [str(a.id)], "kind": kind}

        post = None
        if p.get("post_id"):
            post = await s.get(Post, uuid.UUID(str(p["post_id"])))
            if post is None:
                raise PermanentError("The post no longer exists.")
        fields = p.get("fields") or {}
        if post is not None and not fields and not p.get("size"):
            target = post
            if kind and kind != post.post_type:
                target = _post_from_fields(ctx.org_id, kind, post.platform, {}, None, post)
        else:
            target = _post_from_fields(ctx.org_id, kind, p.get("platform") or (post.platform if post else "manual"),
                                       fields, p.get("size"), post)
        await ctx.progress({"video": "Building the video (voice-over, scenes, captions)…",
                            "gif": "Animating…", "animation": "Animating…"}.get(kind, "Rendering…"))
        brand = await media_svc.load_brand(s)
        if p.get("variants"):
            await ctx.progress("Rendering 3 options…")
            assets = await media_svc.generate_variants(s, target, brand, int(p.get("variants") or 3))
            await s.flush()
            # options stay in the library; the user picks one to attach
            return {"asset_ids": [str(a.id) for a in assets], "kind": kind, "post_id": str(post.id) if post else None, "variants": True}
        assets = await media_svc.generate_for_post(s, target, brand)
        ids = [str(a.id) for a in assets]
        if post is not None and ids:
            if post.status in ("scheduled", "approved", "publishing", "published"):
                # Approved content must not change silently: keep the assets in the library only.
                return {"asset_ids": ids, "kind": kind, "post_id": None,
                        "note": "The post was approved meanwhile, so the new media wasn't attached."}
            post.media_ids = ids if p.get("replace") else [*(post.media_ids or []), *ids]
            post.meta = {**(post.meta or {}), "keep_media": True}
        await s.flush()
        return {"asset_ids": ids, "kind": kind, "post_id": str(post.id) if post else None}
    except media_svc.MediaUnavailable as e:
        raise PermanentError(e.message) from e


@periodic(3600, name="media.tmp_cleanup")
async def cleanup_tmp() -> None:
    """Remove temp render folders/files older than a day (left behind by crashes)."""
    root = storage.root() / "tmp"
    if not root.exists():
        return
    cutoff = time.time() - 86400
    removed = 0
    for p in root.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("media temp cleanup removed %d entries", removed)
