"""Find every social profile the organization mentions and normalise them into (platform, handle) profiles.

Inputs, merged and de-duplicated:
  - Organization.social_links (Settings → Organization)
  - CompanyProfile.social_links (Company intelligence)
  - social links found on the crawled website (KnowledgeSource meta + every crawled page's links / JSON-LD sameAs)
  - connected SocialAccount rows (Settings → Social accounts)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompanyProfile, KnowledgeDocument, KnowledgeSource, Organization, SocialAccount

LABELS = {
    "linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram", "x": "X (Twitter)", "youtube": "YouTube",
    "threads": "Threads", "pinterest": "Pinterest", "github": "GitHub", "bluesky": "Bluesky", "mastodon": "Mastodon",
    "tiktok": "TikTok", "medium": "Medium", "other": "Other",
}

# How much each platform matters for a B2B Indian tech company (coverage and the overall average use these).
IMPORTANCE = {"linkedin": 3.0, "youtube": 2.0, "instagram": 2.0, "x": 2.0, "facebook": 1.5, "github": 1.0, "threads": 1.0,
              "pinterest": 0.5, "bluesky": 0.75, "mastodon": 0.5, "tiktok": 0.5, "medium": 0.5, "other": 0.5}

# Connected account platform → profile platform. Personal LinkedIn profiles, Google Business Profile and the hidden
# Google holder aren't company social profiles, so they are left out.
ACCOUNT_FAMILY = {"facebook_page": "facebook", "instagram": "instagram", "threads": "threads", "youtube": "youtube",
                  "linkedin_org": "linkedin", "pinterest": "pinterest", "bluesky": "bluesky", "mastodon": "mastodon", "x": "x"}

_HOSTS = {
    "linkedin.com": "linkedin", "facebook.com": "facebook", "fb.com": "facebook", "fb.me": "facebook",
    "instagram.com": "instagram", "instagr.am": "instagram", "x.com": "x", "twitter.com": "x",
    "youtube.com": "youtube", "threads.net": "threads", "threads.com": "threads", "pinterest.com": "pinterest",
    "pinterest.co.uk": "pinterest", "pinterest.ca": "pinterest", "github.com": "github", "bsky.app": "bluesky",
    "tiktok.com": "tiktok", "medium.com": "medium",
}
_RESERVED = {
    "instagram": {"p", "reel", "reels", "explore", "stories", "accounts", "tv"},
    "x": {"intent", "share", "home", "i", "search", "hashtag", "explore"},
    "facebook": {"sharer", "sharer.php", "share", "dialog", "groups", "events", "watch", "hashtag", "login", "plugins"},
    "pinterest": {"pin", "search", "ideas"},
    "github": {"orgs", "features", "about", "topics", "explore", "sponsors"},
}
_SAFE = re.compile(r"^[A-Za-z0-9._@:/-]{1,100}$")


@dataclass
class ProfileRef:
    platform: str
    handle: str  # what the platform APIs expect (YouTube keeps '@' or the UC… id; Mastodon is @user@host)
    url: str
    kind: str = ""  # linkedin: company | showcase | person | school ; youtube: handle | channel | custom
    sources: list[str] = field(default_factory=list)  # organization | company_profile | website | connected
    account_id: uuid.UUID | None = None
    account_platform: str | None = None
    account_status: str | None = None

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.handle.lower()}"

    @property
    def label(self) -> str:
        return LABELS.get(self.platform, self.platform.title())

    def add_source(self, s: str) -> None:
        if s not in self.sources:
            self.sources.append(s)


def _host(h: str) -> str:
    h = (h or "").lower().strip(".")
    for p in ("www.", "m.", "mobile.", "web.", "business."):
        h = h.removeprefix(p)
    return h


def _family(host: str) -> str | None:
    if host in _HOSTS:
        return _HOSTS[host]
    parts = host.split(".")
    for n in (2, 3):  # in.linkedin.com, in.pinterest.com, uk.linkedin.com
        tail = ".".join(parts[-n:])
        if tail in _HOSTS:
            return _HOSTS[tail]
    if host.endswith(".pinterest.com") or re.match(r"^pinterest\.[a-z.]+$", host):
        return "pinterest"
    return None


def parse_profile_url(raw: str | None) -> ProfileRef | None:
    """Turn a pasted link (or @name) into a profile. None when it isn't a profile link (share buttons, posts, …)."""
    v = (raw or "").strip().strip("<>\"'")
    if not v or " " in v:
        return None
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v.lstrip("/")
    try:
        parts = urlsplit(v)
    except ValueError:
        return None
    host = _host(parts.hostname or "")
    if not host or "." not in host:
        return None
    segs = [s for s in parts.path.split("/") if s]
    fam = _family(host)

    def ref(platform: str, handle: str, url: str, kind: str = "") -> ProfileRef | None:
        handle = handle.strip()
        if not handle or not _SAFE.match(handle):
            return None
        return ProfileRef(platform, handle, url, kind)

    if fam is None:
        if segs and segs[0].startswith("@") and len(segs[0]) > 1:  # mastodon.social/@name (any Mastodon server)
            user = segs[0][1:]
            return ref("mastodon", f"@{user}@{host}", f"https://{host}/@{user}")
        if not segs:
            return None
        path = "/".join(segs)[:200]
        return ProfileRef("other", f"{host}/{path}".lower(), f"https://{host}/{path}")
    low = [s.lower() for s in segs]
    if fam in _RESERVED and low and low[0] in _RESERVED[fam]:
        if fam == "facebook" and low[0] == "groups" and len(segs) > 1:
            return ref("facebook", segs[1], f"https://www.facebook.com/groups/{segs[1]}", "group")
        return None
    if fam == "linkedin":
        if len(segs) >= 2 and low[0] in ("company", "showcase", "school"):
            return ref("linkedin", segs[1].lower(), f"https://www.linkedin.com/{low[0]}/{segs[1].lower()}/", low[0])
        if len(segs) >= 2 and low[0] == "in":
            return ref("linkedin", "in/" + segs[1].lower(), f"https://www.linkedin.com/in/{segs[1].lower()}/", "person")
        return None
    if fam == "facebook":
        if low[:1] == ["profile.php"] or not segs:
            pid = (parse_qs(parts.query).get("id") or [""])[0]
            return ref("facebook", pid, f"https://www.facebook.com/profile.php?id={pid}") if pid.isdigit() else None
        if low[0] == "pages" and len(segs) >= 2:
            last = segs[-1]
            h = last if last.isdigit() else segs[1]
            return ref("facebook", h.lower(), f"https://www.facebook.com/{h}")
        return ref("facebook", segs[0].lower(), f"https://www.facebook.com/{segs[0]}")
    if not segs:
        return None
    if fam == "instagram":
        return ref("instagram", segs[0].lstrip("@").lower(), f"https://www.instagram.com/{segs[0].lstrip('@').lower()}/")
    if fam == "x":
        h = segs[0].lstrip("@")
        return ref("x", h.lower(), f"https://x.com/{h}")
    if fam == "youtube":
        if segs[0].startswith("@"):
            return ref("youtube", segs[0], f"https://www.youtube.com/{segs[0]}", "handle")
        if low[0] == "channel" and len(segs) > 1:
            return ref("youtube", segs[1], f"https://www.youtube.com/channel/{segs[1]}", "channel")
        if low[0] in ("c", "user") and len(segs) > 1:
            return ref("youtube", segs[1], f"https://www.youtube.com/{low[0]}/{segs[1]}", "custom")
        if low[0] in ("watch", "shorts", "playlist", "results", "embed", "live", "feed"):
            return None
        return ref("youtube", segs[0], f"https://www.youtube.com/{segs[0]}", "custom")
    if fam == "threads":
        h = segs[0].lstrip("@")
        return ref("threads", h.lower(), f"https://www.threads.net/@{h.lower()}")
    if fam == "pinterest":
        return ref("pinterest", segs[0].lower(), f"https://www.pinterest.com/{segs[0].lower()}/")
    if fam == "github":
        return ref("github", segs[0].lower(), f"https://github.com/{segs[0]}")
    if fam == "bluesky":
        if low[0] == "profile" and len(segs) > 1:
            return ref("bluesky", segs[1].lstrip("@").lower(), f"https://bsky.app/profile/{segs[1].lstrip('@').lower()}")
        return None
    if fam in ("tiktok", "medium"):
        if segs[0].startswith("@"):
            h = segs[0][1:]
            return ref(fam, h.lower(), f"https://www.{'tiktok' if fam == 'tiktok' else 'medium'}.com/@{h}")
        return None
    return None


def account_refs(acc: SocialAccount) -> list[ProfileRef]:
    """The keys a connected account could match (from its profile link and its handle)."""
    fam = ACCOUNT_FAMILY.get(acc.platform)
    if not fam:
        return []
    out: list[ProfileRef] = []
    if acc.profile_url:
        r = parse_profile_url(acc.profile_url)
        if r and r.platform == fam:
            out.append(r)
    h = (acc.handle or "").strip()
    if h and fam != "linkedin":
        if fam == "youtube":
            hh = h if h.startswith("@") else "@" + h
            out.append(ProfileRef("youtube", hh, f"https://www.youtube.com/{hh}", "handle"))
        elif fam == "mastodon":
            out.append(ProfileRef("mastodon", h if h.startswith("@") else "@" + h, acc.profile_url or ""))
        elif fam == "bluesky":
            out.append(ProfileRef("bluesky", h.lower(), f"https://bsky.app/profile/{h.lower()}"))
        else:
            r = parse_profile_url({"facebook": "facebook.com/", "instagram": "instagram.com/", "threads": "threads.net/@",
                                   "pinterest": "pinterest.com/", "x": "x.com/"}[fam] + h.lstrip("@"))
            if r:
                out.append(r)
    if fam == "youtube" and acc.external_id:
        out.append(ProfileRef("youtube", acc.external_id, f"https://www.youtube.com/channel/{acc.external_id}", "channel"))
    if fam == "linkedin" and acc.handle:
        out.append(ProfileRef("linkedin", acc.handle.lower(), f"https://www.linkedin.com/company/{acc.handle.lower()}/", "company"))
    return out


def _urls(v) -> list[str]:
    if isinstance(v, str):
        return [u for u in re.split(r"[\s,;]+", v) if u]
    if isinstance(v, list):
        return [u for u in v if isinstance(u, str)]
    return []


@dataclass
class Inputs:
    profiles: list[ProfileRef]
    website: str | None
    website_domain: str | None
    website_links: set[str]  # profile keys linked from the website
    website_crawled: bool
    org_links: dict[str, str]
    unparsed: list[str]  # links that couldn't be read as a profile


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    u = url if re.match(r"^https?://", url, re.I) else "https://" + url
    try:
        h = (urlsplit(u).hostname or "").lower()
    except ValueError:
        return None
    return h.removeprefix("www.") or None


async def gather(session: AsyncSession, org: Organization) -> Inputs:
    found: dict[str, ProfileRef] = {}
    unparsed: list[str] = []

    def add(url: str, source: str) -> ProfileRef | None:
        r = parse_profile_url(url)
        if r is None:
            if url and source != "website":
                unparsed.append(url)
            return None
        cur = found.get(r.key)
        if cur is None:
            found[r.key] = cur = r
        cur.add_source(source)
        return cur

    org_links = {k: v for k, v in (org.social_links or {}).items() if isinstance(v, str) and v.strip()}
    for _, v in org_links.items():
        for u in _urls(v):
            add(u, "organization")
    profile = await session.scalar(select(CompanyProfile).limit(1))
    for _, v in ((profile.social_links if profile else None) or {}).items():
        for u in _urls(v):
            add(u, "company_profile")

    website_keys: set[str] = set()
    src = (await session.scalars(select(KnowledgeSource).where(KnowledgeSource.kind == "website", KnowledgeSource.status == "ready")
                                 .order_by(KnowledgeSource.last_crawled_at.desc().nulls_last()).limit(1))).first()
    site_urls: list[str] = []
    if src is not None:
        for v in ((src.meta or {}).get("social_links") or {}).values():
            site_urls += _urls(v)
        metas = (await session.scalars(select(KnowledgeDocument.meta).where(KnowledgeDocument.source_id == src.id).limit(400))).all()
        for m in metas:
            m = m if isinstance(m, dict) else {}
            for v in (m.get("social") or {}).values():
                site_urls += _urls(v)
            site_urls += _urls(m.get("jsonld_same_as"))
    for u in site_urls:
        r = parse_profile_url(u)
        if r is None or r.platform == "other":  # sameAs often lists Wikipedia, Crunchbase …: not social profiles
            continue
        website_keys.add(r.key)
        add(u, "website")

    accounts = (await session.scalars(select(SocialAccount).where(SocialAccount.status != "removed"))).all()
    for acc in accounts:
        refs = account_refs(acc)
        if not refs:
            continue
        target = next((found[r.key] for r in refs if r.key in found), None)
        if target is None:
            fam = refs[0].platform
            same = [p for p in found.values() if p.platform == fam and p.account_id is None]
            n_acc = sum(1 for a in accounts if ACCOUNT_FAMILY.get(a.platform) == fam)
            # One link and one account on the same platform with nothing to tell them apart: treat them as the same page.
            if len(same) == 1 and n_acc == 1 and not (acc.handle or acc.profile_url):
                target = same[0]
        if target is None:
            target = refs[0]
            found[target.key] = target
        if target.account_id is None or (acc.status == "connected" and target.account_status != "connected"):
            target.account_id, target.account_platform, target.account_status = acc.id, acc.platform, acc.status
        target.add_source("connected")
        for r in refs:  # the website may link the account under another of its keys (channel id vs @handle)
            if r.key in website_keys:
                website_keys.add(target.key)

    profs = sorted(found.values(), key=lambda p: (-IMPORTANCE.get(p.platform, 0.5), p.platform, p.handle))
    # Personal LinkedIn profiles are people, not the company page: keep them out of the company score.
    profs = [p for p in profs if p.kind != "person"]
    site = org.website or (src.url if src else None)
    return Inputs(profiles=profs, website=site, website_domain=domain_of(site), website_links=website_keys,
                  website_crawled=src is not None, org_links=org_links, unparsed=unparsed[:10])
