"""Follow kit: everything needed to send people to the organization's social profiles to follow them.

Each followable profile (every connected social account, plus profile links saved on the organization that no
connected account covers) gets a permanent tracked short link (/r/<code>) whose destination can be changed later
without reprinting anything, a QR code, a Code 128 barcode and a branded "Follow us" image. A public page
(/f/<slug>) lists every profile that's switched on, so one link / QR code shares them all.

Settings live in Organization.publishing["follow_kit"]:
  {title, message, slug, theme, order: [key], items: {key: {url, include, link_id}}, all_link_id}
Keys: "acc:<social account id>" for connected accounts, "site:<n>" for saved profile links, "all" for the page.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
import uuid
import zipfile
from urllib.parse import quote, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Organization, SocialAccount, TrackedLink
from app.services import barcode, links, qr
from app.services.linkinbio import safe_url

LABELS = {
    "linkedin_org": "LinkedIn", "linkedin_person": "LinkedIn", "facebook_page": "Facebook", "instagram": "Instagram",
    "threads": "Threads", "pinterest": "Pinterest", "gbp": "Google", "youtube": "YouTube", "bluesky": "Bluesky",
    "mastodon": "Mastodon", "x": "X", "telegram": "Telegram", "discord": "Discord", "reddit": "Reddit",
    "whatsapp": "WhatsApp", "github": "GitHub", "website": "Website", "tiktok": "TikTok",
}
BADGE = {
    "facebook_page": ("f", "#1877F2"), "instagram": ("IG", "#D62976"), "threads": ("@", "#111827"),
    "linkedin_person": ("in", "#0A66C2"), "linkedin_org": ("in", "#0A66C2"), "youtube": ("YT", "#FF0000"),
    "gbp": ("G", "#34A853"), "pinterest": ("P", "#E60023"), "bluesky": ("B", "#1185FE"), "mastodon": ("M", "#6364FF"),
    "x": ("X", "#111827"), "telegram": ("TG", "#229ED9"), "discord": ("DC", "#5865F2"), "reddit": ("r/", "#FF4500"),
    "whatsapp": ("WA", "#128C7E"), "github": ("GH", "#24292F"), "website": ("W", "#475569"), "tiktok": ("TT", "#010101"),
}
VERB = {"youtube": "Subscribe on", "telegram": "Join us on", "discord": "Join us on", "whatsapp": "Follow our channel on",
        "linkedin_person": "Connect on", "github": "Follow us on", "reddit": "Join us on"}
# Host → platform, for profile links saved on the organization.
HOSTS = (("linkedin.com", "linkedin_org"), ("facebook.com", "facebook_page"), ("fb.com", "facebook_page"),
         ("instagram.com", "instagram"), ("threads.net", "threads"), ("pinterest.", "pinterest"), ("youtube.com", "youtube"),
         ("youtu.be", "youtube"), ("bsky.app", "bluesky"), ("x.com", "x"), ("twitter.com", "x"), ("t.me", "telegram"),
         ("discord.gg", "discord"), ("discord.com", "discord"), ("reddit.com", "reddit"), ("whatsapp.com", "whatsapp"),
         ("github.com", "github"), ("tiktok.com", "tiktok"))
SHAPES = {"square": (1080, 1080), "portrait": (1080, 1350), "story": (1080, 1920), "landscape": (1200, 630)}
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,40}[a-z0-9])?$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")  # a sign-in email is never shown as a handle


def label_of(platform: str) -> str:
    return LABELS.get(platform, platform.replace("_", " ").title())


def platform_of_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    for h, p in HOSTS:
        if host == h or host.endswith("." + h) or (h.endswith(".") and h in host):
            return p
    if "/@" in url:  # Mastodon-style profile on any instance
        return "mastodon"
    return "website"


def _norm(url: str | None) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://(www\.|in\.|m\.)?", "", u)
    return u.split("?")[0].split("#")[0].rstrip("/").removesuffix("/home")


def settings_of(org: Organization) -> dict:
    cfg = dict((org.publishing or {}).get("follow_kit") or {})
    default_slug = re.sub(r"[^a-z0-9]+", "-", (org.slug or org.name or "follow").lower()).strip("-")[:40] or "follow"
    name = re.sub(r"\s+(LLP|Pvt\.? Ltd\.?|Private Limited|Ltd\.?|LLC|Inc\.?)$", "", org.name or "", flags=re.I).strip() or "us"
    return {
        "title": (cfg.get("title") or f"Follow {name}")[:80],
        "message": (cfg.get("message") if cfg.get("message") is not None else
                    "Get our updates, tips and launches wherever you like to scroll.")[:280],
        "slug": cfg.get("slug") if SLUG_RE.match(cfg.get("slug") or "") else default_slug,
        "theme": cfg.get("theme") if cfg.get("theme") in ("dark", "light") else "dark",
        "order": [k for k in cfg.get("order") or [] if isinstance(k, str)],
        "items": dict(cfg.get("items") or {}),
        "all_link_id": cfg.get("all_link_id"),
    }


def _save(org: Organization, cfg: dict) -> None:
    pub = dict(org.publishing or {})
    pub["follow_kit"] = cfg
    org.publishing = pub
    flag_modified(org, "publishing")


def public_page_url(slug: str) -> str:
    base = links.public_base() or ""
    return f"{base}/f/{slug}"


# ------------------------------------------------------------------ profiles
_FROM_HANDLE = {"instagram": "https://www.instagram.com/{}/", "x": "https://x.com/{}", "telegram": "https://t.me/{}",
                "bluesky": "https://bsky.app/profile/{}", "youtube": "https://www.youtube.com/@{}",
                "pinterest": "https://www.pinterest.com/{}/", "threads": "https://www.threads.net/@{}",
                "reddit": "https://www.reddit.com/user/{}"}


def _from_handle(platform: str, handle: str | None) -> str | None:
    h = (handle or "").strip()
    if platform == "mastodon":
        m = re.fullmatch(r"@?([A-Za-z0-9_]{1,30})@([a-z0-9.-]+\.[a-z]{2,})", h)
        return f"https://{m.group(2)}/@{m.group(1)}" if m else None
    h = h.lstrip("@")
    if platform in _FROM_HANDLE and re.fullmatch(r"[A-Za-z0-9._-]{1,60}", h):
        return _FROM_HANDLE[platform].format(h)
    return None


def _account_url(a: SocialAccount) -> tuple[str | None, str | None]:
    """(follow URL, hint) for a connected account."""
    url = safe_url(a.profile_url) or _from_handle(a.platform, a.handle)
    if a.platform == "discord":
        if url and "discord.gg/" not in url and "/invite/" not in url:
            return None, "Paste a Discord invite link (Server settings → Invites), so people who aren't members can join."
    if a.platform == "linkedin_person" and not url:
        return None, "Paste the profile's public address (linkedin.com/in/…)."
    if not url:
        return None, "Paste the profile's public address."
    return url, None


async def profiles(session: AsyncSession, org: Organization) -> list[dict]:
    cfg = settings_of(org)
    accounts = (await session.scalars(select(SocialAccount).order_by(SocialAccount.created_at))).all()
    out: list[dict] = []
    seen: set[str] = set()
    for a in accounts:
        if a.status == "disconnected":
            continue
        key = f"acc:{a.id}"
        auto, hint = _account_url(a)
        own = cfg["items"].get(key) or {}
        url = safe_url(own.get("url")) or auto
        out.append({"key": key, "platform": a.platform, "platform_label": label_of(a.platform), "name": a.name,
                    "handle": None if _EMAIL.match(a.handle or "") else (a.handle or None),
                    "url": url, "auto_url": auto, "custom_url": bool(safe_url(own.get("url"))), "hint": None if url else hint,
                    "source": "account", "include": own.get("include", url is not None and a.platform != "linkedin_person")})
        for u in (url, auto, a.profile_url):
            if u:
                seen.add(_norm(u))
    saved = []
    for k, v in (org.social_links or {}).items():
        for part in re.split(r"[,\s]+", str(v or "")):
            u = safe_url(part)
            if u and k != "whatsapp_number":
                saved.append(u)
    for u in saved:
        if _norm(u) in seen:
            continue
        seen.add(_norm(u))
        key = f"site:{hashlib.sha1(_norm(u).encode()).hexdigest()[:10]}"
        own = cfg["items"].get(key) or {}
        p = platform_of_url(u)
        host = (urlsplit(u).hostname or "").removeprefix("www.")
        handle = urlsplit(u).path.strip("/").split("/")[-1] or host
        out.append({"key": key, "platform": p, "platform_label": label_of(p), "name": org.name, "handle": handle,
                    "url": safe_url(own.get("url")) or u, "auto_url": u, "custom_url": bool(safe_url(own.get("url"))),
                    "hint": None, "source": "profile", "include": own.get("include", True)})
    order = {k: i for i, k in enumerate(cfg["order"])}
    out.sort(key=lambda x: (order.get(x["key"], 1000), 0 if x["source"] == "account" else 1))
    return out


# ------------------------------------------------------------------ tracked links
async def _link(session: AsyncSession, org: Organization, cfg: dict, key: str, target: str, label: str,
                platform: str | None) -> TrackedLink:
    lid = cfg["all_link_id"] if key == "all" else (cfg["items"].get(key) or {}).get("link_id")
    lk = await session.get(TrackedLink, uuid.UUID(lid)) if lid else None
    if lk is None:
        lk = await links.create_link(session, org.id, target, platform=None, label=label, source="follow")
        lk.utm = {}  # profile pages don't need UTM tags
        lk.platform = platform
        if key == "all":
            cfg["all_link_id"] = str(lk.id)
        else:
            cfg["items"].setdefault(key, {})["link_id"] = str(lk.id)
    elif lk.target_url != target:
        lk.target_url = target  # same code, new destination: printed QR codes keep working
    return lk


async def build(session: AsyncSession, org: Organization) -> dict:
    """The whole kit, creating any missing short links (caller commits)."""
    cfg = settings_of(org)
    items = await profiles(session, org)
    for it in items:
        if it["url"]:
            lk = await _link(session, org, cfg, it["key"], it["url"], f"Follow: {it['platform_label']} · {it['name']}", it["platform"])
            it.update(code=lk.code, short_url=links.short_url(lk.code), clicks=lk.clicks_total or 0, link_id=str(lk.id))
        else:
            it.update(code=None, short_url=None, clicks=0, link_id=None)
    page = public_page_url(cfg["slug"])
    alk = await _link(session, org, cfg, "all", page, "Follow: all profiles page", None)
    _save(org, {k: v for k, v in cfg.items()})
    return {
        "settings": {k: cfg[k] for k in ("title", "message", "slug", "theme")},
        "items": items,
        "all": {"key": "all", "page_url": page, "short_url": links.short_url(alk.code), "code": alk.code,
                "clicks": alk.clicks_total or 0, "link_id": str(alk.id), "count": sum(1 for i in items if i["include"] and i["url"])},
        "public_ready": bool(links.public_base()),
    }


def find(kit: dict, key: str) -> dict | None:
    if key == "all":
        return kit["all"]
    return next((i for i in kit["items"] if i["key"] == key), None)


def update_settings(org: Organization, body: dict) -> None:
    cfg = settings_of(org)
    for f in ("title", "message"):
        if body.get(f) is not None:
            cfg[f] = str(body[f]).strip()[:80 if f == "title" else 280]
    if body.get("slug") is not None:
        s = str(body["slug"]).strip().lower()
        if not SLUG_RE.match(s):
            raise ValueError("Use 1–42 lowercase letters, digits and hyphens for the page address.")
        cfg["slug"] = s
    if body.get("theme") in ("dark", "light"):
        cfg["theme"] = body["theme"]
    if isinstance(body.get("order"), list):
        cfg["order"] = [str(k)[:80] for k in body["order"]][:200]
    for it in body.get("items") or []:
        key = str(it.get("key") or "")[:80]
        if not key:
            continue
        cur = cfg["items"].setdefault(key, {})
        if "url" in it:
            raw = (it.get("url") or "").strip()
            if raw and not safe_url(raw):
                raise ValueError("Enter a full web address that starts with https://")
            cur["url"] = safe_url(raw)
        if "include" in it:
            cur["include"] = bool(it["include"])
    _save(org, cfg)


# ------------------------------------------------------------------ codes
def colour(v: str | None, default: str) -> str:
    return v if v and HEX_RE.match(v) else default


def qr_for(text: str) -> qr.QrCode:
    return qr.best(text, "H")


def qr_png(text: str, *, px: int, fg: str, bg: str, style: str, logo_png: bytes | None) -> bytes:
    return qr.styled_png(qr_for(text), px=px, fg=fg, bg=bg, style=style, logo_png=logo_png)


def qr_svg(text: str, *, fg: str, bg: str, style: str, logo_uri: str | None) -> str:
    return qr.styled_svg(qr_for(text), fg=fg, bg=bg, style=style, logo_uri=logo_uri, title=f"QR code: {html.escape(text)}")


def barcode_png(text: str, fg: str, bg: str) -> bytes:
    return barcode.png(text, fg=fg, bg=bg)


def barcode_svg(text: str, fg: str, bg: str) -> str:
    return barcode.svg(text, fg=fg, bg=bg)


def share_text(item: dict, kit: dict) -> str:
    if item.get("key") == "all":
        return f"{kit['settings']['title']}: all our channels in one place"
    verb = VERB.get(item["platform"], "Follow us on")
    return f"{verb} {item['platform_label']}"


# ------------------------------------------------------------------ "Follow us" image
def _badge(platform: str, size: float) -> str:
    code, bg = BADGE.get(platform, (platform[:2].upper(), "#64748B"))
    return (f"<span class='bdg' style='background:{bg};width:{size:.0f}px;height:{size:.0f}px;font-size:{size * .42:.0f}px;"
            f"border-radius:{size * .24:.0f}px'>{html.escape(code)}</span>")


def card_html(item: dict, kit: dict, brand, shape: str, theme: str, qr_uri: str) -> str:
    """A branded "Follow us" image: logo, headline, the profile(s), and a QR code with the short link.

    Layouts: story (9:16) stacks everything with a large centred QR; landscape (1.91:1) puts the QR on the right;
    square / portrait put the profile list and the QR side by side under the headline."""
    from app.services.media_templates.base import Page, esc

    W, H = SHAPES.get(shape, SHAPES["square"])
    page = Page(W, H, brand, theme=theme)
    u = page.u
    tall, wide = page.tall, page.wide
    is_all = item.get("key") == "all"
    short = re.sub(r"^https?://", "", item.get("short_url") or "")
    if is_all:
        rows = [i for i in kit["items"] if i["include"] and i["url"]][:12]
        eyebrow, head, sub = "Find us everywhere", kit["settings"]["title"], kit["settings"]["message"]
        cols = 2 if (len(rows) > 6 and (tall or wide)) else 1
        bs = u * (6.2 if tall else 3.9 if wide else 4.2)
        chips = "".join(
            f"<div class='row'>{_badge(i['platform'], bs)}<div class='rt'><b>{esc(i['platform_label'])}</b>"
            f"<span>{esc(i.get('handle') or i['name'])}</span></div></div>" for i in rows)
        body_block = f"<div class='rows' style='grid-template-columns:repeat({cols},minmax(0,1fr))'>{chips}</div>"
        cta_note = "Every channel, one tap"
    else:
        verb = VERB.get(item["platform"], "Follow us on")
        eyebrow, head = f"{verb} {item['platform_label']}", f"{verb} *{item['platform_label']}*"
        handle = item.get("handle") or ""
        if handle and not handle.startswith("@") and item["platform"] in ("instagram", "x", "threads", "tiktok", "youtube", "telegram", "pinterest"):
            handle = f"@{handle}"
        sub = item["name"] + (f" · {handle}" if handle and handle.lstrip("@") != item["name"] else "")
        body_block = f"<div class='hero'>{_badge(item['platform'], u * (22 if tall else 11 if wide else 13))}</div>"
        cta_note = "Opens our profile, tap Follow"
    if tall:
        qr_px = W * (0.38 if is_all else 0.58)
    elif wide:
        qr_px = H * 0.52
    else:
        qr_px = u * (30 if is_all else 36)
    css = f"""
    .bdg{{display:inline-grid;place-items:center;color:#fff;font-weight:800;letter-spacing:-.02em;flex:none;
      box-shadow:0 {u*.4:.1f}px {u*1.4:.1f}px rgba(0,0,0,.25)}}
    .wrap{{position:absolute;inset:0;padding:var(--pt) var(--px) var(--pb);display:flex;flex-direction:column;gap:{u*2.2:.0f}px}}
    .wrap>*{{flex:none}}
    .eyebrow{{align-self:flex-start}}
    .headline{{font-size:{u*(10.5 if tall else 7.2 if wide else 7.6):.0f}px}}
    .lead{{font-size:{u*(3.8 if tall else 2.6):.0f}px;line-height:1.4;color:var(--fg-soft);font-weight:450}}
    .main{{flex:1 1 auto;min-height:0;display:flex;gap:{u*4:.0f}px;align-items:{'center' if tall else 'flex-end'};
      flex-direction:{'column' if tall else 'row'};justify-content:{'space-evenly' if tall else 'space-between'}}}
    .rows{{display:grid;column-gap:{u*3:.0f}px;row-gap:{u*(2.4 if tall else 1.2):.0f}px;min-width:0;flex:1;{'width:100%' if tall else ''}}}
    .row{{display:flex;align-items:center;gap:{u*1.5:.0f}px;min-width:0}}
    .rt{{display:flex;flex-direction:column;min-width:0;line-height:1.2}}
    .rt b{{font-size:{u*(3.4 if tall else 2.3):.0f}px;color:var(--fg)}}
    .rt span{{font-size:{u*(2.8 if tall else 1.95):.0f}px;color:var(--fg-soft);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .hero{{flex:1;align-self:{'center' if tall else 'flex-start'}}}
    .scan{{display:flex;flex-direction:column;align-items:{'center' if tall else 'flex-start'};gap:{u*2:.0f}px;flex:none;
      {'text-align:center' if tall else ''}}}
    .qrbox{{background:#fff;border-radius:{u*3:.0f}px;padding:{u*1.6:.0f}px;box-shadow:0 {u*1:.0f}px {u*4:.0f}px rgba(2,12,27,.30)}}
    .qrbox img{{display:block;width:{qr_px:.0f}px;height:{qr_px:.0f}px}}
    .cta{{display:flex;flex-direction:column;gap:{u*.6:.0f}px;max-width:{W * 0.86 if tall else max(qr_px + u * 4, u * 40):.0f}px}}
    .cta .k{{font-size:{u*(2.8 if tall else 2):.0f}px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--accent-text)}}
    .cta .v{{font-size:{u*(4.6 if tall else 2.7):.0f}px;font-weight:800;color:var(--fg);word-break:break-all;line-height:1.15}}
    .cta .n{{font-size:{u*(2.8 if tall else 2):.0f}px;color:var(--fg-soft)}}
    """
    if wide:
        css += f""".wrap{{flex-direction:row;gap:{u*5:.0f}px}} .left{{flex:1;min-width:0;display:flex;flex-direction:column;gap:{u*2:.0f}px}}
        .left>*{{flex:none}} .side{{display:flex;align-items:center}} .main{{align-items:flex-start}}"""
    scan = (f"<div class='scan'><div class='qrbox'><img src='{qr_uri}' alt=''></div>"
            f"<div class='cta'><span class='k'>Scan or visit</span><span class='v'>{esc(short)}</span><span class='n'>{cta_note}</span></div></div>")
    intro = (f"<div class='top'>{page.brandmark(True, True)}</div><span class='eyebrow'>{esc(eyebrow)}</span>"
             f"<h1 class='headline'>{_rich(head)}</h1><p class='lead'>{esc(sub)}</p>")
    if wide:
        inner = f"<div class='left'>{intro}<div class='main'>{body_block}</div></div><div class='side'>{scan}</div>"
    else:
        inner = f"{intro}<div class='main'>{body_block}{scan}</div>"
    body = f"<div class='canvas'>{page.mesh(item.get('key', 'all'))}<div class='wrap'>{inner}</div></div>"
    return page.document(body, css, title="Follow us")


def _rich(s: str) -> str:
    from app.services.media_templates.base import rich

    return rich(s)


async def card_png(item: dict, kit: dict, brand, shape: str, theme: str, logo_png: bytes | None) -> bytes:
    from app.services import media_render
    from app.services.media_templates.base import data_uri

    code = qr_png(item["short_url"], px=900, fg="#0b1d33", bg="#ffffff", style="square", logo_png=logo_png)
    W, H = SHAPES.get(shape, SHAPES["square"])
    return await media_render.render_png(card_html(item, kit, brand, shape, theme, data_uri(code, "image/png")), W, H)


# ------------------------------------------------------------------ public page
def page_html(kit: dict, org: Organization, logo_uri: str | None, colors: dict) -> str:
    s = kit["settings"]
    primary = colour(colors.get("primary"), "#075492")
    dark = colour(colors.get("dark"), "#062f59")
    rows = []
    for it in kit["items"]:
        if not (it["include"] and it["url"] and it.get("code")):
            continue
        code, bg = BADGE.get(it["platform"], (it["platform"][:2].upper(), "#64748B"))
        verb = VERB.get(it["platform"], "Follow on")
        handle = html.escape(it.get("handle") or it["name"])
        rows.append(
            f"<a class='btn' href='/r/{quote(it['code'])}' rel='noopener'><span class='b' style='background:{bg}'>{html.escape(code)}</span>"
            f"<span class='t'><b>{html.escape(verb)} {html.escape(it['platform_label'])}</b><small>{handle}</small></span>"
            f"<span class='go' aria-hidden='true'>→</span></a>")
    logo = (f"<img class='logo' src='{logo_uri}' alt=''>" if logo_uri else
            f"<div class='logo mono'>{html.escape((org.name or '?')[:1])}</div>")
    light = s["theme"] == "light"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(s['title'])}</title><meta name="description" content="{html.escape(s['message'])}">
<meta property="og:title" content="{html.escape(s['title'])}"><meta property="og:description" content="{html.escape(s['message'])}">
<meta name="robots" content="noindex">
<style>
*{{box-sizing:border-box;margin:0}}body{{font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;min-height:100vh;
background:{'#f8fafc' if light else f'radial-gradient(ellipse at 70% 0%,{primary} 0%,{dark} 55%,#020c1b 100%)'};color:{'#0f172a' if light else '#fff'};
display:flex;justify-content:center;padding:40px 16px}}
main{{width:100%;max-width:520px;text-align:center}}
.logo{{width:84px;height:84px;border-radius:22px;background:#fff;object-fit:contain;padding:8px;box-shadow:0 10px 30px rgba(0,0,0,.25)}}
.mono{{display:inline-grid;place-items:center;font-size:40px;font-weight:800;color:{primary}}}
h1{{font-size:28px;line-height:1.15;margin:18px 0 8px;letter-spacing:-.02em}}
p.m{{color:{'#475569' if light else 'rgba(255,255,255,.78)'};line-height:1.5;margin-bottom:26px}}
.btn{{display:flex;align-items:center;gap:14px;text-align:left;text-decoration:none;color:inherit;padding:12px 14px;margin:10px 0;border-radius:16px;
background:{'#fff' if light else 'rgba(255,255,255,.08)'};border:1px solid {'#e2e8f0' if light else 'rgba(255,255,255,.14)'};transition:transform .12s,background .12s}}
.btn:hover{{transform:translateY(-1px);background:{'#f1f5f9' if light else 'rgba(255,255,255,.14)'}}}
.b{{width:44px;height:44px;border-radius:12px;display:grid;place-items:center;color:#fff;font-weight:800;font-size:17px;flex:none}}
.t{{flex:1;min-width:0;display:flex;flex-direction:column}}.t b{{font-size:16px}}.t small{{opacity:.7;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.go{{opacity:.5;font-size:20px}}footer{{margin-top:28px;font-size:12px;opacity:.55}}
</style></head><body><main>{logo}<h1>{html.escape(s['title'])}</h1><p class="m">{html.escape(s['message'])}</p>
{''.join(rows) or '<p class="m">No channels yet.</p>'}
<footer>{html.escape(org.name or '')}</footer></main></body></html>"""


# ------------------------------------------------------------------ zip pack
def pack(files: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


def file_stem(item: dict) -> str:
    if item.get("key") == "all":
        return "all-channels"
    raw = f"{item['platform_label']}-{item.get('handle') or item['name']}"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:60] or "profile"
