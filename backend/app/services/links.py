"""Link tracking: short links (/r/<code>) that add UTM tags and count clicks anonymously.

  wrap_for_publish(session, post, account) -> restore()   swap URLs in a post for tracked links right before the
                                                           platform adapter posts it (never raises)
  create_link(...) / links_for_post(...)                  manual and automatic links
  record_click(org, link, headers)                        cheap per-day upsert, bots and previews skipped
  redirect_url(link)                                      target with UTM parameters merged in

Settings live in Organization.publishing["link_tracking"] = {"enabled": bool}. Short links use PUBLIC_BASE_URL
(the Cloudflare Tunnel address); without it nothing is wrapped. Nothing personal is stored: no IP addresses,
cookies or user agents, only a day, the referring site's domain, the app (from the user agent) and mobile/desktop.
"""

from __future__ import annotations

import logging
import re
import secrets
import string
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import Campaign, LinkClick, Organization, Post, SocialAccount, TrackedLink
from app.services.linkinbio import local_day

log = logging.getLogger("links")

ALPHABET = string.ascii_letters + string.digits  # base62
CODE_LEN = 7
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,16}$")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
DEFAULT_CAMPAIGN = "hrp-studio"

# URLs inside post text. Trailing punctuation is trimmed afterwards ("see https://x.y/a." keeps the full stop).
URL_RE = re.compile(r"https?://[^\s<>\"'\[\]{}|\\^`]+", re.IGNORECASE)
_TRAIL = ".,;:!?)'\"»”’"

# Link previews, crawlers and scripts: still redirected (so previews work) but never counted.
BOT_RE = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|facebookcatalog|meta-externalagent|embedly|"
                    r"whatsapp|telegram|discord|skype|slack|vkshare|bitly|tumblr|redditbot|quora link|outbrain|"
                    r"pinterest/0\.|headless|lighthouse|curl|wget|python|httpx|okhttp|go-http|java/|axios|node-fetch|"
                    r"monitor|uptime|scan", re.IGNORECASE)

# Social apps' in-app browsers announce themselves in the user agent.
APP_RE = [
    ("linkedin", re.compile(r"LinkedInApp", re.I)),
    ("instagram", re.compile(r"Instagram", re.I)),
    ("threads", re.compile(r"Barcelona|Threads", re.I)),
    ("facebook", re.compile(r"FBAN|FBAV|FB_IAB|FBIOS|FB4A", re.I)),
    ("x", re.compile(r"Twitter|TwitterAndroid", re.I)),
    ("pinterest", re.compile(r"Pinterest", re.I)),
    ("tiktok", re.compile(r"musical_ly|BytedanceWebview|TikTok", re.I)),
    ("snapchat", re.compile(r"Snapchat", re.I)),
    ("whatsapp", re.compile(r"WhatsApp", re.I)),
]
MOBILE_RE = re.compile(r"Mobi|Android|iPhone|iPad|iPod", re.I)

# Short-link and app domains the platforms put in the Referer header.
REFERRER_ALIASES = {
    "t.co": "x.com", "twitter.com": "x.com", "lnkd.in": "linkedin.com", "l.facebook.com": "facebook.com",
    "lm.facebook.com": "facebook.com", "m.facebook.com": "facebook.com", "l.instagram.com": "instagram.com",
    "l.threads.net": "threads.net", "out.reddit.com": "reddit.com", "com.linkedin.android": "linkedin.com",
    "android-app": "app",
}

# utm_source per platform id (friendlier than our internal ids).
SOURCE_NAMES = {"linkedin_org": "linkedin", "linkedin_person": "linkedin", "facebook_page": "facebook",
                "gbp": "google-business", "manual": "social"}
# Extra "channels" a manual link can be made for (besides the social platforms).
CHANNELS = {"bio": ("link-in-bio", "social"), "email": ("email", "email"), "website": ("website", "referral"),
            "print": ("print", "offline"), "other": ("hrp-studio", "referral")}
# Captions on these platforms don't make links clickable, so there is nothing to track.
NO_LINK_PLATFORMS = {"instagram", "tiktok"}


# ------------------------------------------------------------------ settings & addresses
def public_base() -> str:
    """The public address short links use (PUBLIC_BASE_URL), or "" when it isn't set."""
    return (get_settings().public_base_url or "").strip().rstrip("/").removesuffix("/api/v1").rstrip("/")


def short_url(code: str, base: str | None = None) -> str:
    base = public_base() if base is None else base
    return f"{base or ''}/r/{code}"


def settings_of(org: Organization | None) -> dict:
    cfg = dict(((org.publishing if org is not None else None) or {}).get("link_tracking") or {})
    return {"enabled": bool(cfg.get("enabled", False))}


def slugify(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]


def safe_target(u: str | None) -> str | None:
    u = (u or "").strip()
    if not u or len(u) > 2000 or any(ch in u for ch in "\r\n\t <>\"'"):
        return None
    parts = urlsplit(u)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or "." not in (parts.hostname or ""):
        return None
    return u


def is_short_link(u: str, base: str | None = None) -> bool:
    base = public_base() if base is None else base
    return bool(base) and u.lower().startswith(f"{base.lower()}/r/")


def default_utm(platform: str | None, campaign: Campaign | None = None, post_id: uuid.UUID | None = None) -> dict:
    if platform in CHANNELS:
        source, medium = CHANNELS[platform]
    elif platform:
        source, medium = SOURCE_NAMES.get(platform, platform.replace("_", "-")), "social"
    else:
        source, medium = CHANNELS["other"]
    utm = {"utm_source": source, "utm_medium": medium,
           "utm_campaign": slugify(campaign.name) if campaign is not None and slugify(campaign.name) else DEFAULT_CAMPAIGN}
    if post_id:
        utm["utm_content"] = str(post_id)[:8]
    return utm


def clean_utm(utm: dict | None) -> dict:
    out = {}
    for k, v in (utm or {}).items():
        k = str(k).strip().lower()
        if not k.startswith("utm_"):
            k = f"utm_{k}"
        v = re.sub(r"\s+", "-", str(v or "").strip())[:100]
        if k in UTM_KEYS and v:
            out[k] = v
    return out


def redirect_url(link: TrackedLink) -> str:
    """target_url plus the link's UTM tags; parameters already in the target URL are kept as they are."""
    parts = urlsplit(link.target_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    present = {k.lower() for k, _ in query}
    extra = [(k, v) for k, v in (link.utm or {}).items() if k in UTM_KEYS and v and k not in present]
    if not extra:
        return link.target_url
    return urlunsplit(parts._replace(query=urlencode(query + extra)))


def link_dict(link: TrackedLink, base: str | None = None, clicks: int | None = None) -> dict:
    return {"id": str(link.id), "code": link.code, "short_url": short_url(link.code, base), "target_url": link.target_url,
            "tracked_url": redirect_url(link), "utm": link.utm or {}, "post_id": str(link.post_id) if link.post_id else None,
            "campaign_id": str(link.campaign_id) if link.campaign_id else None, "platform": link.platform,
            "label": link.label, "source": link.source, "clicks_total": link.clicks_total or 0,
            "clicks": (link.clicks_total or 0) if clicks is None else clicks,
            "last_click_at": link.last_click_at.isoformat() if link.last_click_at else None,
            "created_at": link.created_at.isoformat() if link.created_at else None}


# ------------------------------------------------------------------ creating links
async def _new_code() -> str:
    for _ in range(10):
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        async with platform_session() as ps:  # codes are unique across all organizations
            if await ps.scalar(select(TrackedLink.id).where(TrackedLink.code == code)) is None:
                return code
    raise RuntimeError("Couldn't pick a free short code.")


async def create_link(session: AsyncSession, org_id: uuid.UUID, target_url: str, *, platform: str | None = None,
                      post: Post | None = None, campaign_id: uuid.UUID | None = None, label: str | None = None,
                      utm: dict | None = None, source: str = "manual", created_by: uuid.UUID | None = None) -> TrackedLink:
    campaign_id = campaign_id or (post.campaign_id if post is not None else None)
    campaign = await session.get(Campaign, campaign_id) if campaign_id else None
    tags = default_utm(platform, campaign, post.id if post is not None else None) | clean_utm(utm)
    link = TrackedLink(id=uuid.uuid4(), organization_id=org_id, code=await _new_code(), target_url=target_url,
                       utm=tags, post_id=post.id if post is not None else None,
                       campaign_id=campaign.id if campaign is not None else None, platform=platform,
                       label=(label or "").strip()[:200] or None, source=source, created_by=created_by, clicks_total=0)
    session.add(link)
    await session.flush()
    return link


def find_urls(text: str | None) -> list[str]:
    out = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(_TRAIL)
        if u.count("(") < u.count(")"):
            u = u.rstrip(")")
        if u and u not in out:
            out.append(u)
    return out


def replace_urls(text: str | None, mapping: dict[str, str]) -> str | None:
    if not text or not mapping:
        return text

    def sub(m: re.Match) -> str:
        raw = m.group(0)
        for u in sorted(mapping, key=len, reverse=True):
            if raw.startswith(u) and raw[len(u):].strip(_TRAIL) == "":
                return mapping[u] + raw[len(u):]
        return raw

    return URL_RE.sub(sub, text)


def links_allowed(account: SocialAccount | None, platform: str) -> bool:
    if platform in NO_LINK_PLATFORMS:
        return False
    if platform == "x" and not bool(((account.settings if account is not None else None) or {}).get("allow_links")):
        return False
    return True


async def links_for_post(org_id: uuid.UUID, post: Post, account: SocialAccount | None, urls: list[str]) -> dict[str, TrackedLink]:
    """Existing tracked links for this post and platform are reused; missing ones are created and committed in their
    own transaction, so the short links keep working even if the publishing transaction is rolled back later."""
    platform = (account.platform if account is not None else None) or post.platform
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        existing = (await s.scalars(select(TrackedLink).where(TrackedLink.post_id == post.id,
                                                              TrackedLink.platform == platform,
                                                              TrackedLink.target_url.in_(urls)))).all()
        found = {lk.target_url: lk for lk in existing}
        post_row = await s.get(Post, post.id)
        for u in urls:
            if u not in found:
                found[u] = await create_link(s, org_id, u, platform=platform, post=post_row or post, source="auto",
                                             label=(post.title or post.topic or None))
        await s.commit()
    return found


def _too_long(post: Post, platform: str) -> bool:
    from app.services.platforms import PLATFORMS
    from app.services.social.common import compose_text
    p = PLATFORMS.get(platform)
    limit = getattr(p, "max_chars", None) if p else None
    return bool(limit) and len(compose_text(post, link=bool(post.link_url))) > limit


async def wrap_for_publish(session: AsyncSession, post: Post, account: SocialAccount | None) -> Callable[[], None]:
    """Swap the URLs in the post's text, first comment and link for tracked short links, just for the platform call.

    Returns restore(), which puts the original text back (so the editor keeps what the person wrote); which links
    were used is kept in post.meta["tracked_links"]. Never raises: on any problem the original URLs are posted.
    """
    original = {"text": post.text, "link_url": post.link_url, "first_comment": post.first_comment}

    def restore() -> None:
        for k, v in original.items():
            if getattr(post, k) != v:
                setattr(post, k, v)

    try:
        base = public_base()
        if not base:
            return restore
        org = await session.get(Organization, post.organization_id)
        if not settings_of(org)["enabled"]:
            return restore
        platform = (account.platform if account is not None else None) or post.platform
        if not links_allowed(account, platform):
            return restore
        urls = []
        for field in ("text", "first_comment"):
            urls += find_urls(original[field])
        if post.link_url:
            urls.append(post.link_url.strip())
        urls = [u for u in dict.fromkeys(urls) if safe_target(u) and not is_short_link(u, base)]
        if not urls:
            return restore
        links = await links_for_post(post.organization_id, post, account, urls)
        mapping = {u: short_url(lk.code, base) for u, lk in links.items()}
        post.text = replace_urls(original["text"], mapping)
        post.first_comment = replace_urls(original["first_comment"], mapping)
        if post.link_url and post.link_url.strip() in mapping:
            post.link_url = mapping[post.link_url.strip()]
        if _too_long(post, platform):
            # A short link can be longer than the URL it replaces; a post that fitted must not fail because of it.
            log.info("Link tracking skipped for post %s: the tracked link would push it past %s's limit", post.id, platform)
            restore()
            return restore
        post.meta = {**(post.meta or {}), "tracked_links": [
            {"id": str(lk.id), "code": lk.code, "url": u, "short_url": mapping[u]} for u, lk in links.items()]}
    except Exception:  # noqa: BLE001 - tracking must never stop a post going out
        log.warning("Link tracking skipped for post %s", post.id, exc_info=True)
        restore()
    return restore


# ------------------------------------------------------------------ clicks
def countable(headers) -> bool:
    ua = headers.get("user-agent") or ""
    if not ua or BOT_RE.search(ua):
        return False
    purpose = (headers.get("sec-purpose") or headers.get("purpose") or headers.get("x-purpose") or "").lower()
    return "prefetch" not in purpose and "prerender" not in purpose and "preview" not in purpose


def referrer_bucket(referer: str | None) -> str:
    if not referer:
        return "direct"
    try:
        parts = urlsplit(referer)
        host = (parts.hostname or "").lower()
        if parts.scheme == "android-app":
            host = host or "app"
    except ValueError:
        return "other"
    if not host:
        return "direct"
    host = REFERRER_ALIASES.get(host, host)
    host = host.removeprefix("www.").removeprefix("m.").removeprefix("mobile.")
    host = REFERRER_ALIASES.get(host, host)
    own = (urlsplit(public_base()).hostname or "").lower() if public_base() else ""
    if own and host == own:
        return "direct"
    return host[:80]


def app_bucket(ua: str | None) -> str:
    for name, rx in APP_RE:
        if rx.search(ua or ""):
            return name
    return "browser"


def device_bucket(ua: str | None) -> str:
    return "mobile" if MOBILE_RE.search(ua or "") else "desktop"


async def find_public(code: str) -> tuple[TrackedLink, Organization] | None:
    if not CODE_RE.match(code or ""):
        return None
    async with platform_session() as ps:
        row = (await ps.execute(select(TrackedLink, Organization)
                                .join(Organization, Organization.id == TrackedLink.organization_id)
                                .where(TrackedLink.code == code))).first()
    if row is None or row[1].status != "active":
        return None
    return row[0], row[1]


async def record_click(org: Organization, link: TrackedLink, headers) -> bool:
    """One upsert into the day's bucket and a +1 on the link. Returns False when the request wasn't counted."""
    if not countable(headers):
        return False
    ua = headers.get("user-agent") or ""
    async with SessionLocal() as s:
        set_tenant(s, org.id)
        stmt = insert(LinkClick).values(id=uuid.uuid4(), organization_id=org.id, link_id=link.id, day=local_day(org),
                                        referrer=referrer_bucket(headers.get("referer")), app=app_bucket(ua),
                                        device=device_bucket(ua), count=1)
        stmt = stmt.on_conflict_do_update(constraint="uq_link_clicks_bucket", set_={"count": LinkClick.count + 1})
        await s.execute(stmt)
        await s.execute(update(TrackedLink).where(TrackedLink.id == link.id)
                        .values(clicks_total=TrackedLink.clicks_total + 1, last_click_at=datetime.now(UTC)))
        await s.commit()
    return True


# ------------------------------------------------------------------ analytics
async def clicks_by_link(session: AsyncSession, start: date, end: date, link_ids=None) -> dict[uuid.UUID, int]:
    q = (select(LinkClick.link_id, func.sum(LinkClick.count)).where(LinkClick.day >= start, LinkClick.day <= end)
         .group_by(LinkClick.link_id))
    if link_ids is not None:
        q = q.where(LinkClick.link_id.in_(list(link_ids)))
    return {lid: int(n or 0) for lid, n in (await session.execute(q)).all()}


async def series(session: AsyncSession, start: date, end: date, link_ids=None) -> list[dict]:
    q = (select(LinkClick.day, func.sum(LinkClick.count)).where(LinkClick.day >= start, LinkClick.day <= end)
         .group_by(LinkClick.day))
    if link_ids is not None:
        q = q.where(LinkClick.link_id.in_(list(link_ids)))
    got = {d: int(n or 0) for d, n in (await session.execute(q)).all()}
    return [{"day": (start + timedelta(days=i)).isoformat(), "clicks": got.get(start + timedelta(days=i), 0)}
            for i in range((end - start).days + 1)]


async def breakdown(session: AsyncSession, start: date, end: date, column, link_ids=None, limit: int = 10) -> list[dict]:
    q = (select(column, func.sum(LinkClick.count).label("n")).where(LinkClick.day >= start, LinkClick.day <= end)
         .group_by(column).order_by(func.sum(LinkClick.count).desc()).limit(limit))
    if link_ids is not None:
        q = q.where(LinkClick.link_id.in_(list(link_ids)))
    return [{"key": k, "clicks": int(n or 0)} for k, n in (await session.execute(q)).all()]
