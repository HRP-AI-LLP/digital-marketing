"""List prices for AI models, used to show what each model costs.

Source: the community-maintained LiteLLM price list (thousands of models across OpenAI, Google Gemini, Anthropic,
xAI, Mistral, Groq, DeepSeek, Together, OpenRouter, Stability ...). A trimmed copy ships in
app/data/model_prices.json and a fresh copy is downloaded once a day into storage/cache, so new models and price
changes show up without an update.

How a model's cost is worked out (first match wins):
  1. your own price on the integration (Settings -> Integrations -> Edit -> prices)
  2. local models (Ollama, LM Studio, ComfyUI ...) cost nothing
  3. integrations marked "Free tier" cost nothing (the list-price value is still shown as "value at list price")
  4. OpenRouter models ending in ":free" cost nothing
  5. the list price

Text and embeddings are priced per token; images per image; voice per second (or per character, at about
15 characters per second of speech); video per second (a clip is counted as 5 seconds when its length is unknown).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

from app.core import storage
from app.services.llm.catalog import BY_ID

log = logging.getLogger(__name__)

SOURCE_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
BUNDLED = Path(__file__).resolve().parents[2] / "data" / "model_prices.json"
CHARS_PER_SECOND = 15
VIDEO_SECONDS_PER_CLIP = 5
KEEP = ("input_cost_per_token", "output_cost_per_token", "input_cost_per_character", "output_cost_per_image",
        "input_cost_per_image", "output_cost_per_second", "output_cost_per_video_per_second", "input_cost_per_pixel")
MODES = {"chat", "completion", "embedding", "image_generation", "audio_speech", "video_generation", "responses"}

# Our integration ids -> the prefix the price list uses for that provider ("" = no prefix).
PREFIX = {
    "openai": "", "openai_images": "", "openai_tts": "", "anthropic": "", "custom_openai": "",
    "gemini": "gemini/", "gemini_images": "gemini/", "xai": "xai/", "xai_images": "xai/", "groq": "groq/",
    "mistral": "mistral/", "deepseek": "deepseek/", "together": "together_ai/", "together_images": "together_ai/",
    "cerebras": "cerebras/", "perplexity": "perplexity/", "openrouter": "openrouter/", "huggingface": "huggingface/", "hf_images": "huggingface/",
    "stability": "stability/", "replicate_video": "replicate/",
}
FREE_PROVIDERS = {"edge_tts"}  # always free, no key


def _cache_file() -> Path:
    return storage.root() / "cache" / "model_prices.json"


def trim(raw: dict) -> dict:
    out = {}
    for k, v in raw.items():
        if not isinstance(v, dict) or v.get("mode") not in MODES:
            continue
        row = {f: v[f] for f in KEEP if isinstance(v.get(f), (int, float))}
        if row:
            row["mode"] = v["mode"]
            out[k] = row
    return out


@lru_cache(maxsize=1)
def _table(stamp: float = 0) -> tuple[dict, dict, str]:
    """(prices by exact key, prices by lowercased key, where the list came from). `stamp` busts the cache."""
    data, source = {}, "bundled"
    try:
        f = _cache_file()
        if f.exists():
            data, source = json.loads(f.read_text("utf-8")), "downloaded"
    except (OSError, ValueError):
        data = {}
    if not data:
        try:
            data = json.loads(BUNDLED.read_text("utf-8"))
        except (OSError, ValueError):
            log.exception("bundled price list unreadable")
            data = {}
    return data, {k.lower(): v for k, v in data.items()}, source


_stamp = 0.0


def table() -> tuple[dict, dict, str]:
    return _table(_stamp)


def updated_at() -> float | None:
    try:
        return _cache_file().stat().st_mtime
    except OSError:
        return BUNDLED.stat().st_mtime if BUNDLED.exists() else None


async def refresh() -> int:
    """Download the latest price list. Returns the number of priced models (0 when it couldn't be fetched)."""
    global _stamp
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(SOURCE_URL)
            r.raise_for_status()
            data = trim(r.json())
    except Exception as e:  # noqa: BLE001 - keep the last good copy
        log.warning("price list download failed: %s", e)
        return 0
    if len(data) < 500:  # something odd came back; keep the last good copy
        return 0
    f = _cache_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), "utf-8")
    tmp.replace(f)
    _stamp = time.time()
    return len(data)


# ----------------------------------------------------------------------------- lookup
_DATE = re.compile(r"(-\d{4}-\d{2}-\d{2}|-\d{8}|-\d{4}|-\d{3}|@\d+|:\w+)$")


def _candidates(provider: str, model: str) -> list[str]:
    m = model.strip()
    if m.startswith("models/"):
        m = m[7:]
    pre = PREFIX.get(provider, "")
    names = [m]
    base = _DATE.sub("", m)
    if base != m:
        names.append(base)
    if "/" in m:  # vendor/model (OpenRouter, Together, Hugging Face): also try the bare model name
        tail = m.split("/", 1)[1]
        names += [tail, _DATE.sub("", tail)]
    out: list[str] = []
    for n in names:
        for key in ([pre + n] if pre else []) + [n, f"{n}-latest"]:
            if key not in out:
                out.append(key)
    if provider in ("openai_images", "together_images", "xai_images", "gemini_images"):
        out += [f"{q}1024-x-1024/{n}" for n in names for q in ("", "standard/", "medium/")]
    return out


def list_price(provider: str, model: str) -> tuple[str | None, dict | None]:
    """(matched price-list key, its prices) or (None, None) when the model isn't on the list."""
    if not model:
        return None, None
    exact, lower, _ = table()
    for key in _candidates(provider, model):
        row = exact.get(key) or lower.get(key.lower())
        if row:
            return key, row
    return None, None


def _from_row(row: dict, kind: str, tin: int, tout: int, units: int) -> float | None:
    g = row.get
    if kind in ("chat", "embed"):
        if g("input_cost_per_token") is None and g("output_cost_per_token") is None:
            return None
        return tin * (g("input_cost_per_token") or 0) + tout * (g("output_cost_per_token") or 0)
    if kind == "image":
        per = g("output_cost_per_image") or g("input_cost_per_image")
        if per is None and g("input_cost_per_pixel"):
            per = g("input_cost_per_pixel") * 1024 * 1024
        return None if per is None else units * per
    if kind == "tts":
        if g("output_cost_per_second"):
            return units * g("output_cost_per_second")
        if g("input_cost_per_character"):
            return units * CHARS_PER_SECOND * g("input_cost_per_character")
        return None
    if kind == "video":
        per_s = g("output_cost_per_second") or g("output_cost_per_video_per_second")
        return None if per_s is None else units * VIDEO_SECONDS_PER_CLIP * per_s
    return None


def _custom(settings: dict, model: str, kind: str, tin: int, tout: int, units: int) -> float | None:
    prices = (settings or {}).get("prices") or {}
    if kind in ("chat", "embed"):
        p = prices.get(model) or prices.get("*")
        try:
            pin, pout = float(p[0] or 0), float(p[1] or 0)
        except (TypeError, ValueError, IndexError):
            return None
        return (tin * pin + tout * pout) / 1_000_000 if (pin or pout) else None
    key = {"image": "image", "video": "video", "tts": "minute"}.get(kind)
    try:
        per = float(prices.get(key) or 0) if key else 0
    except (TypeError, ValueError):
        per = 0
    if not per:
        return None
    return units * per / 60 if kind == "tts" else units * per


def default_billing(provider: str) -> str:
    spec = BY_ID.get(provider)
    if provider in FREE_PROVIDERS or (spec and spec.local):
        return "free"
    note = (spec.free if spec else "").lower()
    return "free" if note.startswith(("free", "limited free")) else "paid"


def billing_of(provider: str, settings: dict | None) -> str:
    b = (settings or {}).get("billing")
    return b if b in ("free", "paid") else default_billing(provider)


@dataclass
class Cost:
    cost: float           # what you pay
    list_value: float     # what it would cost at list price (or your price)
    source: str           # custom | list | free | local | free_model | unknown
    price_key: str | None = None


def cost(provider: str, settings: dict | None, model: str, kind: str, tin: int = 0, tout: int = 0, units: int = 0) -> Cost:
    spec = BY_ID.get(provider)
    custom = _custom(settings or {}, model, kind, tin, tout, units)
    key, row = list_price(provider, model)
    listed = _from_row(row, kind, tin, tout, units) if row else None
    value = custom if custom is not None else (listed or 0.0)
    if custom is not None:
        return Cost(round(custom, 6), round(custom, 6), "custom", key)
    if (spec and spec.local) or provider in FREE_PROVIDERS:
        return Cost(0.0, round(listed or 0.0, 6), "local" if spec and spec.local else "free", key)
    if provider == "openrouter" and model.endswith(":free"):
        return Cost(0.0, 0.0, "free_model", key)
    if billing_of(provider, settings) == "free":
        return Cost(0.0, round(value, 6), "free", key)
    if listed is None:
        return Cost(0.0, 0.0, "unknown", None)
    return Cost(round(listed, 6), round(listed, 6), "list", key)


def describe_price(provider: str, model: str, kind: str) -> dict | None:
    """Human-friendly list price for one model, for tooltips and the price table."""
    key, row = list_price(provider, model)
    if not row:
        return None
    out: dict = {"key": key}
    if row.get("input_cost_per_token") is not None:
        out["input_per_million"] = round(row["input_cost_per_token"] * 1_000_000, 4)
    if row.get("output_cost_per_token") is not None:
        out["output_per_million"] = round(row["output_cost_per_token"] * 1_000_000, 4)
    per_image = row.get("output_cost_per_image") or row.get("input_cost_per_image")
    if per_image:
        out["per_image"] = per_image
    if row.get("output_cost_per_second"):
        out["per_second"] = row["output_cost_per_second"]
    if row.get("input_cost_per_character"):
        out["per_million_chars"] = round(row["input_cost_per_character"] * 1_000_000, 4)
    return out
