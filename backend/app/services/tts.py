"""Voice-over: Microsoft Edge voices (free, default), Kokoro / other OpenAI-compatible voice servers, or OpenAI.

`synthesize(session, text, out_path, voice=None)` writes an MP3 and returns duration plus word timings
(Edge voices report word boundaries; for OpenAI the timings are estimated from the text).
"""

from __future__ import annotations

from app.services.llm import keys as _keys
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

# The newest Edge voices ("Multilingual" and "Expressive") sound far more like a person than the older ones.
DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"
OLD_DEFAULT = "en-IN-NeerjaNeural"  # the default until Nov 2026: treated as "not chosen", so it moves to the new one
NATURAL = re.compile(r"Multilingual|Expressive|-(?:Ava|Andrew|Emma|Brian)Neural$")
VOICE_LOCALES = ("en-IN", "en-US", "en-GB", "hi-IN")
KOKORO_DEFAULT = ("hexgrad/Kokoro-82M", "af_heart")
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
            return "edge_tts", _edge_voice(v)
        if c.provider == "openai_tts" and decrypt_json(c.secrets_enc).get("api_key"):
            return "openai_tts", (c.settings or {}).get("voice") or "alloy"
    return "edge_tts", DEFAULT_VOICE


def _edge_voice(v: str | None) -> str:
    return v if v and "Neural" in v and v != OLD_DEFAULT else DEFAULT_VOICE


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]+")


def speakable(text: str) -> str:
    """Text as a person would read it aloud: no hashtags, emojis, links or symbols that voices spell out."""
    t = _EMOJI.sub(" ", text or "")
    t = re.sub(r"https?://(?:www\.)?([^/\s]+)\S*", r"\1", t)        # links: just the site name
    t = re.sub(r"\b([a-z0-9-]+)\.(in|com|io|ai|org|net|co)\b", r"\1 dot \2", t, flags=re.I)
    t = re.sub(r"(^|\s)#(\w+)", r"\1\2", t)                          # hashtags: the word
    t = re.sub(r"(^|\s)@(\w+)", r"\1\2", t)
    t = t.replace("&", " and ").replace("%", " percent").replace("→", ",").replace("/", " or ")
    t = re.sub(r"[*_~`|<>\[\]{}]", " ", t)
    t = re.sub(r"\s*[-–—]{1,2}\s*", ", ", t) if re.search(r"\s[-–—]{1,2}\s", t) else t
    t = re.sub(r"\s+", " ", t).strip()
    return t


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
    """OpenAI's /audio/speech, also spoken by Kokoro-FastAPI, DeepInfra and other OpenAI-compatible voice servers."""
    key = _keys.secrets_of(cfg).get("api_key")
    spec = BY_ID.get(cfg.provider)
    base = (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")
    kokoro = cfg.provider == "kokoro_tts"
    model = (cfg.settings or {}).get("default_model") or (KOKORO_DEFAULT[0] if kokoro else "gpt-4o-mini-tts")
    if not kokoro:
        voice = voice if voice in OPENAI_VOICES else "alloy"
    body = {"model": model, "input": text[:4000], "voice": voice, "response_format": "mp3"}
    instructions = (cfg.settings or {}).get("instructions")
    if instructions and "tts-1" not in model:
        body["instructions"] = instructions
    async with http_client(120) as http:
        r = await http.post(f"{base}/audio/speech", json=body, headers={"Authorization": f"Bearer {key}"} if key else {})
        if r.status_code >= 400:
            raise TtsError(f"{cfg.name}: {r.status_code} {r.text[:200]}")
        if not r.content or r.headers.get("content-type", "").startswith(("application/json", "text/")):
            raise TtsError(f"{cfg.name} returned no audio: {r.text[:200]}")
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
    if cfg.provider == "kokoro_tts":
        return st.get("voice") or KOKORO_DEFAULT[1]
    return _edge_voice(st.get("voice") or st.get("default_model"))


async def synthesize(session: AsyncSession, text: str, out_path: Path, *, voice: str | None = None,
                     provider: str | None = None, rate: str = "+0%") -> TtsResult:
    """Voice-over with automatic fallback: if a voice service fails or hits its limit, the next one is used,
    ending with the free Edge voices."""
    from app.services.llm import health
    from app.services.media_render import probe

    text = speakable(text)
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
            if prov in ("openai_tts", "kokoro_tts"):
                if cfg is None:
                    raise TtsError(f"{prov} isn't set up.")
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
    from app.services.llm import pricing

    c = pricing.cost(provider, (cfg.settings or {}) if cfg is not None else {}, voice, "tts", units=units).cost
    session.add(LlmUsage(organization_id=org_id, provider_id=cfg.id if cfg is not None else None, provider=provider,
                         model=voice, task="tts", kind="tts", units=units,
                         cost_usd=c if ok else 0, ok=ok, error=error, latency_ms=latency))


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
            for vid, loc, g in [("en-US-AndrewMultilingualNeural", "en-US", "Male"), ("en-US-AvaMultilingualNeural", "en-US", "Female"),
                                ("en-IN-NeerjaExpressiveNeural", "en-IN", "Female"),
                                ("en-IN-NeerjaNeural", "en-IN", "Female"), ("en-IN-PrabhatNeural", "en-IN", "Male"),
                                ("en-US-AriaNeural", "en-US", "Female"), ("en-US-GuyNeural", "en-US", "Male"),
                                ("en-GB-SoniaNeural", "en-GB", "Female"), ("en-GB-RyanNeural", "en-GB", "Male"),
                                ("hi-IN-SwaraNeural", "hi-IN", "Female"), ("hi-IN-MadhurNeural", "hi-IN", "Male")]:
                items.append({"provider": "edge_tts", "id": vid, "locale": loc, "gender": g,
                              "label": f"{vid.split('-')[-1].replace('Neural', '')} ({loc}, {g.lower()})"})
            _VOICES.update(items=items, at=time.time() - 86400 + 600)  # retry in 10 minutes
            return list(items)
        for v in items:
            if NATURAL.search(v["id"] or ""):
                v["natural"] = True
                v["label"] += " · most natural"
        items.sort(key=lambda v: (not v.get("natural"), VOICE_LOCALES.index(v["locale"]), v["id"]))
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
    key = _keys.secrets_of(cfg).get("api_key")
    if not key:
        raise TtsError("Add the API key.")
    spec = BY_ID.get(cfg.provider)
    base = (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")
    async with http_client(20) as http:
        r = await http.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            raise TtsError(f"{r.status_code} {r.text[:200]}")
    return f"Connected. Voice-overs will use {(cfg.settings or {}).get('default_model') or 'gpt-4o-mini-tts'}."


@tester("kokoro_tts")
async def _test_kokoro(cfg: ProviderConfig) -> str:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "t.mp3"
        await _openai(cfg, "Hello from the studio.", _cfg_voice(cfg), out)
        n = out.stat().st_size
    return f"Connected. Spoke a test line ({n // 1024} KB) with voice {_cfg_voice(cfg)}."


# ----------------------------------------------------------------------------- whole-script voice-over
async def synthesize_lines(session: AsyncSession, lines: list[str], work: Path, *, voice: str | None = None) -> list["TtsResult | None"]:
    """Voice-over for a video's scenes, spoken as ONE take so the voice keeps its natural flow and intonation from
    scene to scene (one take per scene sounds like a robot restarting). The take is cut at the scene boundaries using
    the word timings. Falls back to one take per scene when the voice gives no word timings."""
    from app.services.media_render import probe, run_ffmpeg

    spoken = [speakable(x) for x in lines]
    idx = [i for i, x in enumerate(spoken) if x]
    out: list[TtsResult | None] = [None] * len(lines)
    if not idx:
        return out
    parts = [spoken[i] if spoken[i][-1] in ".!?…" else spoken[i] + "." for i in idx]
    full_text = " ".join(parts)
    full = await synthesize(session, full_text, work / "vo_full.mp3", voice=voice)
    if len(idx) == 1:
        out[idx[0]] = full
        return out
    # each scene's character span in the spoken text, then each word timing placed by where its text is found
    spans, pos = [], 0
    for x in parts:
        spans.append((pos, pos + len(x)))
        pos += len(x) + 1
    groups: list[list[Word]] = [[] for _ in idx]
    cursor, placed = 0, 0
    low = full_text.lower()
    for w in full.words if full.provider == "edge_tts" else []:
        at = low.find((w.text or "").lower().strip(), cursor)
        if at < 0:
            continue
        cursor = at + max(1, len(w.text.strip()))
        j = next((k for k, (a, b) in enumerate(spans) if a <= at < b), None)
        if j is not None:
            groups[j].append(w)
            placed += 1
    if not full.words or placed < len(full.words) * 0.8 or any(not g for g in groups):
        for i in idx:  # no usable word timings: one take per scene
            out[i] = await synthesize(session, spoken[i], work / f"vo_{i:02d}.mp3", voice=voice)
        return out
    for j, i in enumerate(idx):
        start = max(0.0, groups[j][0].start - 0.06)
        end = (groups[j + 1][0].start - 0.04) if j + 1 < len(idx) else full.duration
        end = max(end, groups[j][-1].end + 0.05)
        path = work / f"vo_{i:02d}.mp3"
        await run_ffmpeg(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(full.path), "-c:a", "libmp3lame", "-q:a", "2", str(path)])
        info = await probe(path)
        d = float(info.get("duration") or (end - start))
        out[i] = TtsResult(path, d, full.provider, full.voice,
                           [Word(round(w.start - start, 3), round(w.end - start, 3), w.text) for w in groups[j]])
    return out
