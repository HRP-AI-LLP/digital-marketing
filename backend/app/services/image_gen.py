"""AI image generation through the configured image providers (Settings → Integrations → Images).

Provider order: a TaskRoute for task "image" when one exists, otherwise every enabled image provider
(cloud first, local last — same convention as text routing). Each provider is tried in turn; failures
are logged to LlmUsage and the next one is tried. Local GPU providers (a1111, comfyui) share
`LOCAL_GPU_LOCK` with local text models (8 GB VRAM: one GPU job at a time).

Text never goes inside AI images: prompts ask for no text and the negative prompt rejects it; headlines
are added afterwards by the branded templates.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import LlmUsage, ProviderConfig, TaskRoute
from app.services.llm.catalog import BY_ID
from app.services.llm.client import LOCAL_GPU_LOCK

log = logging.getLogger("media.image")

# Tests replace this with httpx.MockTransport; every outbound call in the media services uses http_client().
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None

NEGATIVE = ("text, words, letters, typography, caption, watermark, logo, signature, label, blurry, low quality, "
            "distorted hands, extra fingers, deformed, nsfw")
STYLE_SUFFIX = ("high quality, professional, clean composition, natural light, brand-safe, "
                "no text, no words, no logos, no watermark")


def http_client(timeout: float = 120.0, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=HTTP_TRANSPORT, timeout=httpx.Timeout(timeout, connect=15.0),
                             follow_redirects=True, **kw)


class ImageGenError(Exception):
    pass


@dataclass
class GeneratedImage:
    data: bytes  # PNG, already cover-fitted to the requested size
    provider: str
    provider_id: uuid.UUID
    model: str
    prompt: str
    negative: str


# ----------------------------------------------------------------------------- provider resolution
def _secrets(cfg: ProviderConfig) -> dict:
    return decrypt_json(cfg.secrets_enc)


def _base(cfg: ProviderConfig) -> str:
    spec = BY_ID.get(cfg.provider)
    return (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")


def _style(cfg: ProviderConfig) -> str:
    spec = BY_ID.get(cfg.provider)
    return spec.style if spec else ""


def _default_model(cfg: ProviderConfig) -> str:
    spec = BY_ID.get(cfg.provider)
    m = (cfg.settings or {}).get("default_model")
    if m:
        return m
    if spec and spec.examples:
        return spec.examples[0]
    return ""


async def image_chain(session: AsyncSession) -> list[tuple[ProviderConfig, str]]:
    cfgs = {c.id: c for c in (await session.scalars(select(ProviderConfig).where(ProviderConfig.kind == "image"))).all()}
    route = await session.scalar(select(TaskRoute).where(TaskRoute.task == "image"))
    chain: list[tuple[ProviderConfig, str]] = []
    if route and route.chain:
        for step in route.chain:
            try:
                cfg = cfgs.get(uuid.UUID(str(step.get("provider_id"))))
            except ValueError:
                cfg = None
            if cfg and cfg.enabled:
                chain.append((cfg, step.get("model") or _default_model(cfg)))
        if chain:
            return chain
    enabled = [c for c in cfgs.values() if c.enabled]
    enabled.sort(key=lambda c: (BY_ID.get(c.provider).local if BY_ID.get(c.provider) else False, c.created_at))
    return [(c, _default_model(c)) for c in enabled]


async def has_image_provider(session: AsyncSession) -> bool:
    return bool(await image_chain(session))


# ----------------------------------------------------------------------------- prompt building
async def build_prompt(session: AsyncSession, brief: str, *, brand_hint: str = "", orientation: str = "square") -> tuple[str, str]:
    """Turn a content brief into an image prompt (LLM task 'image_prompt'); falls back to a template prompt."""
    from app.services.llm import router

    brief = (brief or "").strip()[:1500]
    if not brief:
        brief = "abstract modern technology background"
    try:
        data = await router.complete_json(
            session, "image_prompt",
            "Write a prompt for an image generation model for a social media post background.\n"
            f"Brief: {brief}\n"
            + (f"Brand look: {brand_hint}\n" if brand_hint else "")
            + f"Orientation: {orientation}.\n"
            "Rules: photographic or clean editorial illustration; describe subject, setting, lighting, composition and mood; "
            "leave calm negative space for a headline; absolutely no text, letters, numbers, logos or watermarks in the image; "
            "brand-safe, inclusive, no real people's likeness, no trademarks. Under 70 words.\n"
            'Return JSON: {"prompt": "...", "negative": "comma separated things to avoid"}',
            system="You write concise, safe, vivid prompts for image models.", temperature=0.6, max_tokens=400,
        )
        prompt = str((data or {}).get("prompt") or "").strip()
        negative = str((data or {}).get("negative") or "").strip()
        if prompt:
            return f"{prompt}, {STYLE_SUFFIX}", ", ".join(x for x in [negative, NEGATIVE] if x)
    except Exception as e:  # noqa: BLE001 - no text model configured or it failed: use the brief directly
        log.info("image_prompt fallback: %s", e)
    return f"{brief}, editorial photograph, {STYLE_SUFFIX}", NEGATIVE


# ----------------------------------------------------------------------------- image utils
def cover_fit(data: bytes, width: int, height: int) -> bytes:
    from PIL import Image, ImageOps

    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    im = ImageOps.fit(im, (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45))
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=False)
    return out.getvalue()


def _aspect(width: int, height: int) -> float:
    return width / max(1, height)


def _closest(width: int, height: int, options: list[tuple[int, int]]) -> tuple[int, int]:
    a = _aspect(width, height)
    return min(options, key=lambda o: abs(_aspect(*o) - a))


def _ratio_label(width: int, height: int, allowed: list[str]) -> str:
    a = _aspect(width, height)

    def val(s: str) -> float:
        x, y = s.split(":")
        return int(x) / int(y)

    return min(allowed, key=lambda s: abs(val(s) - a))


def _gen_dims(width: int, height: int, max_side: int = 1024, multiple: int = 64) -> tuple[int, int]:
    s = max_side / max(width, height)
    w = max(multiple, int(round(width * s / multiple)) * multiple)
    h = max(multiple, int(round(height * s / multiple)) * multiple)
    return w, h


def _raise_for(r: httpx.Response, label: str) -> None:
    if r.status_code >= 400:
        try:
            body = r.json()
            msg = body.get("error") or body.get("message") or body.get("errors") or body.get("detail") or body
            if isinstance(msg, dict):
                msg = msg.get("message") or msg
        except Exception:  # noqa: BLE001
            msg = r.text[:300]
        raise ImageGenError(f"{label}: {r.status_code} {str(msg)[:300]}")


async def _download(http: httpx.AsyncClient, url: str) -> bytes:
    r = await http.get(url)
    _raise_for(r, "download")
    return r.content


# ----------------------------------------------------------------------------- provider calls
async def _openai_image(cfg, model, prompt, negative, width, height, seed) -> bytes:
    key = _secrets(cfg).get("api_key")
    body: dict = {"model": model, "prompt": prompt, "n": 1}
    if cfg.provider == "together_images":
        w, h = _gen_dims(width, height, 1024, 64)
        body.update(width=w, height=h, steps=4 if "schnell" in model.lower() else 28, response_format="b64_json")
        if negative:
            body["negative_prompt"] = negative
    elif cfg.provider == "xai_images":
        body["response_format"] = "b64_json"
    else:
        if model.startswith("dall-e-3"):
            body["size"] = "%dx%d" % _closest(width, height, [(1024, 1024), (1792, 1024), (1024, 1792)])
            body["response_format"] = "b64_json"
        elif model.startswith("dall-e-2"):
            body["size"] = "1024x1024"
            body["response_format"] = "b64_json"
        else:  # gpt-image-*: always base64
            body["size"] = "%dx%d" % _closest(width, height, [(1024, 1024), (1536, 1024), (1024, 1536)])
    async with http_client(180) as http:
        r = await http.post(f"{_base(cfg)}/images/generations", json=body,
                            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        _raise_for(r, cfg.name)
        item = (r.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            return await _download(http, item["url"])
    raise ImageGenError(f"{cfg.name}: no image in the response")


async def _gemini_image(cfg, model, prompt, negative, width, height, seed) -> bytes:
    key = _secrets(cfg).get("api_key")
    ratio = _ratio_label(width, height, ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"])
    body = {
        "contents": [{"parts": [{"text": f"{prompt}\nAvoid: {negative}"}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": ratio}},
    }
    async with http_client(180) as http:
        r = await http.post(f"{_base(cfg)}/models/{model}:generateContent", json=body,
                            headers={"x-goog-api-key": key or "", "Content-Type": "application/json"})
        _raise_for(r, cfg.name)
        for cand in r.json().get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])
    raise ImageGenError(f"{cfg.name}: the model returned no image (it may have refused the prompt)")


async def _hf_image(cfg, model, prompt, negative, width, height, seed) -> bytes:
    key = _secrets(cfg).get("api_key")
    w, h = _gen_dims(width, height, 1024, 64)
    body = {"inputs": prompt, "parameters": {"negative_prompt": negative, "width": w, "height": h, "seed": seed}}
    async with http_client(180) as http:
        r = await http.post(f"{_base(cfg)}/{model}", json=body,
                            headers={"Authorization": f"Bearer {key}", "Accept": "image/png"})
        _raise_for(r, cfg.name)
        if not r.headers.get("content-type", "").startswith("image/"):
            raise ImageGenError(f"{cfg.name}: unexpected response {r.text[:200]}")
        return r.content


async def _stability(cfg, model, prompt, negative, width, height, seed) -> bytes:
    key = _secrets(cfg).get("api_key")
    model = (model or "core").lower()
    endpoint = model if model in ("core", "ultra") else "sd3"
    ratio = _ratio_label(width, height, ["16:9", "1:1", "21:9", "2:3", "3:2", "4:5", "5:4", "9:16", "9:21"])
    fields = {"prompt": prompt, "negative_prompt": negative, "aspect_ratio": ratio, "output_format": "png", "seed": str(seed)}
    if endpoint == "sd3":
        fields["model"] = model
    async with http_client(180) as http:
        r = await http.post(f"{_base(cfg)}/v2beta/stable-image/generate/{endpoint}",
                            files={k: (None, v) for k, v in fields.items()},
                            headers={"Authorization": f"Bearer {key}", "Accept": "image/*"})
        _raise_for(r, cfg.name)
        return r.content


async def _a1111(cfg, model, prompt, negative, width, height, seed) -> bytes:
    w, h = _gen_dims(width, height, int((cfg.settings or {}).get("max_side") or 1024), 8)
    body = {"prompt": prompt, "negative_prompt": negative, "width": w, "height": h, "seed": seed,
            "steps": int((cfg.settings or {}).get("steps") or 25), "cfg_scale": 6, "sampler_name": "DPM++ 2M"}
    if model and model not in ("default", "local"):
        body["override_settings"] = {"sd_model_checkpoint": model}
    async with LOCAL_GPU_LOCK, http_client(600) as http:
        r = await http.post(f"{_base(cfg)}/sdapi/v1/txt2img", json=body)
        _raise_for(r, cfg.name)
        images = r.json().get("images") or []
        if not images:
            raise ImageGenError(f"{cfg.name}: no image returned")
        return base64.b64decode(images[0].split(",", 1)[-1])


def comfy_workflow(raw: str, prompt: str, negative: str, width: int, height: int, seed: int) -> dict:
    """Fill {{prompt}}, {{negative}}, {{width}}, {{height}}, {{seed}} in an API-format ComfyUI workflow."""
    if not raw or not raw.strip():
        raise ImageGenError("ComfyUI needs a workflow: export it with 'Save (API format)' and paste it in the integration.")

    def s(v: str) -> str:
        return json.dumps(v)[1:-1]  # JSON-escape without the quotes

    text = raw
    for k, v in (("width", width), ("height", height), ("seed", seed)):
        text = text.replace(f'"{{{{{k}}}}}"', str(v)).replace(f"{{{{{k}}}}}", str(v))
    text = text.replace("{{prompt}}", s(prompt)).replace("{{negative}}", s(negative))
    try:
        wf = json.loads(text)
    except json.JSONDecodeError as e:
        raise ImageGenError(f"The ComfyUI workflow isn't valid JSON: {e}") from e
    if isinstance(wf, dict) and "prompt" in wf and isinstance(wf["prompt"], dict) and "nodes" not in wf:
        wf = wf["prompt"]
    if isinstance(wf, dict) and "nodes" in wf:
        raise ImageGenError("That's the UI workflow format. In ComfyUI use 'Save (API format)' and paste that instead.")
    return wf


async def _comfyui(cfg, model, prompt, negative, width, height, seed) -> bytes:
    raw = _secrets(cfg).get("workflow_json") or (cfg.settings or {}).get("workflow_json") or ""
    w, h = _gen_dims(width, height, int((cfg.settings or {}).get("max_side") or 1024), 8)
    wf = comfy_workflow(raw, prompt, negative, w, h, seed)
    base = _base(cfg)
    async with LOCAL_GPU_LOCK, http_client(60) as http:
        r = await http.post(f"{base}/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex})
        _raise_for(r, cfg.name)
        pid = r.json().get("prompt_id")
        if not pid:
            raise ImageGenError(f"{cfg.name}: no prompt id returned ({r.text[:200]})")
        deadline = time.monotonic() + float((cfg.settings or {}).get("timeout_s") or 600)
        poll = float((cfg.settings or {}).get("poll_s") or 1.5)
        while time.monotonic() < deadline:
            hr = await http.get(f"{base}/history/{pid}")
            _raise_for(hr, cfg.name)
            entry = (hr.json() or {}).get(pid)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    raise ImageGenError(f"{cfg.name}: the workflow failed ({str(status.get('messages'))[:300]})")
                for out in (entry.get("outputs") or {}).values():
                    for img in out.get("images") or []:
                        vr = await http.get(f"{base}/view", params={"filename": img.get("filename"),
                                                                    "subfolder": img.get("subfolder", ""),
                                                                    "type": img.get("type", "output")})
                        _raise_for(vr, cfg.name)
                        return vr.content
                if status.get("completed"):
                    raise ImageGenError(f"{cfg.name}: the workflow finished without an image output (add a SaveImage node)")
            await asyncio.sleep(poll)
    raise ImageGenError(f"{cfg.name}: timed out waiting for the image")


STYLES = {
    "openai_image": _openai_image,
    "gemini_image": _gemini_image,
    "hf_image": _hf_image,
    "stability": _stability,
    "a1111": _a1111,
    "comfyui": _comfyui,
}


# ----------------------------------------------------------------------------- public API
def _log_usage(session: AsyncSession, cfg: ProviderConfig, model: str, task: str, *, ok: bool, error: str | None,
               latency: int) -> None:
    prices = (cfg.settings or {}).get("prices") or {}
    try:
        price = float(prices.get("image") or 0)
    except (TypeError, ValueError):
        price = 0.0
    session.add(LlmUsage(
        organization_id=cfg.organization_id, provider_id=cfg.id, provider=cfg.provider, model=model or "-", task=task,
        kind="image", units=1 if ok else 0, cost_usd=round(price if ok else 0, 6), ok=ok, error=error, latency_ms=latency,
    ))


async def generate(session: AsyncSession, prompt: str, width: int, height: int, *, negative: str | None = None,
                   task: str = "image", seed: int | None = None) -> GeneratedImage:
    """Generate one image with the first provider in the chain that succeeds. Raises ImageGenError."""
    from app.services.llm.router import over_budget

    chain = await image_chain(session)
    if not chain:
        raise ImageGenError("No image provider is set up. Add one in Settings → Integrations → Images.")
    from app.services.llm import health

    chain, _ = await health.order_chain(session, chain)
    negative = negative if negative is not None else NEGATIVE
    seed = seed if seed is not None else random.randint(1, 2**31 - 1)
    errors: list[str] = []
    for cfg, model in chain:
        fn = STYLES.get(_style(cfg))
        if fn is None:
            continue
        if await over_budget(session, cfg):
            errors.append(f"{cfg.name}: monthly budget reached")
            continue
        t0 = time.perf_counter()
        try:
            raw = await fn(cfg, model, prompt, negative, width, height, seed)
            data = await asyncio.to_thread(cover_fit, raw, width, height)
        except Exception as e:  # noqa: BLE001
            log.warning("image provider %s failed: %s", cfg.name, e)
            _log_usage(session, cfg, model, task, ok=False, error=str(e)[:1000], latency=int((time.perf_counter() - t0) * 1000))
            v = await health.record_failure(cfg, model, e)
            errors.append(f"{cfg.name}: {(v.reason + '. ') if v.reason else ''}{str(e)[:200]}")
            continue
        _log_usage(session, cfg, model, task, ok=True, error=None, latency=int((time.perf_counter() - t0) * 1000))
        await session.flush()
        await health.record_success(cfg, model)
        return GeneratedImage(data, cfg.provider, cfg.id, model, prompt, negative)
    await session.flush()
    raise ImageGenError("All image providers failed. " + " | ".join(errors))


# ----------------------------------------------------------------------------- connection tests
from app.api.integrations import tester  # noqa: E402


@tester("openai_image")
async def _test_openai_image(cfg: ProviderConfig) -> str:
    key = _secrets(cfg).get("api_key")
    if not key:
        raise ImageGenError("Add the API key.")
    async with http_client(20) as http:
        r = await http.get(f"{_base(cfg)}/models", headers={"Authorization": f"Bearer {key}"})
        _raise_for(r, cfg.name)
        data = r.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        ids = [m.get("id") for m in items if isinstance(m, dict)] if isinstance(items, list) else []
    model = _default_model(cfg)
    hint = "" if not model or not ids or model in ids else f" Note: {model} isn't in the model list."
    return f"Connected. {len(ids)} models available. Images will use {model or 'the default model'}.{hint}"


@tester("gemini_image")
async def _test_gemini_image(cfg: ProviderConfig) -> str:
    key = _secrets(cfg).get("api_key")
    if not key:
        raise ImageGenError("Add the API key.")
    async with http_client(20) as http:
        r = await http.get(f"{_base(cfg)}/models", headers={"x-goog-api-key": key})
        _raise_for(r, cfg.name)
        n = len(r.json().get("models") or [])
    return f"Connected. {n} models available. Images will use {_default_model(cfg)}."


@tester("hf_image")
async def _test_hf(cfg: ProviderConfig) -> str:
    key = _secrets(cfg).get("api_key")
    if not key:
        raise ImageGenError("Add your Hugging Face access token.")
    async with http_client(20) as http:
        r = await http.get("https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {key}"})
        _raise_for(r, cfg.name)
        name = r.json().get("name")
    return f"Signed in as {name}. Images will use {_default_model(cfg)}."


@tester("stability")
async def _test_stability(cfg: ProviderConfig) -> str:
    key = _secrets(cfg).get("api_key")
    if not key:
        raise ImageGenError("Add the API key.")
    async with http_client(20) as http:
        r = await http.get(f"{_base(cfg)}/v1/user/balance", headers={"Authorization": f"Bearer {key}"})
        _raise_for(r, cfg.name)
        credits = r.json().get("credits")
    return f"Connected. Credits left: {credits}."


@tester("a1111")
async def _test_a1111(cfg: ProviderConfig) -> str:
    async with http_client(15) as http:
        r = await http.get(f"{_base(cfg)}/sdapi/v1/sd-models")
        _raise_for(r, cfg.name)
        models = [m.get("model_name") for m in r.json() if isinstance(m, dict)]
    return f"Connected. {len(models)} checkpoints found" + (f", e.g. {models[0]}." if models else ". Add a checkpoint to generate images.")


@tester("comfyui")
async def _test_comfyui(cfg: ProviderConfig) -> str:
    raw = _secrets(cfg).get("workflow_json") or (cfg.settings or {}).get("workflow_json") or ""
    wf = comfy_workflow(raw, "test", "test", 512, 512, 1)
    missing = [p for p in ("{{prompt}}",) if p not in raw]
    async with http_client(15) as http:
        r = await http.get(f"{_base(cfg)}/system_stats")
        _raise_for(r, cfg.name)
        dev = ((r.json().get("devices") or [{}])[0]).get("name", "unknown device")
    msg = f"Connected to ComfyUI on {dev}. Workflow has {len(wf)} nodes."
    if missing:
        msg += " Warning: the workflow has no {{prompt}} placeholder, so every image will look the same."
    return msg
