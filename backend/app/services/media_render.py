"""Headless Chromium renderer (Playwright) + ffmpeg encoders.

- One browser per process, launched lazily under a lock and relaunched if it dies or the event loop changes.
- `render_png(html, w, h)` renders at exactly w x h (device scale 1).
- `render_video(html, w, h, duration, fps, out)` pauses every CSS animation and seeks it frame by frame
  (deterministic output), piping JPEG frames into ffmpeg -> H.264 MP4 (yuv420p, faststart).
- `mp4_to_gif(...)` makes a palette-optimised GIF under a size cap.

Chromium: settings.playwright_chromium_path, else /opt/pw-browsers/chromium-*/chrome-linux/chrome when present
(dev container), else Playwright's own download (`python -m playwright install chromium`).
"""

from __future__ import annotations

import asyncio
import base64
import glob
import io
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.config import get_settings

log = logging.getLogger("media.render")

MAX_PAGES = 3  # concurrent pages per process
# WebGL for the 3D scenes: use the GPU when there is one, otherwise ANGLE's SwiftShader software rasteriser
# (works headless on servers without a GPU; measured ~250 ms per 540x960 frame on 2 vCPUs).
GL_ARGS = ["--ignore-gpu-blocklist", "--enable-unsafe-swiftshader", "--enable-webgl"]
JPEG_Q = 92


class RenderError(Exception):
    pass


# ----------------------------------------------------------------------------- browser lifecycle
_state: dict = {"loop": None, "pw": None, "browser": None, "lock": None, "sem": None}


def chromium_path() -> str | None:
    p = (get_settings().playwright_chromium_path or "").strip()
    if p and Path(p).exists():
        return p
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome", "/opt/pw-browsers/chromium-*/chrome-linux64/chrome"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None


def _kill_driver(st: dict) -> None:
    """Hard-stop a Playwright driver started on another (possibly closed) event loop; Chromium exits with it."""
    try:
        proc = st["pw"]._connection._transport._proc  # noqa: SLF001 - no public API for this
        if proc.returncode is None:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _loop_state() -> dict:
    loop = asyncio.get_running_loop()
    if _state["loop"] is not loop:
        if _state.get("pw") is not None:
            _kill_driver(_state)
        _state.update(loop=loop, pw=None, browser=None, lock=asyncio.Lock(), sem=asyncio.Semaphore(MAX_PAGES))
    return _state


async def get_browser():
    st = _loop_state()
    async with st["lock"]:
        b = st["browser"]
        if b is not None and b.is_connected():
            return b
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover
            raise RenderError("Playwright isn't installed. Run: pip install playwright") from e
        args = ["--force-color-profile=srgb", "--font-render-hinting=none", "--disable-dev-shm-usage",
                "--hide-scrollbars", "--disable-lcd-text", *GL_ARGS]
        exe = chromium_path()
        try:
            if st["pw"] is None:
                st["pw"] = await async_playwright().start()
            st["browser"] = await st["pw"].chromium.launch(executable_path=exe, args=args, headless=True)
        except NotImplementedError as e:
            raise RenderError("The renderer needs an event loop with subprocess support. On Windows, start the API "
                              "without --reload (the default Proactor loop works).") from e
        except Exception as e:  # noqa: BLE001
            raise RenderError(
                "Couldn't start the image renderer (Chromium). Run `python -m playwright install chromium` once, "
                f"or set PLAYWRIGHT_CHROMIUM_PATH. Details: {str(e).splitlines()[0][:300]}"
            ) from e
        log.info("chromium started (%s)", exe or "playwright default")
        return st["browser"]


async def shutdown() -> None:
    """Close the browser. From a different event loop than the one that started it, the driver is killed."""
    st = _state
    if st.get("pw") is not None and st.get("loop") is not asyncio.get_running_loop():
        _kill_driver(st)
        st.update(browser=None, pw=None, loop=None)
        return
    for what, fn in (("browser", lambda: st["browser"].close()), ("pw", lambda: st["pw"].stop())):
        if st.get(what) is None:
            continue
        try:
            await asyncio.wait_for(fn(), timeout=10)
        except Exception as e:  # noqa: BLE001
            log.info("renderer shutdown (%s): %s", what, e)
    st.update(browser=None, pw=None)


@asynccontextmanager
async def _page(width: int, height: int, assets: dict[str, tuple[bytes, str]] | None = None):
    """A fresh offline page. `assets`: {url: (bytes, mime)} served from memory (3D models); every other
    request is refused, so templates can never pull anything from a CDN or the network."""
    st = _loop_state()
    async with st["sem"]:
        browser = await get_browser()
        ctx = await browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                        java_script_enabled=True, offline=True)
        try:
            page = await ctx.new_page()
            if assets:
                async def handle(route):
                    hit = assets.get(route.request.url)
                    if hit:
                        await route.fulfill(body=hit[0], content_type=hit[1], headers={"Access-Control-Allow-Origin": "*"})
                    else:
                        await route.abort()
                await page.route("**/*", handle)
            yield page
        finally:
            await ctx.close()


async def _load(page, html: str, timeout_s: float) -> None:
    await page.set_content(html, wait_until="load", timeout=timeout_s * 1000)
    try:
        await page.wait_for_function("document.body && document.body.getAttribute('data-ready')==='1'",
                                     timeout=min(30000, timeout_s * 1000))
    except Exception:  # noqa: BLE001 - custom HTML without the runtime: render as is
        pass


async def _seek_still(page) -> None:
    await page.evaluate("() => { if (window.__seek) window.__seek(window.__stillTime || 0);"
                        " document.getAnimations().forEach(a => { try { a.finish(); } catch (e) { a.pause(); } }); }")


async def page_error(page) -> str | None:
    return await page.evaluate("() => window.__glError || window.__runtimeError || null")


def _check_size(width: int, height: int) -> None:
    if not (16 <= width <= 4096 and 16 <= height <= 4096):
        raise RenderError("Size must be between 16 and 4096 pixels.")


async def render_png(html: str, width: int, height: int, *, transparent: bool = False, timeout_s: float = 20,
                     at: float | None = None, require_ok: bool = False, assets: dict | None = None) -> bytes:
    """PNG of the composed state (or the state at `at` seconds). require_ok: raise when the page reports a
    WebGL/runtime error (3D scenes use this to fall back to 2D)."""
    _check_size(width, height)
    async with _page(width, height, assets) as page:
        await _load(page, html, timeout_s)
        if require_ok:
            err = await page_error(page)
            if err:
                raise RenderError(f"Scene failed to render: {err[:300]}")
        if at is None:
            await _seek_still(page)
        else:
            await page.evaluate("(t) => window.__seek && window.__seek(t)", at)
        return await page.screenshot(type="png", omit_background=transparent,
                                     clip={"x": 0, "y": 0, "width": width, "height": height}, timeout=timeout_s * 1000)


_SEEK_CSS = "(t) => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t * 1000; }); }"
_SEEK = """(t) => { if (window.__seek) { window.__seek(t); return window.__activeAt ? !!window.__activeAt(t) : true; }
  document.getAnimations().forEach(a => { a.pause(); a.currentTime = t * 1000; }); return true; }"""
_BGL = re.compile(r"<!--bgl:(\w+)-->(.*?)<!--/bgl-->", re.S)


async def flatten_background(html: str, width: int, height: int, *, timeout_s: float = 20) -> str:
    """Replace the (expensive) layered background - blurred blobs, grain, scrims, photo - with one pre-rendered
    JPEG that keeps a gentle Ken Burns move. Cuts per-frame cost roughly in half; the look is unchanged."""
    m = _BGL.search(html)
    if not m:
        return html
    hide = "<style>.canvas>*:not(.bgl){visibility:hidden!important}</style>"
    async with _page(width, height) as page:
        await _load(page, html.replace("</head>", hide + "</head>", 1), timeout_s)
        await page.evaluate("() => { document.querySelectorAll('.bgl [data-kb],.bgl [data-drift]').forEach(e => "
                            "{ e.removeAttribute('data-kb'); e.removeAttribute('data-drift'); e.style.transform='none'; }); }")
        jpg = await page.screenshot(type="jpeg", quality=95, clip={"x": 0, "y": 0, "width": width, "height": height})
    uri = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    kb = m.group(1) if m.group(1) in ("in", "out", "left", "right", "drift") else "drift"
    loop = "data-mode='loop'" in html  # GIF loops keep a still background: smaller files, seamless loop
    kb_attr = "" if loop else f" data-kb='{kb}'"
    flat = (f"<div class='bgl layer' style='overflow:hidden'><div class='layer'{kb_attr} style=\"will-change:transform;"
            f"background:url('{uri}') center/cover\"></div></div>")
    return html[:m.start()] + flat + html[m.end():]


async def _capture(cdp, fmt: str = "jpeg") -> bytes:
    params = {"format": fmt, "fromSurface": True, "captureBeyondViewport": False}
    if fmt == "jpeg":
        params["quality"] = JPEG_Q
    r = await cdp.send("Page.captureScreenshot", params)
    return base64.b64decode(r["data"])


async def capture_frames(html: str, width: int, height: int, duration: float, fps: int, sink, *, alpha: bool = False,
                         flatten: bool = True, timeout_s: float = 30, max_frames: int = 3600, require_ok: bool = False,
                         assets: dict | None = None) -> int:
    """Seek the page frame by frame (window.__seek contract, CSS animations as fallback) and hand each frame's
    bytes to `sink(bytes)`. Frames where nothing moves reuse the previous capture. Returns frames written."""
    _check_size(width, height)
    frames = max(1, min(max_frames, round(duration * fps)))
    if flatten and not alpha:
        html = await flatten_background(html, width, height)
    async with _page(width, height, assets) as page:
        await _load(page, html, timeout_s)
        if require_ok:
            err = await page_error(page)
            if err:
                raise RenderError(f"Scene failed to render: {err[:300]}")
        cdp = await page.context.new_cdp_session(page)
        if alpha:
            await cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        last: bytes | None = None
        for i in range(frames):
            active = await page.evaluate(_SEEK, i / fps)
            if active or last is None:
                last = await _capture(cdp, "png" if alpha else "jpeg")
            r = sink(last)
            if asyncio.iscoroutine(r):
                await r
    return frames


async def render_video(html: str, width: int, height: int, duration: float, out_path: Path, *, fps: int = 30,
                       timeout_s: float = 300, crf: int = 18, require_ok: bool = False, flatten: bool = True,
                       assets: dict | None = None) -> Path:
    """Record an animated page to H.264 MP4 (yuv420p, high profile, faststart), frame-accurate."""
    _check_size(width, height)
    w2, h2 = width - width % 2, height - height % 2
    args = ["-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
            "-vf", f"scale={w2}:{h2}:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-r", str(fps), str(out_path)]
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)

    async def sink(b: bytes) -> None:
        proc.stdin.write(b)
        await proc.stdin.drain()

    try:
        await asyncio.wait_for(capture_frames(html, width, height, duration, fps, sink, flatten=flatten, require_ok=require_ok,
                                              assets=assets),
                               timeout=timeout_s)
        proc.stdin.close()
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        raise
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed: {err.decode(errors='ignore')[-600:]}")
    return out_path


async def render_alpha_video(html: str, width: int, height: int, duration: float, out_path: Path, *, fps: int = 30,
                             timeout_s: float = 600) -> Path:
    """Transparent overlay track for compositing in ffmpeg. Only frames that change are written (as PNGs with
    alpha); `out_path` is an ffconcat list giving each one its on-screen duration. Read it with
    `-f concat -safe 0 -i out_path` (build_video does). No intermediate encode."""
    folder = out_path.parent / (out_path.stem + "_frames")
    folder.mkdir(parents=True, exist_ok=True)
    entries: list[list] = []  # [file, frames]
    state = {"last": None}

    def sink(b: bytes) -> None:
        if b is state["last"] and entries:
            entries[-1][1] += 1
            return
        state["last"] = b
        f = folder / f"f{len(entries):05d}.png"
        f.write_bytes(b)
        entries.append([f, 1])

    await asyncio.wait_for(capture_frames(html, width, height, duration, fps, sink, alpha=True, max_frames=60 * fps),
                           timeout=timeout_s)
    lines = ["ffconcat version 1.0"]
    for f, n in entries:
        lines += [f"file '{f.name}'", f"duration {n / fps:.6f}"]
    if entries:
        lines.append(f"file '{entries[-1][0].name}'")  # the concat demuxer needs the last file repeated
    out_path = folder / "list.ffconcat"
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


# ----------------------------------------------------------------------------- ffmpeg helpers
def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise RenderError("ffmpeg isn't available. Run: pip install imageio-ffmpeg") from e


async def _exec(cmd: list[str], timeout_s: float) -> tuple[int, str]:
    """Run a command, returning (exit code, stderr). Falls back to a thread when the event loop has no
    subprocess support (e.g. a Windows SelectorEventLoop)."""
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except NotImplementedError:
        import subprocess

        def run() -> tuple[int, str]:
            try:
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout_s, check=False)
            except subprocess.TimeoutExpired:
                raise RenderError("ffmpeg took too long and was stopped.")
            return r.returncode, r.stderr.decode(errors="ignore")

        return await asyncio.to_thread(run)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        raise RenderError("ffmpeg took too long and was stopped.")
    return proc.returncode or 0, err.decode(errors="ignore")


async def run_ffmpeg(args: list[str], timeout_s: float = 300) -> str:
    """Run ffmpeg; returns stderr text. Raises RenderError on failure."""
    code, text = await _exec([ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", *args], timeout_s)
    if code != 0:
        tail = "\n".join(line for line in text.splitlines()[-12:] if line.strip())
        raise RenderError(f"ffmpeg failed: {tail[-800:]}")
    return text


async def probe(path: Path | str) -> dict:
    """Width, height, duration (s), has_audio, video codec — parsed from `ffmpeg -i`."""
    _, text = await _exec([ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)], 60)
    return parse_probe(text)


def parse_probe(text: str) -> dict:
    info: dict = {"width": None, "height": None, "duration": None, "has_audio": False, "has_video": False, "vcodec": None, "fps": None}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in text.splitlines():
        if "Stream #" in line and "Video:" in line and not info["has_video"]:
            info["has_video"] = True
            cm = re.search(r"Video:\s*([a-z0-9_]+)", line)
            info["vcodec"] = cm.group(1) if cm else None
            sm = re.search(r"[,\s](\d{2,5})x(\d{2,5})[,\s\[]", line)
            if sm:
                info["width"], info["height"] = int(sm.group(1)), int(sm.group(2))
            fm = re.search(r"([\d.]+)\s*fps", line)
            if fm:
                info["fps"] = float(fm.group(1))
            rm = re.search(r"rotate\s*:\s*(-?\d+)", text) or re.search(r"rotation of (-?\d+)", text)
            if rm and abs(int(float(rm.group(1)))) in (90, 270) and info["width"]:
                info["width"], info["height"] = info["height"], info["width"]
        elif "Stream #" in line and "Audio:" in line:
            info["has_audio"] = True
    return info


async def mp4_to_gif(src: Path, out: Path, *, max_width: int = 720, fps: int = 15, max_bytes: int = 8 * 1024 * 1024,
                     max_seconds: float = 6.0) -> Path:
    """Palette-optimised GIF, shrinking until it fits under max_bytes."""
    width, colors = max_width, 192
    for _ in range(5):
        vf = (f"fps={fps},scale='min({width},iw)':-2:flags=lanczos,split[a][b];"
              f"[a]palettegen=max_colors={colors}:stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle")
        await run_ffmpeg(["-t", f"{max_seconds:.2f}", "-i", str(src), "-filter_complex", vf, "-loop", "0", str(out)], timeout_s=180)
        if out.stat().st_size <= max_bytes:
            return out
        width, colors = int(width * 0.8), max(64, colors - 48)
        fps = max(10, fps - 2)
    raise RenderError("Couldn't make the GIF small enough; shorten the text or use the MP4 instead.")


async def poster_frame(src: Path, out: Path, at: float = 0.5) -> Path:
    await run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(src), "-frames:v", "1", "-q:v", "3", str(out)], timeout_s=60)
    if not out.exists():  # very short clip: take the first frame
        await run_ffmpeg(["-i", str(src), "-frames:v", "1", str(out)], timeout_s=60)
    return out


# ----------------------------------------------------------------------------- PDF from slides
def _pngs_to_pdf(pngs: list[bytes], dpi: int = 144) -> bytes:
    from PIL import Image

    imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in pngs]
    if not imgs:
        raise RenderError("No pages to put in the PDF.")
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:], resolution=dpi, quality=92)
    return buf.getvalue()


async def pngs_to_pdf(pngs: list[bytes]) -> bytes:
    return await asyncio.to_thread(_pngs_to_pdf, pngs)


def tmp_root() -> Path:
    from app.core import storage

    p = storage.root() / "tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def tmp_dir(prefix: str = "media-") -> Path:
    import tempfile

    return Path(tempfile.mkdtemp(prefix=prefix, dir=tmp_root()))


def rm_tree(p: Path | None) -> None:
    import shutil

    if p and p.exists():
        shutil.rmtree(p, ignore_errors=True)

