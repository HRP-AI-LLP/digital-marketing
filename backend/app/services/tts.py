"""Voice-over: Microsoft Edge voices (free, default) or OpenAI text-to-speech.

`synthesize(session, text, out_path, voice=None)` writes an MP3 and returns duration plus word timings
(Edge voices report word boundaries; for OpenAI the timings are estimated from the text).
"""

from __future__ import annotations

import logging
import re
import uuid
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import LlmUsage, ProviderConfig
from app.services.image_gen import http_client
from app.services.llm.catalog import BY_ID

log = logging.getLogger("media.tts")

DEFAULT_VOICE = "en-IN-NeerjaNeural"
VOICE_LOCALES = ("en-IN", "en-US", "en-GB", "hi-IN")
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")


class TtsError(Exception):
    pass


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class TtsResult:
    path: Path
    duration: float
    provider: str
    voice: str
    words: list[Word] = field(default_factory=list)


async def _tts_configs(session: AsyncSession) -> list[ProviderConfig]:
    q = select(ProviderConfig).where(ProviderConfig.kind == "tts", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at)
    return list((await session.scalars(q)).all())


async def default_voice(session: AsyncSession) -> tuple[str, str]:
    """(provider, voice) used when the caller doesn't pick one."""
    for c in await _tts_configs(session):
        v = (c.settings or {}).get("voice") or (c.settings or {}).get("default_model")
        if c.provider == "edge_tts":
            return "edge_tts", v if v and "Neural" in v else DEFAULT_VOICE
        if c.provider == "openai_tts" and decrypt_json(c.secrets_enc).get("api_key"):
            return "openai_tts", (c.settings or {}).get("voice") or "alloy"
    return "edge_tts", DEFAULT_VOICE


def _estimate_words(text: str, duration: float) -> list[Word]:
    words = re.findall(r"\S+", text)
    if not words or duration <= 0:
        return []
    weights = [len(w) + 2 for w in words]
    total = sum(weights)
    t, out = 0.0, []
    for w, k in zip(words, weights):
        d = duration * k / total
        out.append(Word(round(t, 3), round(t + d, 3), w))
        t += d
    return out


async def _edge(text: str, voice: str, out: Path, rate: str = "+0%") -> list[Word]:
    try:
        import edge_tts
    except ImportError as e:  # pragma: no cover
        raise TtsError("edge-tts isn't installed. Run: pip install edge-tts") from e
    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    except TypeError:  # older edge-tts without the boundary argument
        comm = edge_tts.Communicate(text, voice, rate=rate)
    words: list[Word] = []
    with open(out, "wb") as fh:
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                fh.write(chunk["data"])
            elif chunk.get("type") == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                words.append(Word(round(start, 3), round(start + chunk["duration"] / 10_000_000, 3), chunk.get("text", "")))
    if out.stat().st_size == 0:
        raise TtsError("The voice service returned no audio.")
    return words


async def _openai(cfg: ProviderConfig, text: str, voice: str, out: Path) -> None:
    key = decrypt_json(cfg.secrets_enc).get("api_key")
    spec = BY_ID.get(cfg.provider)
    base = (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")
    model = (cfg.settings or {}).get("default_model") or "gpt-4o-mini-tts"
    body = {"model": model, "input": text[:4000], "voice": voice if voice in OPENAI_VOICES else "alloy", "response_format": "mp3"}
    instructions = (cfg.settings or {}).get("instructions")
    if instructions and "tts-1" not in model:
        body["instructions"] = instructions
    async with http_client(120) as http:
        r = await http.post(f"{base}/audio/speech", json=body, headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            raise TtsError(f"OpenAI text-to-speech: {r.status_code} {r.text[:200]}")
        out.write_bytes(r.content)


async def tts_chain(session: AsyncSession, voice: str | None = None, provider: str | None = None) -> list[tuple]:
    """Ordered (cfg | None, provider, voice) steps: the caller's pick, the 'tts' route, other voice
    integrations, then the free Edge voice as a last resort."""
    from app.models import TaskRoute

    cfgs = await _tts_configs(session)
    by_id = {c.id: c for c in cfgs}
    steps: list[tuple] = []

    def add(cfg, prov, v):
        key = (prov, v)
        if v and key not in {(p, vv) for _, p, vv in steps}:
            steps.append((cfg, prov, v))

    if voice or provider:
        prov = provider or ("openai_tts" if voice in OPENAI_VOICES else "edge_tts")
        add(next((c for c in cfgs if c.provider == prov), None), prov, voice or (DEFAULT_VOICE if prov == "edge_tts" else "alloy"))
    route = await session.scalar(select(TaskRoute).where(TaskRoute.task == "tts"))
    for st in (route.chain if route else []) or []:
        try:
            cfg = by_id.get(uuid.UUID(str(st.get("provider_id"))))
        except ValueError:
            cfg = None
        if cfg:
            add(cfg, cfg.provider, st.get("model") or _cfg_voice(cfg))
    for c in cfgs:
        add(c, c.provider, _cfg_voice(c))
    add(next((c for c in cfgs if c.provider == "edge_tts"), None), "edge_tts", DEFAULT_VOICE)
    return steps


def _cfg_voice(cfg: ProviderConfig) -> str:
    st = cfg.settings or {}
    if cfg.provider == "openai_tts":
        return st.get("voice") or "alloy"
    v = st.get("voice") or st.get("default_model")
    return v if v and "Neural" in v else DEFAULT_VOICE


async def synthesize(session: AsyncSession, text: str, out_path: Path, *, voice: str | None = None,
                     provider: str | None = None, rate: str = "+0%") -> TtsResult:
    """Voice-over with automatic fallback: if a voice service fails or hits its limit, the next one is used,
    ending with the free Edge voices."""
    from app.services.llm import health
    from app.services.media_render import probe

    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        raise TtsError("There's no voice-over text.")
    steps = await tts_chain(session, voice, provider)
    with_cfg = [(c, v, p) for c, p, v in steps if c is not None]
    ordered, _ = await health.order_chain(session, with_cfg)
    steps = [(c, p, v) for c, v, p in ordered] + [s for s in steps if s[0] is None]
    errors: list[str] = []
    for cfg, prov, v in steps:
        if cfg is not None:
            from app.services.llm.router import over_budget

            if await over_budget(session, cfg):
                errors.append(f"{cfg.name}: monthly budget reached")
                continue
        t0 = time.perf_counter()
        words: list[Word] = []
        try:
            if prov == "openai_tts":
                if cfg is None:
                    raise TtsError("OpenAI text-to-speech isn't set up.")
                await _openai(cfg, text, v, out_path)
            else:
                words = await _edge(text, v, out_path, rate)
            info = await probe(out_path)
            duration = float(info.get("duration") or 0) or (words[-1].end if words else len(text) / 14)
        except Exception as e:  # noqa: BLE001
            _usage(session, cfg, prov, v, ok=False, error=str(e)[:500], latency=int((time.perf_counter() - t0) * 1000))
            if cfg is not None:
                await health.record_failure(cfg, v, e)
            errors.append(f"{cfg.name if cfg else prov} ({v}): {str(e)[:200]}")
            continue
        _usage(session, cfg, prov, v, ok=True, units=int(round(duration)), latency=int((time.perf_counter() - t0) * 1000))
        if cfg is not None:
            await health.record_success(cfg, v)
        return TtsResult(out_path, duration, prov, v, words or _estimate_words(text, duration))
    raise TtsError("All voice-over services failed. " + " | ".join(errors))


def _usage(session, cfg: ProviderConfig | None, provider: str, voice: str, *, ok: bool, units: int = 0,
           error: str | None = None, latency: int = 0) -> None:
    org_id = cfg.organization_id if cfg is not None else session.info.get("org_id")
    if org_id is None:
        return
    prices = ((cfg.settings or {}).get("prices") or {}) if cfg is not None else {}
    try:
        per_min = float(prices.get("minute") or 0)
    except (TypeError, ValueError):
        per_min = 0.0
    session.add(LlmUsage(organization_id=org_id, provider_id=cfg.id if cfg is not None else None, provider=provider,
                         model=voice, task="tts", kind="tts", units=units,
                         cost_usd=round(per_min * units / 60, 6) if ok else 0, ok=ok, error=error, latency_ms=latency))


# ----------------------------------------------------------------------------- voices
_VOICES: dict = {"at": 0.0, "items": []}


async def list_voices() -> list[dict]:
    """Edge voices for en-IN, en-US, en-GB and hi-IN (cached for a day), plus OpenAI voice names."""
    if not _VOICES["items"] or time.time() - _VOICES["at"] > 86400:
        items: list[dict] = []
        try:
            import edge_tts

            for v in await edge_tts.list_voices():
                loc = v.get("Locale", "")
                if loc in VOICE_LOCALES:
                    items.append({"provider": "edge_tts", "id": v.get("ShortName"), "locale": loc, "gender": v.get("Gender"),
                                  "label": f"{v.get('ShortName', '').split('-')[-1].replace('Neural', '')} ({loc}, {v.get('Gender', '').lower()})"})
        except Exception as e:  # noqa: BLE001 - offline: offer a fixed list
            log.info("edge voice list unavailable: %s", e)
            for vid, loc, g in [("en-IN-NeerjaNeural", "en-IN", "Female"), ("en-IN-PrabhatNeural", "en-IN", "Male"),
                                ("en-US-AriaNeural", "en-US", "Female"), ("en-US-GuyNeural", "en-US", "Male"),
                                ("en-GB-SoniaNeural", "en-GB", "Female"), ("en-GB-RyanNeural", "en-GB", "Male"),
                                ("hi-IN-SwaraNeural", "hi-IN", "Female"), ("hi-IN-MadhurNeural", "hi-IN", "Male")]:
                items.append({"provider": "edge_tts", "id": vid, "locale": loc, "gender": g,
                              "label": f"{vid.split('-')[-1].replace('Neural', '')} ({loc}, {g.lower()})"})
            _VOICES.update(items=items, at=time.time() - 86400 + 600)  # retry in 10 minutes
            return list(items)
        items.sort(key=lambda v: (VOICE_LOCALES.index(v["locale"]), v["id"]))
        _VOICES.update(items=items, at=time.time())
    return list(_VOICES["items"])


# ----------------------------------------------------------------------------- connection tests
from app.api.integrations import tester  # noqa: E402


@tester("edge_tts")
async def _test_edge(cfg: ProviderConfig) -> str:
    import edge_tts

    voices = await edge_tts.list_voices()
    n = sum(1 for v in voices if v.get("Locale") in VOICE_LOCALES)
    v = (cfg.settings or {}).get("voice") or DEFAULT_VOICE
    return f"Connected. {n} English and Hindi voices available. Default voice: {v}."


@tester("openai_tts")
async def _test_openai_tts(cfg: ProviderConfig) -> str:
    key = decrypt_json(cfg.secrets_enc).get("api_key")
    if not key:
        raise TtsError("Add the API key.")
    spec = BY_ID.get(cfg.provider)
    base = (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")
    async with http_client(20) as http:
        r = await http.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            raise TtsError(f"{r.status_code} {r.text[:200]}")
    return f"Connected. Voice-overs will use {(cfg.settings or {}).get('default_model') or 'gpt-4o-mini-tts'}."
