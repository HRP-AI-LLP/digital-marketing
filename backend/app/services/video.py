"""Short-video pipelines with ffmpeg (imageio-ffmpeg's binary).

build_video(scenes, out, width, height, voice_clips, music):
  1. every scene becomes a silent segment: Ken Burns on stills (zoompan over a supersampled frame, so the
     motion is smooth) or a scaled/cropped/looped clip; its caption overlay PNG fades in on top;
  2. segments are joined with short crossfades (xfade);
  3. voice-over clips are placed at their scene start (adelay); optional music loops underneath and is ducked
     under the voice (sidechaincompress), with fades;
  4. H.264 (yuv420p, high profile) + AAC, `-movflags +faststart`, capped at 60 s.

Optional: `replicate_generate(...)` creates an AI clip with a configured Replicate video model (paid).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import LlmUsage, ProviderConfig
from app.services.image_gen import http_client
from app.services.llm.catalog import BY_ID
from app.services.media_render import (  # noqa: F401
    RenderError,
    ffmpeg_exe,
    poster_frame,
    probe,
    run_ffmpeg,
)

log = logging.getLogger("media.video")

MAX_SECONDS = 60.0
XFADE = 0.4
FINAL_PRESET = "veryfast"  # x264 preset for the finished file (medium is ~3x slower for little gain at 1080p social sizes)
SUPERSAMPLE = 1.5  # Ken Burns works on a frame this much larger than the output, for smooth sub-pixel motion


class VideoError(Exception):
    pass


@dataclass
class Scene:
    duration: float
    visual: Path | None = None  # still image or video clip; None = solid brand colour
    visual_kind: str = "image"  # image | video
    overlay: Path | None = None  # full-frame transparent PNG (captions, logo)
    zoom: str = "in"  # Ken Burns direction: in | out | left | right
    color: str = "#062f59"
    transition: str = "fade"  # xfade into the NEXT scene: fade | smoothleft | slideup | zoomin | hblur | wipeleft | ...


def _even(n: int) -> int:
    return n - n % 2


async def make_segment(scene: Scene, out: Path, width: int, height: int, *, fps: int = 30, tail: float = 0.0) -> Path:
    """Render one silent scene segment of `scene.duration + tail` seconds."""
    w, h = _even(width), _even(height)
    dur = max(0.5, scene.duration + tail)
    frames = max(1, round(dur * fps))
    args: list[str] = []
    if scene.visual is not None and scene.visual_kind == "video":
        args += ["-stream_loop", "-1", "-i", str(scene.visual)]
        base = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},"
                f"fps={fps},setsar=1,trim=duration={dur:.3f},setpts=PTS-STARTPTS[bg]")
    elif scene.visual is not None:
        args += ["-i", str(scene.visual)]
        # eased Ken Burns (smoothstep), so moves start and settle gently
        e = f"(3*pow(on/{frames},2)-2*pow(on/{frames},3))"
        z = {"in": f"1+0.12*{e}", "out": f"1.12-0.12*{e}"}.get(scene.zoom, "1.10")
        x = {"left": f"(iw-iw/zoom)*(1-{e})", "right": f"(iw-iw/zoom)*{e}"}.get(scene.zoom, "(iw-iw/zoom)/2")
        sw, sh = _even(int(w * SUPERSAMPLE)), _even(int(h * SUPERSAMPLE))
        base = (f"[0:v]scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos,crop={sw}:{sh},setsar=1,"
                f"zoompan=z='{z}':x='{x}':y='(ih-ih/zoom)/2':d={frames}:s={w}x{h}:fps={fps},"
                f"trim=duration={dur:.3f},setpts=PTS-STARTPTS[bg]")
    else:
        args += ["-f", "lavfi", "-i", f"color=c={scene.color}:s={w}x{h}:r={fps}:d={dur:.3f}"]
        base = "[0:v]setsar=1[bg]"
    if scene.overlay is not None:
        # decode the PNG once and repeat it in memory (re-decoding a 1080x1920 PNG per frame is slow)
        args += ["-i", str(scene.overlay)]
        graph = (f"{base.removesuffix('[bg]')},format=yuv420p[bgy];[1:v]scale={w}:{h},format=yuva420p,loop=loop=-1:size=1:start=0,setpts=N/{fps}/TB,"
                 f"trim=duration={dur:.3f},fade=t=in:st=0.15:d=0.35:alpha=1[ov];"
                 f"[bgy][ov]overlay=0:0:format=yuv420:shortest=1[v]")
    else:
        graph = f"{base};[bg]format=yuv420p[v]"
    args += ["-filter_complex", graph, "-map", "[v]", "-t", f"{dur:.3f}", "-r", str(fps), "-an",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "15", "-pix_fmt", "yuv420p", str(out)]
    await run_ffmpeg(args, timeout_s=300)
    return out


XFADES = {"fade", "smoothleft", "smoothright", "slideup", "slideleft", "zoomin", "hblur", "wipeleft", "wipeup", "circleopen", "fadeblack", "dissolve"}


async def build_video(scenes: list[Scene], out: Path, width: int, height: int, *, work: Path,
                      voice_clips: list[tuple[Path, float]] | None = None, music: Path | None = None,
                      fps: int = 30, xfade: float = XFADE, music_volume: float = 0.32, overlay_video: Path | None = None,
                      overlay_until: float | None = None, progress_color: str | None = None) -> dict:
    """Compose scenes + audio into a finished MP4. Returns {duration, width, height}.

    overlay_video: a full-frame track with alpha (captions, subtitles, progress bar) laid over the scenes from t=0
    until `overlay_until` seconds (the end card stays clean)."""
    if not scenes:
        raise VideoError("A video needs at least one scene.")
    w, h = _even(width), _even(height)
    # cap the total length
    total, kept = 0.0, []
    for s in scenes:
        if total >= MAX_SECONDS:
            break
        s.duration = min(s.duration, MAX_SECONDS - total)
        if s.duration < 0.5:
            break
        kept.append(s)
        total += s.duration
    scenes = kept
    xf = xfade if len(scenes) > 1 else 0.0
    segs: list[Path] = []
    for i, s in enumerate(scenes):
        seg = work / f"seg_{i:02d}.mp4"
        await make_segment(s, seg, w, h, fps=fps, tail=xf if i < len(scenes) - 1 else 0.0)
        segs.append(seg)
    total = sum(s.duration for s in scenes)

    args: list[str] = []
    for seg in segs:
        args += ["-i", str(seg)]
    parts: list[str] = []
    if len(segs) == 1:
        parts.append("[0:v]null[vbase]")
    else:
        prev, offset = "[0:v]", 0.0
        for i in range(1, len(segs)):
            offset += scenes[i - 1].duration
            label = "[vbase]" if i == len(segs) - 1 else f"[x{i}]"
            tr = scenes[i - 1].transition if scenes[i - 1].transition in XFADES else "fade"
            parts.append(f"{prev}[{i}:v]xfade=transition={tr}:duration={xf:.3f}:offset={offset:.3f}{label}")
            prev = label
    n = len(segs)
    if overlay_video is not None and Path(overlay_video).exists():
        if str(overlay_video).endswith(".ffconcat"):
            args += ["-f", "concat", "-safe", "0", "-i", str(overlay_video)]
        else:
            args += ["-i", str(overlay_video)]
        until = overlay_until if overlay_until is not None else total
        parts.append(f"[{n}:v]fps={fps},format=yuva420p,setpts=PTS-STARTPTS[ovl]")
        parts.append(f"[vbase][ovl]overlay=0:0:eof_action=pass:enable='lt(t,{until:.3f})':format=auto[vov]")
        n += 1
    else:
        parts.append("[vbase]null[vov]")
    if progress_color:  # progress bar along the top edge, drawn per frame by ffmpeg
        bh = max(4, round(min(w, h) / 135))
        until = overlay_until if overlay_until is not None else total
        args += ["-f", "lavfi", "-i", f"color=c={progress_color}:s={w}x{bh}:r={fps}:d={total:.3f}"]
        parts.append(f"[{n}:v]format=yuv420p[pbar]")
        parts.append(f"[vov][pbar]overlay=x='-W+W*min(t/{until:.3f}\\,1)':y=0:enable='lt(t,{until:.3f})',format=yuv420p[vout]")
        n += 1
    else:
        parts.append("[vov]format=yuv420p[vout]")
    voice_clips = [(p, st) for p, st in (voice_clips or []) if p and Path(p).exists() and st < total]
    for p, _ in voice_clips:
        args += ["-i", str(p)]
    vlabels = []
    for k, (_, st) in enumerate(voice_clips):
        ms = int(st * 1000)
        parts.append(f"[{n + k}:a]aformat=sample_rates=44100:channel_layouts=stereo,adelay={ms}|{ms}[vo{k}]")
        vlabels.append(f"[vo{k}]")
    has_voice = bool(vlabels)
    if has_voice:
        if len(vlabels) == 1:
            parts.append(f"{vlabels[0]}apad,atrim=0:{total:.3f}[voice]")
        else:
            parts.append(f"{''.join(vlabels)}amix=inputs={len(vlabels)}:normalize=0:dropout_transition=0,apad,atrim=0:{total:.3f}[voice]")
    mi = n + len(voice_clips)
    if music is not None and Path(music).exists():
        args += ["-stream_loop", "-1", "-i", str(music)]
        fade_out = max(0.0, total - 1.5)
        parts.append(f"[{mi}:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                     f"volume={music_volume:.2f},afade=t=in:st=0:d=1,afade=t=out:st={fade_out:.3f}:d=1.5[mus]")
        if has_voice:
            parts.append("[voice]asplit=2[vmain][vkey]")
            parts.append("[mus][vkey]sidechaincompress=threshold=0.03:ratio=12:attack=20:release=400:makeup=1[duck]")
            parts.append("[vmain][duck]amix=inputs=2:normalize=0:duration=first[aout]")
        else:
            parts.append("[mus]anull[aout]")
    elif has_voice:
        parts.append("[voice]anull[aout]")
    else:
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        parts.append(f"[{mi}:a]anull[aout]")
    args += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", FINAL_PRESET, "-crf", "19", "-maxrate", "12M", "-bufsize", "24M",
             "-profile:v", "high", "-level:v", "4.2", "-pix_fmt", "yuv420p",
             "-r", str(fps), "-g", str(fps * 2), "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
             "-t", f"{min(total, MAX_SECONDS):.3f}", "-movflags", "+faststart", str(out)]
    await run_ffmpeg(args, timeout_s=600)
    info = await probe(out)
    return {"duration": info.get("duration") or total, "width": info.get("width") or w, "height": info.get("height") or h,
            "has_audio": info.get("has_audio")}


async def fit_clip(src: Path, out: Path, width: int, height: int, max_seconds: float = MAX_SECONDS) -> Path:
    """Scale/crop any clip to width x height H.264/AAC (used for uploads that need a platform format)."""
    w, h = _even(width), _even(height)
    await run_ffmpeg(["-i", str(src), "-t", f"{max_seconds:.2f}",
                      "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p",
                      "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
                      "-movflags", "+faststart", str(out)])
    return out


# ----------------------------------------------------------------------------- AI video (optional, paid)
async def video_provider(session: AsyncSession) -> ProviderConfig | None:
    q = select(ProviderConfig).where(ProviderConfig.kind == "video", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at)
    for c in (await session.scalars(q)).all():
        if decrypt_json(c.secrets_enc).get("api_key"):
            return c
    return None


async def video_chain(session: AsyncSession) -> list[tuple[ProviderConfig, str]]:
    """'video' route first (in order), then any other enabled video integration with a key."""
    import uuid as _uuid

    from app.models import TaskRoute

    q = select(ProviderConfig).where(ProviderConfig.kind == "video", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at)
    cfgs = [c for c in (await session.scalars(q)).all() if decrypt_json(c.secrets_enc).get("api_key")]
    by_id = {c.id: c for c in cfgs}
    chain: list[tuple[ProviderConfig, str]] = []
    route = await session.scalar(select(TaskRoute).where(TaskRoute.task == "video"))
    for st in (route.chain if route else []) or []:
        try:
            cfg = by_id.get(_uuid.UUID(str(st.get("provider_id"))))
        except ValueError:
            cfg = None
        if cfg and st.get("model"):
            chain.append((cfg, st["model"]))
    for c in cfgs:
        spec = BY_ID.get(c.provider)
        m = (c.settings or {}).get("default_model") or (spec.examples[0] if spec and spec.examples else "")
        if (c, m) not in chain:
            chain.append((c, m))
    return chain


async def replicate_generate(session: AsyncSession, prompt: str, out: Path, *, aspect_ratio: str = "9:16",
                             timeout_s: float = 900, poll_s: float = 5) -> Path:
    """AI clip from the first video model that works; falls back along the chain on limits or errors."""
    from app.services.llm import health
    from app.services.llm.router import over_budget

    chain, _ = await health.order_chain(session, await video_chain(session))
    if not chain:
        raise VideoError("No AI video provider is set up.")
    errors: list[str] = []
    for cfg, model in chain:
        if await over_budget(session, cfg):
            errors.append(f"{cfg.name}: monthly budget reached")
            continue
        try:
            result = await _replicate_one(session, cfg, model, prompt, out, aspect_ratio=aspect_ratio,
                                          timeout_s=timeout_s, poll_s=poll_s)
        except Exception as e:  # noqa: BLE001
            v = await health.record_failure(cfg, model, e)
            errors.append(f"{cfg.name} ({model}): {(v.reason + '. ') if v.reason else ''}{str(e)[:200]}")
            continue
        await health.record_success(cfg, model)
        return result
    raise VideoError("All AI video providers failed. " + " | ".join(errors))


async def _replicate_one(session: AsyncSession, cfg: ProviderConfig, model: str, prompt: str, out: Path, *,
                         aspect_ratio: str, timeout_s: float, poll_s: float) -> Path:
    key = decrypt_json(cfg.secrets_enc).get("api_key")
    spec = BY_ID.get(cfg.provider)
    base = (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    ok, err = False, None
    try:
        async with http_client(60) as http:
            inp = {"prompt": prompt[:2000], "aspect_ratio": aspect_ratio, **((cfg.settings or {}).get("input") or {})}
            r = await http.post(f"{base}/models/{model}/predictions", json={"input": inp}, headers=headers)
            if r.status_code >= 400:
                raise VideoError(f"Replicate: {r.status_code} {r.text[:300]}")
            pred = r.json()
            deadline = time.monotonic() + timeout_s
            while pred.get("status") not in ("succeeded", "failed", "canceled"):
                if time.monotonic() > deadline:
                    raise VideoError("Replicate: timed out waiting for the video.")
                await asyncio.sleep(poll_s)
                url = (pred.get("urls") or {}).get("get") or f"{base}/predictions/{pred.get('id')}"
                pr = await http.get(url, headers=headers)
                if pr.status_code >= 400:
                    raise VideoError(f"Replicate: {pr.status_code} {pr.text[:300]}")
                pred = pr.json()
            if pred.get("status") != "succeeded":
                raise VideoError(f"Replicate: {pred.get('status')} — {str(pred.get('error'))[:300]}")
            output = pred.get("output")
            link = output[0] if isinstance(output, list) and output else output
            if not isinstance(link, str):
                raise VideoError("Replicate returned no video.")
            dr = await http.get(link)
            if dr.status_code >= 400:
                raise VideoError(f"Download failed: {dr.status_code}")
            out.write_bytes(dr.content)
        ok = True
        return out
    except Exception as e:
        err = str(e)[:1000]
        raise
    finally:
        prices = (cfg.settings or {}).get("prices") or {}
        try:
            price = float(prices.get("video") or 0)
        except (TypeError, ValueError):
            price = 0.0
        session.add(LlmUsage(organization_id=cfg.organization_id, provider_id=cfg.id, provider=cfg.provider, model=model or "-",
                             task="video", kind="video", units=1 if ok else 0, cost_usd=price if ok else 0, ok=ok, error=err,
                             latency_ms=int((time.perf_counter() - t0) * 1000)))


from app.api.integrations import tester  # noqa: E402


@tester("replicate")
async def _test_replicate(cfg: ProviderConfig) -> str:
    key = decrypt_json(cfg.secrets_enc).get("api_key")
    if not key:
        raise VideoError("Add the API token.")
    spec = BY_ID.get(cfg.provider)
    base = (cfg.base_url or (spec.base_url if spec else "")).rstrip("/")
    async with http_client(20) as http:
        r = await http.get(f"{base}/account", headers={"Authorization": f"Bearer {key}"})
        if r.status_code >= 400:
            raise VideoError(f"{r.status_code} {r.text[:200]}")
        user = r.json().get("username")
    model = (cfg.settings or {}).get("default_model") or "minimax/video-01"
    return f"Signed in as {user}. AI clips will use {model} (paid per clip)."
