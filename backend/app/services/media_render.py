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
import glob
import io
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from app.core.config import get_settings

log = logging.getLogger("media.render")

MAX_PAGES = 3  # concurrent pages per process


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
                "--hide-scrollbars", "--disable-lcd-text"]
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
async def _page(width: int, height: int):
    st = _loop_state()
    async with st["sem"]:
        browser = await get_browser()
        ctx = await browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                        java_script_enabled=True, offline=True)
        try:
            page = await ctx.new_page()
            yield page
        finally:
            await ctx.close()


async def _load(page, html: str, timeout_s: float) -> None:
    await page.set_content(html, wait_until="load", timeout=timeout_s * 1000)
    try:
        await page.wait_for_function("document.body && document.body.getAttribute('data-ready')==='1'",
                                     timeout=min(8000, timeout_s * 1000))
    except Exception:  # noqa: BLE001 - custom HTML without the fitter: render as is
        pass


def _check_size(width: int, height: int) -> None:
    if not (16 <= width <= 4096 and 16 <= height <= 4096):
        raise RenderError("Size must be between 16 and 4096 pixels.")


async def render_png(html: str, width: int, height: int, *, transparent: bool = False, timeout_s: float = 20) -> bytes:
    _check_size(width, height)
    async with _page(width, height) as page:
        await _load(page, html, timeout_s)
        # freeze any animations at their end state for stills
        await page.evaluate("() => document.getAnimations().forEach(a => { try { a.finish(); } catch (e) { a.pause(); } })")
        return await page.screenshot(type="png", omit_background=transparent,
                                     clip={"x": 0, "y": 0, "width": width, "height": height}, timeout=timeout_s * 1000)


_SEEK_JS = "(t) => { document.getAnimations().forEach(a => { a.pause(); a.currentTime = t; }); }"


async def render_video(html: str, width: int, height: int, duration: float, out_path: Path, *, fps: int = 30,
                       timeout_s: float = 240) -> Path:
    """Record a CSS-animated page to MP4 by seeking animations frame by frame."""
    _check_size(width, height)
    w2, h2 = width - width % 2, height - height % 2
    frames = max(1, round(duration * fps))
    args = ["-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
            "-vf", f"scale={w2}:{h2}:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-r", str(fps), str(out_path)]
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        async with _page(width, height) as page:
            await _load(page, html, 20)
            for i in range(frames):
                await page.evaluate(_SEEK_JS, i * 1000.0 / fps)
                shot = await page.screenshot(type="jpeg", quality=94, clip={"x": 0, "y": 0, "width": width, "height": height})
                proc.stdin.write(shot)
                await proc.stdin.drain()
        proc.stdin.close()
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        raise
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed: {err.decode(errors='ignore')[-600:]}")
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

