"""Product demos: real screens of your products in posts about them.

Brand → Visuals → Product demos lists each product with words that identify it in a post (e.g. "HRMS, payroll,
attendance") and the public pages that show it working (a product page, a demo login page). "Capture" opens each page
in the studio's own browser and saves:
  - screenshots: the top of the page and two further down, on a laptop screen and on a phone screen;
  - a slow scroll-through recording of each, for videos (phone size for Shorts/Reels, laptop size for wide videos).
You can also add your own screen recordings and screenshots (e.g. of a logged-in demo) from the Media library.

A post whose topic, title or text matches a product then uses these: Shorts/Reels show the recordings and
screenshots in their scenes, and image posts show a screenshot in a device frame under the headline.
"""

from __future__ import annotations

import logging
import random
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset
from app.services import media_policy

log = logging.getLogger("demos")

VIEWS = {"laptop": (1440, 900, 1.0, False), "phone": (412, 892, 2.0, True)}
SCROLL_SECONDS = 9.0
FPS = 15


class DemoError(Exception):
    pass


def find(policy: media_policy.Policy, *texts: str | None) -> media_policy.Demo | None:
    text = " ".join(t for t in texts if t)
    for d in policy.demos:
        if d.matches(text):
            return d
    return None


async def assets(session: AsyncSession, demo: media_policy.Demo) -> list[MediaAsset]:
    """The demo's screens: listed uploads plus the latest captures (newest first)."""
    ids = []
    for a in demo.asset_ids:
        try:
            ids.append(uuid.UUID(a))
        except ValueError:
            continue
    rows = list((await session.scalars(select(MediaAsset).where(MediaAsset.kind.in_(("image", "video")))
                                          .order_by(MediaAsset.created_at.desc()).limit(600))).all())
    key = demo.product.lower()
    return [a for a in rows if a.id in ids or (str((a.meta or {}).get("demo") or "").lower() == key
                                                 and not (a.meta or {}).get("retired"))]


def pick(items: list[MediaAsset], *, kind: str | None = None, view: str | None = None, seed: str = "") -> list[MediaAsset]:
    """Matching items in a stable shuffled order (videos/screens rotate between posts)."""
    out = [a for a in items if (kind is None or a.kind == kind) and (view is None or (a.meta or {}).get("view") in (view, None))]
    random.Random(seed).shuffle(out)
    return out


# ----------------------------------------------------------------------------- capture
async def capture(session: AsyncSession, org_id, demo: media_policy.Demo) -> list[MediaAsset]:
    """Screenshots and scroll-through recordings of the demo's pages. Replaces the previous captures."""
    from app.services import media
    from app.services import media_render as render

    if not demo.urls:
        raise DemoError(f"Add at least one web address for {demo.product}.")
    out: list[MediaAsset] = []
    errors = []
    tmp = render.tmp_dir("demo-")
    try:
        browser = await render.get_browser()
        for url in demo.urls:
            if not url.startswith(("https://", "http://")):
                errors.append(f"{url}: use a full address starting with https://")
                continue
            for view, (w, h, scale, mobile) in VIEWS.items():
                try:
                    shots, video = await _capture_one(browser, url, w, h, scale, mobile, tmp / f"{view}-{len(out)}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{url} ({view}): {str(e)[:200]}")
                    continue
                base = {"demo": demo.product, "url": url, "view": view, "captured": True}
                for i, png in enumerate(shots):
                    out.append(await media.store_bytes(session, org_id, png, "png", source="demo:capture", kind="image",
                                                       alt_text=f"{demo.product}: {url}"[:300],
                                                       meta={**base, "shot": i}, area="demos"))
                if video is not None:
                    info = await render.probe(video)
                    out.append(await media.store_file(session, org_id, video, "mp4", source="demo:capture", kind="video",
                                                      mime="video/mp4", alt_text=f"{demo.product}: {url}"[:300],
                                                      width=info.get("width"), height=info.get("height"),
                                                      duration=info.get("duration"), meta={**base, "scroll": True}))
    finally:
        render.rm_tree(tmp)
    if not out:
        raise DemoError("Nothing could be captured. " + "; ".join(errors)[:600])
    # the new captures replace older ones of this product (uploads stay)
    keep = {a.id for a in out}
    for a in await assets(session, demo):  # older captures stay in the library (posts may use them), retired
        if a.id not in keep and (a.meta or {}).get("captured") and str(a.id) not in demo.asset_ids:
            a.meta = {**(a.meta or {}), "retired": True}
    return out


async def _capture_one(browser, url: str, w: int, h: int, scale: float, mobile: bool, work: Path) -> tuple[list[bytes], Path | None]:
    """Screenshots at three scroll positions, and a smooth scroll-through: frames taken while scrolling, encoded
    with ffmpeg (Playwright's own recorder needs a separate ffmpeg build)."""
    from app.services import media_render as render

    work.mkdir(parents=True, exist_ok=True)
    ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=scale, is_mobile=mobile,
                                    has_touch=mobile)
    shots: list[bytes] = []
    frames = work / "frames"
    frames.mkdir(exist_ok=True)
    n_frames = 0
    try:
        page = await ctx.new_page()
        await page.goto(url, wait_until="networkidle", timeout=45_000)
        await page.wait_for_timeout(1200)
        await _dismiss_banners(page)
        height = await page.evaluate("() => document.documentElement.scrollHeight")
        for frac in (0.0, 0.35, 0.7):
            y = int(max(0, height - h) * frac)
            await page.evaluate(f"window.scrollTo(0, {y})")
            await page.wait_for_timeout(700)
            shots.append(await page.screenshot(type="png"))
        # scroll-through: hold on the top, glide down (ease in/out), hold at the end
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)
        dist = max(0, min(height - h, h * 4))
        hold, glide = int(FPS * 1.0), int(FPS * (SCROLL_SECONDS - 2.0))
        positions = [0] * hold + [int(dist * (1 - __import__("math").cos(__import__("math").pi * (i + 1) / glide)) / 2)
                                  for i in range(glide)] + [dist] * hold
        last = None
        for y in positions:
            if y != last:
                await page.evaluate(f"window.scrollTo(0, {y})")
                last = y
            (frames / f"f{n_frames:04d}.jpg").write_bytes(await page.screenshot(type="jpeg", quality=82))
            n_frames += 1
    finally:
        await ctx.close()
    if n_frames < FPS * 2:
        return shots, None
    mp4 = work / "scroll.mp4"
    await render.run_ffmpeg(["-framerate", str(FPS), "-i", str(frames / "f%04d.jpg"),
                             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
                             "-crf", "21", "-an", "-movflags", "+faststart", str(mp4)])
    return shots, mp4


async def _dismiss_banners(page) -> None:
    """Close cookie/consent banners so they don't cover the product."""
    for label in ("Accept", "Accept all", "I agree", "Got it", "OK", "Close"):
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if await btn.count():
                await btn.first.click(timeout=1500)
                await page.wait_for_timeout(300)
                return
        except Exception:  # noqa: BLE001, S112
            continue


# ----------------------------------------------------------------------------- framing
def device_html(style, shot_uri: str, w: int, h: int, *, headline: str = "", eyebrow: str = "", phone: bool = False) -> str:
    """A screenshot in a laptop-window or phone frame on the brand background, with an optional headline above."""
    import html as _h

    c = style.colors
    pad = int(min(w, h) * 0.06)
    head_px = int(min(w, h) * (0.062 if h >= w else 0.055))
    frame = (f"<div class='phone'><img src='{shot_uri}'></div>" if phone else
             "<div class='win'><div class='bar'><i></i><i></i><i></i></div>"
             f"<div class='scr'><img src='{shot_uri}'></div></div>")
    title = (f"<div class='eyebrow'>{_h.escape(eyebrow)}</div>" if eyebrow else "") + \
            (f"<h1>{_h.escape(headline)}</h1>" if headline else "")
    logo = f"<img class='logo' src='{style.logo_uri}'>" if style.logo_uri else f"<div class='name'>{_h.escape(style.name)}</div>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
*{{box-sizing:border-box;margin:0}}
body{{width:{w}px;height:{h}px;overflow:hidden;font-family:'{style.heading_font}',Inter,system-ui,sans-serif;
 background:radial-gradient(120% 90% at 15% 0%,{c['primary']} 0%,{c['dark']} 60%,#05070d 100%);color:#fff;
 display:flex;flex-direction:column;align-items:center;padding:{pad}px;gap:{int(pad * .6)}px}}
.eyebrow{{font-size:{int(head_px * .38)}px;letter-spacing:.14em;text-transform:uppercase;color:{c.get('secondary_light', c['secondary'])};
 font-weight:700;text-align:center;margin-bottom:{int(pad * .2)}px}}
h1{{font-size:{head_px}px;line-height:1.12;font-weight:800;text-align:center;max-width:92%}}
.stage{{flex:1;min-height:0;width:100%;display:flex;align-items:center;justify-content:center}}
.win{{width:100%;max-height:100%;border-radius:{int(pad * .35)}px;overflow:hidden;background:#fff;
 box-shadow:0 {pad}px {pad * 2}px rgba(0,0,0,.45);display:flex;flex-direction:column}}
.bar{{height:{int(pad * .55)}px;background:#e9edf3;display:flex;align-items:center;gap:{int(pad * .14)}px;padding:0 {int(pad * .3)}px}}
.bar i{{width:{int(pad * .17)}px;height:{int(pad * .17)}px;border-radius:50%;background:#c4cbd6;display:block}}
.scr{{flex:1;min-height:0;overflow:hidden}} .scr img{{width:100%;display:block}}
.phone{{height:100%;max-height:100%;aspect-ratio:412/892;border-radius:{int(pad * .9)}px;padding:{int(pad * .22)}px;background:#0b0d12;
 box-shadow:0 {pad}px {pad * 2}px rgba(0,0,0,.5);overflow:hidden}}
.phone img{{width:100%;height:100%;object-fit:cover;object-position:top;border-radius:{int(pad * .7)}px;display:block}}
.logo{{height:{int(pad * .7)}px}} .name{{font-weight:800;font-size:{int(head_px * .4)}px;opacity:.9}}
</style></head><body>{title}<div class='stage'>{frame}</div>{logo}</body></html>"""


async def framed_png(style, asset: MediaAsset, w: int, h: int, *, headline: str = "", eyebrow: str = "") -> bytes:
    from app.services import media
    from app.services import media_render as render

    raw = await media._asset_bytes(asset)
    if not raw:
        raise DemoError("The screenshot file is missing.")
    uri = media.data_uri(raw, asset.mime or "image/png")
    phone = (asset.meta or {}).get("view") == "phone" or ((asset.height or 0) > (asset.width or 1) * 1.3)
    return await render.render_png(device_html(style, uri, w, h, headline=headline, eyebrow=eyebrow, phone=phone), w, h)


async def ensure_captured(session: AsyncSession, org_id, demo: media_policy.Demo) -> list[MediaAsset]:
    found = await assets(session, demo)
    if found or not demo.urls:
        return found
    try:
        return await capture(session, org_id, demo)
    except Exception as e:  # noqa: BLE001 - a post still gets made without the demo
        log.info("demo capture for %s failed: %s", demo.product, e)
        return []

