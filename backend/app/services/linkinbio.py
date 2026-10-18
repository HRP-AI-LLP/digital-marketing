"""Link-in-bio page: defaults, validation, server-rendered public HTML, and anonymous daily click counts.

The public page lives at /l/<slug> (and PUBLIC_BASE_URL/l/<slug> through the tunnel). It is plain HTML with inline
CSS: no scripts, no cookies, no third-party requests. Counters store only (page, day, target, count).
"""

import html
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.signing import media_url
from app.models import BrandProfile, LinkPage, LinkPageStat, MediaAsset, Organization, Post

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$")
RESERVED = {"api", "admin", "l", "go", "img", "static", "assets", "www", "login"}
MAX_LINKS = 40
SOCIAL_ORDER = ("linkedin", "instagram", "facebook", "x", "youtube", "tiktok", "threads", "pinterest", "github", "other")
SOCIAL_META = {
    "linkedin": ("LinkedIn", "in", "https://www.linkedin.com/in/{}"),
    "instagram": ("Instagram", "IG", "https://www.instagram.com/{}"),
    "facebook": ("Facebook", "f", "https://www.facebook.com/{}"),
    "x": ("X", "X", "https://x.com/{}"),
    "youtube": ("YouTube", "▶", "https://www.youtube.com/@{}"),
    "tiktok": ("TikTok", "♪", "https://www.tiktok.com/@{}"),
    "threads": ("Threads", "@", "https://www.threads.net/@{}"),
    "pinterest": ("Pinterest", "P", "https://www.pinterest.com/{}"),
    "github": ("GitHub", "GH", "https://github.com/{}"),
    "other": ("Website", "↗", None),
}
BUTTON_STYLES = ("filled", "outline", "soft")
MODES = ("light", "dark", "brand")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_BOT = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|embedly|preview|whatsapp|telegram|discord|curl|python",
                  re.IGNORECASE)


# ------------------------------------------------------------------ helpers
def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


def public_base() -> str:
    s = get_settings()
    base = (s.public_base_url or "").strip().rstrip("/").removesuffix("/api/v1")
    return base or s.api_base_url.rstrip("/")


def page_urls(slug: str) -> dict:
    s = get_settings()
    return {"public_url": f"{public_base()}/l/{slug}", "local_url": f"{s.api_base_url.rstrip('/')}/l/{slug}",
            "has_public_address": bool((s.public_base_url or "").strip())}


def safe_url(u: str | None) -> str | None:
    u = (u or "").strip()
    if not u or any(ch in u for ch in "\r\n\t <>\"'"):
        return None
    low = u.lower()
    if low.startswith(("https://", "http://")) and len(u) > 10:
        return u[:1000]
    if low.startswith(("mailto:", "tel:")) and len(u) > 6:
        return u[:300]
    return None


def social_url(key: str, value: str | None) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    bare = re.sub(r"^https?://", "", v, flags=re.IGNORECASE)
    if bare != v and "." in bare.split("/")[0]:
        return safe_url(v)
    v = bare  # a handle that was saved with a scheme in front ("https://@hrpai")
    tpl = SOCIAL_META.get(key, (None, None, None))[2]
    handle = v.lstrip("@").strip("/")
    if tpl and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", handle):
        return tpl.format(handle)
    return safe_url("https://" + v) if "." in v else None


def socials(org: Organization) -> list[dict]:
    links = org.social_links or {}
    out = []
    for k in SOCIAL_ORDER:
        url = social_url(k, links.get(k))
        if url:
            label, code, _ = SOCIAL_META[k]
            out.append({"key": k, "label": label, "code": code, "url": url})
    return out


def _hexc(v: str | None, default: str) -> str:
    v = (v or "").strip()
    return v if _HEX.match(v) else default


def _lum(c: str) -> float:
    def ch(x: int) -> float:
        x = x / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _on(bg: str) -> str:
    """Readable text colour on a background."""
    return "#0f172a" if _lum(bg) > 0.42 else "#ffffff"


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def theme_colors(theme: dict, brand: BrandProfile | None) -> dict:
    colors = (brand.colors if brand else None) or {}
    primary = _hexc(theme.get("accent"), _hexc(colors.get("primary"), "#075492"))
    mode = theme.get("mode") if theme.get("mode") in MODES else "light"
    if mode == "dark":
        bg = _hexc(theme.get("background"), _hexc(colors.get("dark"), "#0b1220"))
    elif mode == "brand":
        bg = _hexc(theme.get("background"), primary)
    else:
        bg = _hexc(theme.get("background"), _hexc(colors.get("light"), "#f6f8fb"))
    text = _hexc(theme.get("text"), _on(bg))
    if _contrast(text, bg) < 4.5:
        text = _on(bg)
    accent = primary
    if mode == "brand" or _contrast(accent, bg) < 2.2:
        accent = _on(bg)  # buttons in white/ink on a brand-coloured background
    return {"bg": bg, "text": text, "accent": accent, "on_accent": _on(accent), "mode": mode,
            "button_style": theme.get("button_style") if theme.get("button_style") in BUTTON_STYLES else "filled"}


def clean_links(raw: list) -> list[dict]:
    out, seen = [], set()
    for x in (raw or [])[:MAX_LINKS]:
        if not isinstance(x, dict):
            continue
        url = safe_url(x.get("url"))
        title = str(x.get("title") or "").strip()[:100]
        if not url or not title:
            continue
        try:
            lid = str(uuid.UUID(str(x.get("id"))))
        except (ValueError, TypeError):
            lid = str(uuid.uuid4())
        if lid in seen:
            lid = str(uuid.uuid4())
        seen.add(lid)
        thumb = None
        if x.get("thumbnail_asset_id"):
            try:
                thumb = str(uuid.UUID(str(x["thumbnail_asset_id"])))
            except ValueError:
                thumb = None
        icon = str(x.get("icon") or "").strip()[:4] or None
        out.append({"id": lid, "title": title, "url": url, "icon": icon, "thumbnail_asset_id": thumb,
                    "highlight": bool(x.get("highlight")), "enabled": x.get("enabled", True) is not False})
    return out


def clean_theme(raw: dict | None) -> dict:
    raw = raw or {}
    out: dict = {}
    if raw.get("mode") in MODES:
        out["mode"] = raw["mode"]
    if raw.get("button_style") in BUTTON_STYLES:
        out["button_style"] = raw["button_style"]
    for k in ("background", "accent", "text"):
        if isinstance(raw.get(k), str) and _HEX.match(raw[k].strip()):
            out[k] = raw[k].strip()
    return out


def default_slug(org: Organization) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (org.slug or org.name or "page").lower()).strip("-")[:60].strip("-")
    return s if SLUG_RE.match(s or "") and s not in RESERVED else f"page-{str(org.id)[:8]}"


def defaults(org: Organization) -> dict:
    links = []
    if org.website and safe_url(org.website):
        links.append({"id": str(uuid.uuid4()), "title": "Visit our website", "url": safe_url(org.website), "icon": None,
                      "thumbnail_asset_id": None, "highlight": True, "enabled": True})
    return {"slug": default_slug(org), "published": False, "title": org.name, "tagline": org.tagline,
            "show_logo": True, "show_socials": True, "show_posts": False, "posts_count": 6, "links": links, "theme": {}}


async def org_logo_id(session: AsyncSession, org: Organization) -> uuid.UUID | None:
    if org.logo_asset_id:
        return org.logo_asset_id
    brand = await session.scalar(select(BrandProfile))
    return brand.logo_asset_id if brand else None


async def latest_posts(session: AsyncSession, n: int) -> list[dict]:
    """Published posts that have a picture and a link to the live post, newest first."""
    if n <= 0:
        return []
    rows = (await session.scalars(select(Post).where(
        Post.status == "published", Post.external_url.is_not(None)).order_by(Post.published_at.desc().nulls_last())
        .limit(60))).all()
    out = []
    for p in rows:
        ids = []
        for i in p.media_ids or []:
            try:
                ids.append(uuid.UUID(str(i)))
            except ValueError:
                continue
        if not ids or not safe_url(p.external_url):
            continue
        a = await session.get(MediaAsset, ids[0])
        if a is None or not (a.kind in ("image", "gif") or (a.meta or {}).get("poster_path")):
            continue
        out.append({"id": str(p.id), "asset_id": str(a.id), "url": p.external_url,
                    "alt": (a.alt_text or p.title or p.topic or "Post")[:140], "platform": p.platform})
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ rendering
def _initials(name: str) -> str:
    words = [w for w in re.split(r"\s+", re.sub(r"[^\w\s]", " ", name or "")) if w]
    return ("".join(w[0] for w in words[:2]) or "?").upper()


def render(page: dict, org: Organization, colors: dict, *, logo_id, posts: list[dict], social: list[dict],
           img_url, go_url, preview: bool = False) -> str:
    """The public page as one self-contained HTML document."""
    title = page.get("title") or org.name
    tagline = page.get("tagline") or ""
    c = colors
    style = c["button_style"]
    if style == "outline":
        btn = f"background:transparent;color:{c['text']};border:2px solid {c['accent']};"
    elif style == "soft":
        btn = f"background:color-mix(in srgb,{c['accent']} 12%,{c['bg']});color:{c['text']};border:2px solid transparent;"
    else:
        btn = f"background:{c['accent']};color:{c['on_accent']};border:2px solid {c['accent']};"
    hl = f"background:{c['accent']};color:{c['on_accent']};border:2px solid {c['accent']};"
    parts: list[str] = []
    if page.get("show_logo", True):
        if logo_id:
            parts.append(f"<img class='logo' src='{esc(img_url(logo_id))}' alt='{esc(org.name)} logo' width='96' height='96'>")
        else:
            parts.append(f"<div class='logo mono' aria-hidden='true'>{esc(_initials(org.name))}</div>")
    parts.append(f"<h1>{esc(title)}</h1>")
    if tagline:
        parts.append(f"<p class='tag'>{esc(tagline)}</p>")
    if page.get("show_socials", True) and social:
        icons = "".join(f"<a class='soc' href='{esc(go_url('s-' + s['key']))}' rel='noopener' aria-label='{esc(s['label'])}' "
                        f"title='{esc(s['label'])}'>{esc(s['code'])}</a>" for s in social)
        parts.append(f"<nav class='socials' aria-label='Social profiles'>{icons}</nav>")
    btns = []
    for link in page.get("links") or []:
        if not link.get("enabled", True):
            continue
        lead = ""
        if link.get("thumbnail_asset_id"):
            lead = f"<img class='thumb' src='{esc(img_url(link['thumbnail_asset_id']))}' alt='' width='40' height='40'>"
        elif link.get("icon"):
            lead = f"<span class='ico' aria-hidden='true'>{esc(link['icon'])}</span>"
        cls = "btn hl" if link.get("highlight") else "btn"
        pad = "<span class='pad' aria-hidden='true'></span>" if lead else ""  # keeps the text centred
        btns.append(f"<a class='{cls}' href='{esc(go_url(link['id']))}' rel='noopener'>{lead}<span class='bt'>{esc(link['title'])}</span>{pad}</a>")
    if btns:
        parts.append(f"<div class='links'>{''.join(btns)}</div>")
    elif preview:
        parts.append("<p class='empty'>Add your first button in the editor.</p>")
    if page.get("show_posts") and posts:
        tiles = "".join(f"<a class='tile' href='{esc(go_url(p['id']))}' rel='noopener'><img src='{esc(img_url(p['asset_id']))}' "
                        f"alt='{esc(p['alt'])}' loading='lazy'></a>" for p in posts)
        parts.append(f"<section aria-label='Latest posts'><h2>Latest posts</h2><div class='grid'>{tiles}</div></section>")
    parts.append(f"<footer>© {datetime.now(UTC).year} {esc(org.name)}</footer>")
    desc = tagline or f"Links from {org.name}"
    og_img = f"<meta property='og:image' content='{esc(img_url(logo_id, absolute=True))}'>" if logo_id else ""
    icon = f"<link rel='icon' href='{esc(img_url(logo_id))}'>" if logo_id else ""
    css = f"""
*{{box-sizing:border-box}}html{{-webkit-text-size-adjust:100%}}
body{{margin:0;min-height:100vh;background:{c['bg']};color:{c['text']};font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,Inter,Arial,sans-serif}}
main{{max-width:560px;margin:0 auto;padding:40px 18px 28px;display:flex;flex-direction:column;align-items:center;text-align:center}}
.logo{{width:96px;height:96px;border-radius:50%;object-fit:cover;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.12)}}
.mono{{display:flex;align-items:center;justify-content:center;font-weight:700;font-size:34px;background:{c['accent']};color:{c['on_accent']}}}
h1{{margin:16px 0 4px;font-size:1.45rem;line-height:1.25;overflow-wrap:anywhere}}
.tag{{margin:0;opacity:.85;max-width:40ch;overflow-wrap:anywhere}}
.socials{{display:flex;flex-wrap:wrap;justify-content:center;gap:10px;margin:18px 0 4px}}
.soc{{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;text-decoration:none;color:{c['text']};border:2px solid color-mix(in srgb,{c['text']} 25%,transparent)}}
.soc:hover,.soc:focus-visible{{border-color:{c['accent']};outline:none}}
.links{{width:100%;display:flex;flex-direction:column;gap:12px;margin-top:22px}}
.btn{{display:flex;align-items:center;gap:12px;min-height:56px;padding:8px 18px;border-radius:14px;text-decoration:none;font-weight:600;{btn}transition:transform .12s ease}}
.btn.hl{{{hl}box-shadow:0 6px 18px color-mix(in srgb,{c['accent']} 35%,transparent)}}
.btn:hover,.btn:focus-visible{{transform:translateY(-1px);outline:none}}
.btn:focus-visible{{box-shadow:0 0 0 3px color-mix(in srgb,{c['accent']} 45%,transparent)}}
.bt{{flex:1;overflow-wrap:anywhere}}.btn .thumb{{width:40px;height:40px;border-radius:9px;object-fit:cover;flex:none}}
.ico{{width:40px;flex:none;font-size:22px}}.pad{{width:40px;flex:none}}
section{{width:100%;margin-top:30px}}h2{{font-size:1rem;margin:0 0 12px;opacity:.85}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}}
.tile{{display:block;aspect-ratio:1;border-radius:10px;overflow:hidden;background:color-mix(in srgb,{c['text']} 8%,transparent)}}
.tile img{{width:100%;height:100%;object-fit:cover;display:block}}
.empty{{opacity:.7;margin-top:24px}}footer{{margin-top:34px;font-size:12px;opacity:.6}}
@media (prefers-reduced-motion:reduce){{.btn{{transition:none}}}}
"""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><meta name='description' content='{esc(desc)}'>"
        f"<meta property='og:title' content='{esc(title)}'><meta property='og:description' content='{esc(desc)}'>"
        f"<meta property='og:type' content='website'>{og_img}{icon}"
        f"<meta name='theme-color' content='{c['bg']}'><style>{css}</style></head>"
        f"<body><main>{''.join(parts)}</main></body></html>"
    )


async def build_html(session: AsyncSession, page: dict, org: Organization, *, preview: bool) -> str:
    brand = await session.scalar(select(BrandProfile))
    colors = theme_colors(page.get("theme") or {}, brand)
    logo = await org_logo_id(session, org)
    posts = await latest_posts(session, int(page.get("posts_count") or 6)) if page.get("show_posts") else []
    slug = page.get("slug") or "page"
    if preview:
        def img_url(aid, absolute: bool = False):
            return media_url(aid, org.id)

        def go_url(target):
            return "#"
    else:
        def img_url(aid, absolute: bool = False):
            path = f"/l/{slug}/img/{aid}"
            return f"{public_base()}{path}" if absolute else path

        def go_url(target):
            return f"/l/{slug}/go/{target}"
    return render(page, org, colors, logo_id=logo, posts=posts, social=socials(org), img_url=img_url, go_url=go_url,
                  preview=preview)


def page_dict(p: LinkPage) -> dict:
    return {"slug": p.slug, "published": p.published, "title": p.title, "tagline": p.tagline, "show_logo": p.show_logo,
            "show_socials": p.show_socials, "show_posts": p.show_posts, "posts_count": p.posts_count,
            "links": p.links or [], "theme": p.theme or {}}


# ------------------------------------------------------------------ counting
def countable(headers) -> bool:
    """Skip link-preview bots and browser prefetches. Nothing about the visitor is stored either way."""
    ua = headers.get("user-agent") or ""
    if not ua or _BOT.search(ua):
        return False
    purpose = (headers.get("sec-purpose") or headers.get("purpose") or "").lower()
    return "prefetch" not in purpose and "prerender" not in purpose


def local_day(org: Organization) -> date:
    try:
        tz = ZoneInfo(org.timezone or "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).date()


async def bump(session: AsyncSession, page: LinkPage, org: Organization, target: str) -> None:
    stmt = insert(LinkPageStat).values(id=uuid.uuid4(), organization_id=page.organization_id, page_id=page.id,
                                       day=local_day(org), target=target[:80], count=1)
    stmt = stmt.on_conflict_do_update(constraint="uq_link_page_stats_page_day_target",
                                      set_={"count": LinkPageStat.count + 1})
    await session.execute(stmt)


async def stats(session: AsyncSession, page: LinkPage, org: Organization, days: int = 30) -> dict:
    days = max(1, min(days, 365))
    end = local_day(org)
    start = end - timedelta(days=days - 1)
    rows = (await session.execute(select(LinkPageStat.day, LinkPageStat.target, func.sum(LinkPageStat.count))
                                  .where(LinkPageStat.page_id == page.id, LinkPageStat.day >= start)
                                  .group_by(LinkPageStat.day, LinkPageStat.target))).all()
    by_day = {start + timedelta(days=i): {"views": 0, "clicks": 0} for i in range(days)}
    by_target: dict[str, int] = {}
    for d, target, n in rows:
        n = int(n or 0)
        slot = by_day.setdefault(d, {"views": 0, "clicks": 0})
        if target == "view":
            slot["views"] += n
        else:
            slot["clicks"] += n
            by_target[target] = by_target.get(target, 0) + n
    labels = {lk["id"]: lk["title"] for lk in page.links or []}
    labels |= {f"s-{k}": f"{v[0]} icon" for k, v in SOCIAL_META.items()}
    targets = []
    for t, n in sorted(by_target.items(), key=lambda kv: -kv[1]):
        kind = "link" if t in labels and not t.startswith("s-") else "social" if t.startswith("s-") else "post"
        targets.append({"target": t, "label": labels.get(t) or ("A post in the grid" if kind == "post" else "Removed button"),
                        "kind": kind, "clicks": n})
    views = sum(v["views"] for v in by_day.values())
    clicks = sum(v["clicks"] for v in by_day.values())
    return {"days": days, "views": views, "clicks": clicks,
            "click_rate": round(clicks / views * 100, 1) if views else None,
            "by_day": [{"day": d.isoformat(), **v} for d, v in sorted(by_day.items())], "by_target": targets}
