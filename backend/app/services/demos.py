"""Product demos: real screens of your products in posts about them.

Brand → Visuals → Product demos lists each product with words that identify it in a post (e.g. "HRMS, payroll,
attendance") and the public pages that show it working (a product page, a demo login page). "Capture" opens each page
in the studio's own browser and saves:
  - screenshots: the top of the page and two further down, on a laptop screen and on a phone screen;
  - a slow scroll-through recording of each, for videos (phone size for Shorts/Reels, laptop size for wide videos).
A demo behind a sign-in works too: give the sign-in page and a DEMO account's username and password (kept
encrypted). The studio signs in, then captures the listed pages and, if asked, the main pages in the app's menu. It
only opens links: it never clicks buttons or changes anything (except pressing "sign in"). Screens get posted
publicly, so use an account with sample data only.
You can also add your own screen recordings and screenshots from the Media library.

A post whose topic, title or text matches a product then uses these: Shorts/Reels show the recordings and
screenshots in their scenes, and image posts show a screenshot in a device frame under the headline.
"""

from __future__ import annotations

import logging
import random
import re
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
MAX_PAGES = 6
# links never followed while exploring a signed-in demo (only links are opened; nothing is clicked or changed)
SKIP_LINK = re.compile(r"log.?out|sign.?out|delete|remove|deactivat|destroy|reset|billing|subscription|payment|checkout|"
                       r"password|security|api.?key|token|export|download|\.(?:pdf|csv|xlsx?|zip)$", re.I)


async def capture(session: AsyncSession, org_id, demo: media_policy.Demo) -> list[MediaAsset]:
    """Screenshots and scroll-through recordings of the demo's pages, on a laptop and a phone screen. With a sign-in
    (a demo account), the studio signs in first and, with `explore`, also captures the main pages in the app's menu.
    Replaces the previous captures."""
    from app.services import media
    from app.services import media_render as render

    if not demo.urls and not demo.has_login:
        raise DemoError(f"Add at least one web address (or a sign-in page) for {demo.product}.")
    for url in [*demo.urls, *([demo.login_url] if demo.login_url else [])]:
        if not url.startswith(("https://", "http://")):
            raise DemoError(f"{url}: use a full address starting with https://")
    out: list[MediaAsset] = []
    errors: list[str] = []
    pages_done: list[str] = []
    tmp = render.tmp_dir("demo-")
    try:
        browser = await render.get_browser()
        for view, (w, h, scale, mobile) in VIEWS.items():
            ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=scale,
                                            is_mobile=mobile, has_touch=mobile)
            try:
                page = await ctx.new_page()
                urls = list(demo.urls)
                if demo.has_login:
                    landing = await _sign_in(page, demo)
                    if not urls:
                        urls = [landing]
                    if demo.explore:
                        for link in await _menu_links(page, landing):
                            if len(urls) >= MAX_PAGES:
                                break
                            if link not in urls:
                                urls.append(link)
                for k, url in enumerate(urls[:MAX_PAGES]):
                    try:
                        shots, video = await _capture_page(page, url, w, h, tmp / f"{view}-{k}")
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{url} ({view}): {str(e)[:200]}")
                        continue
                    pages_done.append(url)
                    base = {"demo": demo.product, "url": url, "view": view, "captured": True,
                            **({"signed_in": True} if demo.has_login else {})}
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
            except DemoError:
                raise
            except Exception as e:  # noqa: BLE001
                errors.append(f"{view}: {str(e)[:200]}")
            finally:
                await ctx.close()
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


USER_FIELD = ("input[type=email]:visible, input[autocomplete=username]:visible, input[name*=user i]:visible, "
              "input[name*=email i]:visible, input[name*=login i]:visible, input[id*=user i]:visible, "
              "input[id*=email i]:visible, input[type=text]:visible")
PASSWORD_FIELD = "input[type=password]:visible"


async def _sign_in(page, demo: media_policy.Demo) -> str:
    """Sign in with the demo account; returns the page the app lands on. One-page and two-step (username, then
    password) sign-ins work; codes sent by SMS/email and CAPTCHAs don't."""
    await page.goto(demo.login_url, wait_until="networkidle", timeout=45_000)
    await page.wait_for_timeout(800)
    await _dismiss_banners(page)
    user = page.locator(USER_FIELD).first
    if not await user.count():
        raise DemoError(f"{demo.product}: no username or email box on {demo.login_url}. Use the address of the sign-in page.")
    await user.fill(demo.username)
    pw = page.locator(PASSWORD_FIELD).first
    if not await pw.count():  # two-step: username first
        await user.press("Enter")
        try:
            await pw.wait_for(state="visible", timeout=10_000)
        except Exception:  # noqa: BLE001
            raise DemoError(f"{demo.product}: the sign-in page didn't ask for a password after the username.") from None
    await pw.fill(demo.password)
    await pw.press("Enter")
    try:
        await page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:  # noqa: BLE001, S110 - some apps keep a connection open
        pass
    await page.wait_for_timeout(2000)
    if await page.locator(PASSWORD_FIELD).count():
        raise DemoError(f"{demo.product}: couldn't sign in with the demo account (check the username and password; "
                        "sign-ins that need a code or a CAPTCHA can't be automated).")
    return page.url


async def _menu_links(page, landing: str) -> list[str]:
    """Pages linked from the app's navigation (sidebar, top bar), same site only, nothing risky."""
    from urllib.parse import urlsplit

    origin = "{0.scheme}://{0.netloc}".format(urlsplit(landing))
    hrefs: list[str] = await page.evaluate("""() => {
      const pick = (root) => [...root.querySelectorAll('a[href]')].filter(a => a.offsetParent !== null)
        .map(a => a.href);
      const navs = [...document.querySelectorAll('nav, aside, header, [role=navigation], [class*=sidebar], [class*=menu]')];
      const inNav = navs.flatMap(pick);
      return inNav.length ? inNav : pick(document);
    }""")
    seen, out = {landing.split("#")[0] if "#/" not in landing else landing}, []
    for href in hrefs:
        if not href.startswith(origin) or SKIP_LINK.search(href):
            continue
        key = href if "#/" in href else href.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out[:MAX_PAGES]


async def _capture_page(page, url: str, w: int, h: int, work: Path) -> tuple[list[bytes], Path | None]:
    """Screenshots (top, and further down on long pages) and a smooth scroll-through of a long page: frames taken
    while scrolling, encoded with ffmpeg (Playwright's own recorder needs a separate ffmpeg build)."""
    import math

    from app.services import media_render as render

    work.mkdir(parents=True, exist_ok=True)
    frames = work / "frames"
    frames.mkdir(exist_ok=True)
    if page.url != url:
        await page.goto(url, wait_until="networkidle", timeout=45_000)
    await page.wait_for_timeout(1200)
    await _dismiss_banners(page)
    height = await page.evaluate("() => document.documentElement.scrollHeight")
    long_page = height > h * 1.3
    shots: list[bytes] = []
    for frac in ((0.0, 0.35, 0.7) if long_page else (0.0,)):
        await page.evaluate(f"window.scrollTo(0, {int(max(0, height - h) * frac)})")
        await page.wait_for_timeout(700)
        shots.append(await page.screenshot(type="png"))
    if not long_page:  # an app screen that doesn't scroll: the screenshot is enough (videos pan across it)
        return shots, None
    # scroll-through: hold on the top, glide down (ease in/out), hold at the end
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)
    dist = max(0, min(height - h, h * 4))
    hold, glide = int(FPS * 1.0), int(FPS * (SCROLL_SECONDS - 2.0))
    positions = [0] * hold + [int(dist * (1 - math.cos(math.pi * (i + 1) / glide)) / 2) for i in range(glide)] + [dist] * hold
    n_frames, last = 0, None
    for y in positions:
        if y != last:
            await page.evaluate(f"window.scrollTo(0, {y})")
            last = y
        (frames / f"f{n_frames:04d}.jpg").write_bytes(await page.screenshot(type="jpeg", quality=82))
        n_frames += 1
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

