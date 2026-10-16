"""Live model lists for every AI provider (text, embeddings, images, voices, video).

`discover(cfg)` asks the provider which models this key can use right now and labels each one:
chat | embed | image | tts_model | voice | video | other. Providers without a listing API fall back to the
catalog's suggestions (source = "suggested").

`sync_provider(...)` stores the list in the provider's settings and flags models you configured that are gone.
It runs when a provider is added or its key changes, every 6 hours in the worker, and from the Refresh button.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import ModelStatus, ProviderConfig, TaskRoute
from app.services.llm.catalog import BY_ID

log = logging.getLogger(__name__)

AI_KINDS = ("llm", "image", "tts", "video")
TIMEOUT = httpx.Timeout(20.0, connect=8.0)

_EMBED = re.compile(r"embed|bge-|bge_|/bge|e5-|minilm|mxbai|gte-|nomic-embed|arctic-embed|jina-embeddings|voyage", re.I)
_IMAGE = re.compile(r"image|dall-e|imagen|flux|stable-diffusion|sdxl|sd3|kandinsky|playground-v|recraft|ideogram", re.I)
_TTS = re.compile(r"\btts\b|tts-|-tts|text-to-speech|speech", re.I)
_VIDEO = re.compile(r"video|veo|sora|kling|hunyuan-video|wan2|ltx", re.I)
_OTHER = re.compile(r"whisper|transcri|moderation|guard|rerank|audio|realtime|search-preview|computer-use|babbage|davinci-00", re.I)


@dataclass
class Discovered:
    models: list[str]
    types: dict[str, str]
    source: str  # live | suggested


def classify_model(mid: str, raw: dict | None = None) -> str:
    raw = raw or {}
    t = str(raw.get("type") or raw.get("task") or "").lower()
    if t in ("embedding", "embeddings", "feature-extraction"):
        return "embed"
    if t in ("image", "text-to-image"):
        return "image"
    if t in ("chat", "language", "text-generation", "code"):
        return "chat" if not _EMBED.search(mid) else "embed"
    methods = [m.lower() for m in raw.get("supportedGenerationMethods", []) or []]
    if methods:
        if "embedcontent" in methods or "embedtext" in methods:
            return "embed"
        if "predict" in methods and _IMAGE.search(mid):
            return "image"
        if "predictlongrunning" in methods and _VIDEO.search(mid):
            return "video"
    if _EMBED.search(mid):
        return "embed"
    if _TTS.search(mid):
        return "tts_model"
    if _VIDEO.search(mid) and not re.search(r"vision|vl\b|-vl-", mid, re.I):
        return "video"
    if _IMAGE.search(mid) and not re.search(r"vision|-vl|image-?to-?text|understanding", mid, re.I):
        return "image"
    if _OTHER.search(mid):
        return "other"
    return "chat"


def _secrets(cfg: ProviderConfig) -> dict:
    return decrypt_json(cfg.secrets_enc)


def _base(cfg: ProviderConfig) -> str:
    spec = BY_ID.get(cfg.provider)
    return (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")


def _bearer(key: str | None) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _get_json(url: str, headers: dict | None = None, params: dict | None = None):
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        r = await http.get(url, headers=headers or {}, params=params)
    if r.status_code >= 400:
        msg = r.text[:300]
        try:
            body = r.json()
            err = body.get("error") if isinstance(body, dict) else None
            msg = (err.get("message") if isinstance(err, dict) else err) or msg
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"{r.status_code}: {msg}")
    return r.json()


def _items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "models", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _ids(items: list) -> list[tuple[str, dict]]:
    out = []
    for m in items:
        if isinstance(m, str):
            out.append((m, {}))
        elif isinstance(m, dict):
            mid = m.get("id") or m.get("name") or m.get("model") or m.get("model_name") or m.get("title")
            if mid:
                out.append((str(mid).removeprefix("models/"), m))
    return out


async def _openai_style(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    return _ids(_items(await _get_json(f"{_base(cfg)}/models", _bearer(_secrets(cfg).get("api_key")))))


async def _anthropic(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    key = _secrets(cfg).get("api_key") or ""
    data = await _get_json(f"{_base(cfg)}/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"},
                           {"limit": 1000})
    return [(i, {**raw, "type": "chat"}) for i, raw in _ids(_items(data))]


async def _gemini_native(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    key = _secrets(cfg).get("api_key") or ""
    base = _base(cfg)
    base = base[: base.index("/v1beta") + 7] if "/v1beta" in base else "https://generativelanguage.googleapis.com/v1beta"
    out: list[tuple[str, dict]] = []
    token = None
    for _ in range(5):
        params = {"key": key, "pageSize": 1000}
        if token:
            params["pageToken"] = token
        data = await _get_json(f"{base}/models", params=params)
        out += _ids(_items(data))
        token = data.get("nextPageToken") if isinstance(data, dict) else None
        if not token:
            break
    return out


async def _a1111(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    data = await _get_json(f"{_base(cfg)}/sdapi/v1/sd-models")
    return [((m.get("model_name") or m.get("title")), {"type": "image"}) for m in _items(data) if isinstance(m, dict)]


async def _edge_voices(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    from app.services.tts import list_voices

    return [(v["id"], {"type": "voice"}) for v in await list_voices() if v.get("provider") == "edge_tts"]


async def _openai_tts(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    from app.services.tts import OPENAI_VOICES

    voices = [(v, {"type": "voice"}) for v in OPENAI_VOICES]
    try:
        models = [(i, {"type": "tts_model"}) for i, _ in await _openai_style(cfg) if _TTS.search(i)]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(str(e)) from e
    return models + voices


async def _openai_images(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    found = await _openai_style(cfg)
    return [(i, {**raw, "type": raw.get("type") or ("image" if _IMAGE.search(i) else "other")}) for i, raw in found]


async def _gemini_images(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    return [(i, raw) for i, raw in await _gemini_native(cfg) if _IMAGE.search(i)]


STABILITY_MODELS = ["core", "ultra", "sd3.5-large", "sd3.5-large-turbo", "sd3.5-medium", "sd3.5-flash"]


async def _hf_text_to_image(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    """Text-to-image models that Hugging Face's own inference serves (official Hub API), most liked first."""
    key = _secrets(cfg).get("api_key")
    data = await _get_json("https://huggingface.co/api/models", _bearer(key), {
        "pipeline_tag": "text-to-image", "inference_provider": "hf-inference", "sort": "likes", "direction": "-1",
        "limit": "80"})
    return [(m.get("id") or m.get("modelId"), {"type": "image"}) for m in _items(data) if isinstance(m, dict)]


async def _replicate_video(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    """Replicate's curated text-to-video collection (official API)."""
    key = _secrets(cfg).get("api_key")
    data = await _get_json(f"{_base(cfg)}/collections/text-to-video", _bearer(key))
    out = []
    for m in data.get("models", []) if isinstance(data, dict) else []:
        if isinstance(m, dict) and m.get("owner") and m.get("name"):
            out.append((f"{m['owner']}/{m['name']}", {"type": "video"}))
    return out


async def _comfyui(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    data = await _get_json(f"{_base(cfg)}/object_info/CheckpointLoaderSimple")
    try:
        names = data["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except (KeyError, IndexError, TypeError):
        names = []
    return [(n, {"type": "image"}) for n in names if isinstance(n, str)]


async def _edge_all_voices(cfg: ProviderConfig) -> list[tuple[str, dict]]:
    """Every Microsoft Edge voice (all languages), Indian and English voices first."""
    try:
        import edge_tts

        voices = await edge_tts.list_voices()
    except Exception:  # noqa: BLE001 - offline: the app's built-in short list
        return await _edge_voices(cfg)
    first = ("en-IN", "hi-IN", "en-US", "en-GB")
    voices = sorted(voices, key=lambda v: (first.index(v.get("Locale")) if v.get("Locale") in first else 9,
                                           v.get("Locale", ""), v.get("ShortName", "")))
    return [(v["ShortName"], {"type": "voice"}) for v in voices if v.get("ShortName")]


async def _discover_raw(cfg: ProviderConfig) -> list[tuple[str, dict]] | None:
    """None means the provider has no listing API (use the catalog's suggestions)."""
    spec = BY_ID.get(cfg.provider)
    style = spec.style if spec else "openai"
    if cfg.kind == "llm":
        if style == "anthropic":
            return await _anthropic(cfg)
        if cfg.provider == "gemini":
            # The native list carries supportedGenerationMethods, so embeddings are labelled exactly.
            try:
                return await _gemini_native(cfg)
            except Exception:  # noqa: BLE001
                return await _openai_style(cfg)
        return await _openai_style(cfg)
    if cfg.kind == "image":
        if style == "openai_image":
            return await _openai_images(cfg)
        if style == "gemini_image":
            return await _gemini_images(cfg)
        if style == "a1111":
            return await _a1111(cfg)
        if style == "hf_image":
            return await _hf_text_to_image(cfg)
        if style == "comfyui":
            return await _comfyui(cfg)
        if style == "stability":
            return [(m, {"type": "image"}) for m in STABILITY_MODELS]
        return None
    if cfg.kind == "tts":
        if style == "edge_tts":
            return await _edge_all_voices(cfg)
        if style == "openai_tts":
            return await _openai_tts(cfg)
        return None
    if cfg.kind == "video" and style == "replicate":
        return await _replicate_video(cfg)
    return None


def _suggested(cfg: ProviderConfig) -> Discovered:
    spec = BY_ID.get(cfg.provider)
    ex = list(spec.examples) if spec else []
    default_type = {"image": "image", "video": "video", "tts": "voice"}.get(cfg.kind)
    types = {m: default_type or classify_model(m) for m in ex}
    if cfg.provider == "openai_tts":
        from app.services.tts import OPENAI_VOICES

        types = {m: "tts_model" for m in ex} | {v: "voice" for v in OPENAI_VOICES}
        ex = list(types)
    return Discovered(ex, types, "suggested")


async def discover(cfg: ProviderConfig) -> Discovered:
    raw = await _discover_raw(cfg)
    if raw is None:
        return _suggested(cfg)
    types: dict[str, str] = {}
    for mid, meta in raw:
        if not mid:
            continue
        t = meta.get("type") if meta.get("type") in ("chat", "embed", "image", "tts_model", "voice", "video", "other") else None
        types[mid] = t or classify_model(mid, meta)
    if cfg.kind == "image":
        types = {m: t for m, t in types.items() if t == "image"} or types
    models = sorted(types, key=lambda m: ({"chat": 0, "embed": 1, "image": 2, "voice": 3, "tts_model": 4, "video": 5}.get(types[m], 9), m.lower()))
    return Discovered(models, types, "live")


# ----------------------------------------------------------------------------- storing + checks
def configured_models(cfg: ProviderConfig, routes: list[TaskRoute]) -> set[str]:
    s = cfg.settings or {}
    used = {m for m in (s.get("default_model"), s.get("embed_model"), s.get("voice")) if m}
    for r in routes:
        for step in r.chain or []:
            if str(step.get("provider_id")) == str(cfg.id) and step.get("model"):
                used.add(step["model"])
    return used


async def sync_provider(session: AsyncSession, cfg: ProviderConfig, *, notify_missing: bool = True) -> dict:
    """Refresh cfg.settings['models'] from the provider. Caller commits. Never raises."""
    from app.services.llm import health

    now = datetime.now(UTC)
    settings = dict(cfg.settings or {})
    try:
        d = await discover(cfg)
    except Exception as e:  # noqa: BLE001
        settings.update(models_error=str(e)[:300], models_checked_at=now.isoformat())
        cfg.settings = settings
        return {"ok": False, "error": str(e)[:300], "models": settings.get("models", [])}
    settings.update(models=d.models, model_types=d.types, models_source=d.source, models_synced_at=now.isoformat(),
                    models_checked_at=now.isoformat(), models_error=None)
    missing: list[str] = []
    if d.source == "live" and d.models:
        routes = (await session.scalars(select(TaskRoute))).all()
        offered = set(d.models)
        rows = await health.statuses(session, [cfg.id])
        for m in sorted(configured_models(cfg, routes)):
            if m in offered or m.removeprefix("models/") in offered or f"{m}:latest" in offered:
                continue
            row = rows.get((cfg.id, m))
            # A model that answered recently is clearly still offered (aliases are often missing from lists).
            if row and row.last_ok_at and now - row.last_ok_at < timedelta(hours=24):
                continue
            missing.append(m)
    settings["missing_models"] = missing
    cfg.settings = settings
    if notify_missing:
        for m in missing:
            await health.mark_unavailable(cfg, m, f"{cfg.name} no longer lists {m}")
    return {"ok": True, "models": d.models, "types": d.types, "source": d.source, "missing": missing}


async def sync_org(session: AsyncSession) -> int:
    cfgs = (await session.scalars(select(ProviderConfig).where(ProviderConfig.kind.in_(AI_KINDS),
                                                               ProviderConfig.enabled.is_(True)))).all()
    n = 0
    for cfg in cfgs:
        r = await sync_provider(session, cfg)
        n += 1 if r.get("ok") else 0
    return n


async def discover_unsaved(provider: str, base_url: str | None, secrets: dict, settings: dict | None = None) -> Discovered:
    """For the Add form: list models before the integration is saved."""
    from app.core.crypto import encrypt_json

    spec = BY_ID.get(provider)
    if spec is None:
        raise ValueError(f"Unknown provider: {provider}")
    cfg = ProviderConfig(kind=spec.kind, provider=spec.id, name=spec.label, base_url=(base_url or "").strip() or None,
                         secrets_enc=encrypt_json({k: v for k, v in secrets.items() if v}), settings=settings or {})
    return await discover(cfg)


async def statuses_for_ui(session: AsyncSession) -> list[dict]:
    rows = (await session.scalars(select(ModelStatus))).all()
    from app.services.llm.health import LABELS, resting

    now = datetime.now(UTC)
    return [{
        "provider_id": str(r.provider_id), "model": r.model,
        "state": r.state if (r.state == "ok" or resting(r, now)) else "ok",
        "label": LABELS.get(r.state if resting(r, now) else "ok"),
        "reason": r.reason if resting(r, now) else None,
        "until": r.cooldown_until.isoformat() if resting(r, now) else None,
        "last_error": r.last_error, "last_ok_at": r.last_ok_at.isoformat() if r.last_ok_at else None,
        "last_fail_at": r.last_fail_at.isoformat() if r.last_fail_at else None,
    } for r in rows]
