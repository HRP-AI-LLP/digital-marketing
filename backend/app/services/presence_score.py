"""Transparent scoring of social profiles and the company's overall social presence, plus rule-based suggestions.

Per profile (0–100): completeness 30, activity 30, audience 15, engagement 15, content mix 10.
Overall (0–100): completeness 15, activity 20, audience 10, engagement 15, content mix 10 (each the importance-weighted
average across profiles), cross-linking 10, brand consistency 10, platform coverage 10.
A part that can't be measured (no data) is left out and the other weights are scaled up, never guessed.
Every check carries a plain-language explanation of what was found and what it cost.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.services.presence_profiles import IMPORTANCE, LABELS, ProfileRef

PROFILE_WEIGHTS = {"completeness": 30, "activity": 30, "audience": 15, "engagement": 15, "content_mix": 10}
OVERALL_WEIGHTS = {"completeness": 15, "activity": 20, "audience": 10, "engagement": 15, "content_mix": 10,
                   "cross_linking": 10, "brand": 10, "coverage": 10}
PART_LABELS = {"completeness": "Profile completeness", "activity": "Posting activity", "audience": "Audience size",
               "engagement": "Engagement", "content_mix": "Content mix", "cross_linking": "Cross-linking with the website",
               "brand": "Brand consistency", "coverage": "Platform coverage"}
PART_HOW = {
    "completeness": "Brand name, a bio that says what you offer, a link back to your website, profile and cover images, contact details.",
    "activity": "Posts in the last 30 days against each platform's good practice, how recent the last post is, and how many weeks had a post.",
    "audience": "Followers against what's realistic for a growing B2B company on each platform, plus recent growth when we have history.",
    "engagement": "Average likes, comments and shares per post compared with the number of followers.",
    "content_mix": "Whether you use the formats each platform favours (Reels, carousels, Shorts, documents, video).",
    "cross_linking": "Your website links to each profile, and each profile links back to your website.",
    "brand": "The same name, handle and logo on every platform.",
    "coverage": "Whether you have the platforms that matter most for a B2B tech company in India.",
}

# Good-practice posting frequency (posts per 30 days) and its plain wording.
TARGET_30D = {"linkedin": 12, "instagram": 12, "facebook": 12, "x": 30, "youtube": 4, "threads": 16, "pinterest": 20,
              "bluesky": 16, "mastodon": 12, "github": 4, "tiktok": 12, "medium": 2, "other": 4}
CADENCE = {"linkedin": "3 times a week", "instagram": "3 times a week", "facebook": "3 times a week", "x": "about once a day",
           "youtube": "once a week (Shorts count)", "threads": "4 times a week", "pinterest": "5 pins a week",
           "bluesky": "4 times a week", "mastodon": "3 times a week", "github": "update a repository every week",
           "tiktok": "3 times a week", "medium": "2 articles a month", "other": "every week"}
# Followers that make a profile "established" for a growing B2B company (log scale up to this number).
AUDIENCE_TARGET = {"linkedin": 5000, "instagram": 5000, "facebook": 5000, "x": 3000, "youtube": 2000, "github": 300,
                   "threads": 2000, "pinterest": 2000, "bluesky": 1000, "mastodon": 500, "tiktok": 5000, "medium": 500,
                   "other": 1000}
# Typical engagement per post as a share of followers.
ENGAGEMENT_BENCH = {"linkedin": 0.02, "instagram": 0.015, "facebook": 0.005, "x": 0.005, "youtube": 0.03, "threads": 0.02,
                    "bluesky": 0.02, "mastodon": 0.02, "pinterest": 0.003, "tiktok": 0.04}
# Formats each platform's feed favours, and how many of them we'd like to see used.
FAVOURED = {"instagram": ({"reel", "video", "carousel"}, 2), "linkedin": ({"document", "carousel", "video", "image"}, 2),
            "youtube": ({"short", "video"}, 2), "facebook": ({"video", "reel", "image", "carousel"}, 2),
            "x": ({"image", "video", "gif"}, 1), "threads": ({"image", "video", "carousel"}, 1),
            "bluesky": ({"image", "carousel", "video"}, 1), "mastodon": ({"image", "carousel", "video"}, 1),
            "pinterest": ({"video", "image", "carousel"}, 2), "tiktok": ({"video"}, 1)}
FORMAT_LABELS = {"reel": "Reels", "video": "videos", "carousel": "carousels", "document": "document (PDF) posts",
                 "image": "images", "short": "Shorts", "gif": "GIFs", "text": "text posts", "link": "link posts"}
COVER_PLATFORMS = {"facebook", "linkedin", "youtube", "x", "bluesky", "mastodon"}
CORE = [("linkedin", 30, "LinkedIn company page"), ("youtube", 15, "YouTube channel"), ("instagram", 15, "Instagram account"),
        ("x", 15, "X (Twitter) account"), ("facebook", 15, "Facebook page"), ("github", 10, "GitHub organization")]

_SUFFIXES = re.compile(r"\b(llp|pvt|private|ltd|limited|inc|llc|corp|corporation|co|company|technologies|technology|"
                       r"solutions|software|services|labs|india|the)\b\.?", re.I)
_STOP = {"and", "for", "with", "your", "our", "the", "from", "that", "this", "into", "more", "best", "platform", "solution",
         "solutions", "software", "services", "service", "company", "online", "based", "management", "system", "tool",
         "tools", "app", "apps", "digital", "smart", "powered"}


def compact(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def base_handle(platform: str, handle: str | None) -> str:
    """The comparable part of a handle: '@hrpai@mastodon.social' → 'hrpai', 'hrpai.bsky.social' → 'hrpai'.
    Empty for ids (YouTube UC…, numeric Facebook ids) and for platforms without handles."""
    h = (handle or "").strip().lstrip("@").lower()
    if platform in ("other", "linkedin") or not h:
        return ""
    if platform == "mastodon":
        h = h.split("@")[0]
    elif platform == "bluesky":
        h = h.split(".")[0]
    if re.match(r"^uc[a-z0-9_-]{20,}$", h) or h.isdigit():
        return ""
    return compact(h)


def brand_stems(org_name: str | None, domain: str | None) -> list[str]:
    out: list[str] = []
    n = compact(_SUFFIXES.sub(" ", org_name or ""))
    if len(n) >= 3:
        out.append(n)
    full = compact(org_name)
    if len(full) >= 3 and full not in out:
        out.append(full)
    if domain:
        stem = compact(domain.split(".")[0])
        if len(stem) >= 3 and stem not in out:
            out.append(stem)
    return out


def offering_keywords(names: list[str]) -> list[str]:
    """Phrases and distinctive words from the company's products, services, tagline and industries."""
    out: list[str] = []
    for n in names:
        n = (n or "").strip().lower()
        if not n:
            continue
        if 3 <= len(n) <= 60 and n not in out:
            out.append(n)
        for w in re.findall(r"[a-z][a-z0-9+#-]{2,}", n):
            if (len(w) >= 4 or w.isupper() or w in ("hr", "ai", "erp", "crm", "hrms", "rag", "llm")) and w not in _STOP and w not in out:
                out.append(w)
    return out[:60]


@dataclass
class Context:
    org_name: str
    brand: list[str]
    domain: str | None
    keywords: list[str]
    website_links: set[str]
    website_crawled: bool
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    previous: dict[str, dict] = field(default_factory=dict)  # profile key → {"followers": n, "at": iso} from an older run


def _check(cid: str, group: str, label: str, status: str, detail: str, weight: float) -> dict:
    return {"id": cid, "group": group, "label": label, "status": status, "detail": detail, "weight": weight}


def _fmt(n: float | int | None) -> str:
    if n is None:
        return "—"
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.1f}"
    return f"{int(n):,}"


def _when(d: dict) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(d.get("at")))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def mentions_domain(text_or_urls, domain: str | None) -> bool:
    if not domain:
        return False
    d = domain.lower().removeprefix("www.")
    items = text_or_urls if isinstance(text_or_urls, list) else [text_or_urls]
    for t in items:
        t = (t or "").lower()
        if re.search(r"(^|[^a-z0-9-])" + re.escape(d) + r"($|[^a-z0-9-])", t):
            return True
    return False


# ------------------------------------------------------------------ one profile
def score_profile(p: ProfileRef, data: dict, ctx: Context) -> dict:
    plat = p.platform
    limited = data.get("source") == "limited"
    nothing = data.get("source_label", "").startswith("No data")
    checks: list[dict] = []
    name, handle = data.get("name"), data.get("handle") or p.handle
    bio = (data.get("bio") or "").strip()
    domain = ctx.domain

    # --- completeness
    if ctx.brand:
        ok = any(s in compact(name) or s in compact(handle) for s in ctx.brand)
        checks.append(_check("name_brand", "completeness", "Brand name in the profile name or handle",
                             "pass" if ok else "fail",
                             f"Name “{name or '—'}”, handle {handle}." + ("" if ok else f" Neither contains “{ctx.org_name}”."), 2))
    if nothing or (limited and not bio):
        checks.append(_check("bio", "completeness", "Bio or description", "unknown", "The bio couldn't be read.", 2))
    else:
        checks.append(_check("bio", "completeness", "Bio or description", "pass" if len(bio) >= 20 else "fail",
                             f"{len(bio)} characters." if bio else "No bio or description.", 2))
    if bio and ctx.keywords:
        low = bio.lower()
        hits = [k for k in ctx.keywords if re.search(r"(^|[^a-z0-9])" + re.escape(k) + r"($|[^a-z0-9])", low)]
        checks.append(_check("bio_offerings", "completeness", "Bio says what you offer", "pass" if hits else "fail",
                             ("Mentions " + ", ".join(hits[:4]) + ".") if hits else
                             "The bio doesn't mention any of your products or services (" + ", ".join(ctx.keywords[:4]) + ")." , 2))
    elif ctx.keywords:
        checks.append(_check("bio_offerings", "completeness", "Bio says what you offer", "unknown", "No bio to check.", 2))
    else:
        checks.append(_check("bio_offerings", "completeness", "Bio says what you offer", "unknown",
                             "Add your products and services in Company intelligence so this can be checked.", 2))
    links = [data.get("website") or "", *(data.get("links") or [])]
    if not domain:
        checks.append(_check("website_link", "completeness", "Links back to your website", "unknown",
                             "Add your website in Settings → Organization so this can be checked.", 3))
    elif mentions_domain(links + [bio], domain):
        checks.append(_check("website_link", "completeness", "Links back to your website", "pass", f"Links to {domain}.", 3))
    elif nothing or (limited and not data.get("website") and not data.get("links")):
        checks.append(_check("website_link", "completeness", "Links back to your website", "unknown",
                             "The profile's links couldn't be read.", 3))
    else:
        other = data.get("website") or (data.get("links") or [None])[0]
        checks.append(_check("website_link", "completeness", "Links back to your website", "fail",
                             f"No link to {domain}." + (f" It links to {other} instead." if other else ""), 3))
    if nothing:
        checks.append(_check("avatar", "completeness", "Profile picture", "unknown", "Not readable.", 1.5))
    else:
        checks.append(_check("avatar", "completeness", "Profile picture", "pass" if data.get("avatar_url") else "fail",
                             "Present." if data.get("avatar_url") else "No profile picture found.", 1.5))
    if plat in COVER_PLATFORMS:
        hc = data.get("has_cover")
        checks.append(_check("cover", "completeness", "Cover or banner image",
                             "unknown" if hc is None else "pass" if hc else "fail",
                             "Not shared by this source." if hc is None else "Present." if hc else "No cover image.", 1))
    if data.get("has_contact") is not None:
        checks.append(_check("contact", "completeness", "Contact details (email or phone)",
                             "pass" if data["has_contact"] else "fail",
                             "Present." if data["has_contact"] else "No email or phone on the profile.", 1))

    # --- activity
    posts = data.get("posts")
    stats: dict = {"posts_30d": None, "posts_90d": None, "last_post_at": None, "days_since_last_post": None,
                   "avg_engagement": None, "engagement_rate": None, "formats": {}}
    activity = None
    target = TARGET_30D.get(plat, 4)
    if posts is not None:
        times = sorted((t for t in (_when(q) for q in posts) if t), reverse=True)
        n30 = sum(1 for t in times if t >= ctx.now - timedelta(days=30))
        n90 = sum(1 for t in times if t >= ctx.now - timedelta(days=90))
        last = times[0] if times else None
        days = (ctx.now - last).days if last else None
        stats.update(posts_30d=n30, posts_90d=n90, last_post_at=last.isoformat() if last else None, days_since_last_post=days)
        freq = min(1.0, n30 / target) if target else 1.0
        rec = 0.0 if days is None else 1.0 if days <= 7 else 0.8 if days <= 14 else 0.55 if days <= 30 else \
            0.3 if days <= 60 else 0.15 if days <= 90 else 0.0
        weeks = {(ctx.now - t).days // 7 for t in times if (ctx.now - t).days < 56}
        cons = len(weeks) / 8
        activity = round(100 * (0.45 * freq + 0.35 * rec + 0.20 * cons))
        st = "pass" if n30 >= 0.75 * target else "warn" if n30 >= 0.25 * target else "fail"
        checks.append(_check("frequency", "activity", "Posting frequency", st,
                             f"{n30} post{'s' if n30 != 1 else ''} in the last 30 days; good practice on {p.label} is {CADENCE.get(plat, 'every week')} "
                             f"(about {target} a month).", 3))
        if last is None:
            checks.append(_check("recency", "activity", "Recent posts", "fail", "No posts found.", 2))
        else:
            st = "pass" if days <= 14 else "warn" if days <= 30 else "fail"
            checks.append(_check("recency", "activity", "Recent posts", st,
                                 "Last post today." if days == 0 else f"Last post {days} day{'s' if days != 1 else ''} ago.", 2))
        if times:
            checks.append(_check("consistency", "activity", "Consistency", "pass" if len(weeks) >= 6 else "warn" if len(weeks) >= 3 else "fail",
                                 f"Posted in {len(weeks)} of the last 8 weeks.", 1))
    else:
        checks.append(_check("frequency", "activity", "Posting frequency", "unknown",
                             "Recent posts couldn't be read from this source.", 3))

    # --- audience
    fol = data.get("followers")
    audience = None
    if fol is not None:
        tgt = AUDIENCE_TARGET.get(plat, 1000)
        audience = min(100.0, 100 * math.log10(fol + 1) / math.log10(tgt + 1))
        before, since = data.get("followers_30d_ago"), "30 days"
        if before is None and ctx.previous.get(p.key, {}).get("followers") is not None:
            before, since = ctx.previous[p.key]["followers"], "the last check"
        growth = None
        if before is not None and before > 0:
            growth = (fol - before) / before
            audience += 10 if growth >= 0.05 else 5 if growth > 0 else -10 if growth < -0.02 else 0
            stats["growth_pct"] = round(growth * 100, 1)
            stats["growth_since"] = since
        audience = round(max(0.0, min(100.0, audience)))
        detail = f"{_fmt(fol)} followers (an established {p.label} profile for a growing B2B company has about {_fmt(tgt)})."
        if growth is not None:
            detail += f" {'Up' if growth >= 0 else 'Down'} {abs(round(growth * 100, 1))}% since {since}."
        checks.append(_check("audience", "audience", "Audience size", "pass" if audience >= 70 else "warn" if audience >= 40 else "fail",
                             detail, 1))
    else:
        checks.append(_check("audience", "audience", "Audience size", "unknown", "The follower count isn't available.", 1))

    # --- engagement
    engagement = None
    sample = [q for q in (posts or []) if q.get("eng") is not None and (_when(q) or ctx.now) >= ctx.now - timedelta(days=90)]
    if not sample:
        sample = [q for q in (posts or []) if q.get("eng") is not None][:SAMPLE_FALLBACK]
    if not sample:
        sample = [q for q in data.get("engagement_sample") or [] if q.get("eng") is not None]
    bench = ENGAGEMENT_BENCH.get(plat)
    if sample:
        avg = sum(q["eng"] for q in sample) / len(sample)
        stats["avg_engagement"] = round(avg, 1)
        if fol and bench:
            rate = avg / fol
            stats["engagement_rate"] = round(rate, 4)
            engagement = round(min(100.0, 70 * rate / bench))
            checks.append(_check("engagement", "engagement", "Engagement per post",
                                 "pass" if rate >= bench else "warn" if rate >= bench / 3 else "fail",
                                 f"Posts average {_fmt(round(avg, 1))} likes, comments and shares ({round(rate * 100, 2)}% of followers); "
                                 f"around {round(bench * 100, 1)}% is typical on {p.label}.", 1))
    if engagement is None and plat in ENGAGEMENT_BENCH:
        checks.append(_check("engagement", "engagement", "Engagement per post", "unknown",
                             "Likes and comments per post aren't available from this source.", 1))

    # --- content mix
    content = None
    fmts = Counter(q.get("format") or "text" for q in (posts or []))
    stats["formats"] = dict(fmts)
    if plat in FAVOURED and fmts:
        fav, need = FAVOURED[plat]
        used = set(fmts) & fav
        top_share = max(fmts.values()) / sum(fmts.values())
        variety = 1.0 if top_share <= 0.8 else 0.5 if top_share <= 0.95 else 0.0
        content = round(30 + 50 * min(1.0, len(used) / need) + 20 * variety)
        missing = sorted(fav - set(fmts))
        detail = "Uses " + ", ".join(FORMAT_LABELS.get(f, f) for f in sorted(fmts)) + "."
        if len(used) < need:
            detail += f" {p.label} favours " + ", ".join(FORMAT_LABELS.get(f, f) for f in sorted(fav)) + "."
        checks.append(_check("content_mix", "content_mix", "Formats the platform favours",
                             "pass" if len(used) >= need else "warn" if used else "fail", detail, 1))
        stats["missing_formats"] = missing

    parts = {"completeness": completeness_score(checks), "activity": activity, "audience": audience,
             "engagement": engagement, "content_mix": content}
    known = {k: v for k, v in parts.items() if v is not None}
    total_w = sum(PROFILE_WEIGHTS[k] for k in known)
    score = round(sum(v * PROFILE_WEIGHTS[k] for k, v in known.items()) / total_w) if total_w else None
    linked_from = (p.key in ctx.website_links) if ctx.website_crawled else None
    links_back = next((c["status"] == "pass" for c in checks if c["id"] == "website_link" and c["status"] != "unknown"), None)
    return {
        "key": p.key, "platform": plat, "label": p.label, "handle": handle, "url": p.url, "kind": p.kind,
        "display_name": name, "avatar_url": data.get("avatar_url") if str(data.get("avatar_url") or "").startswith("http") else None,
        "has_avatar": bool(data.get("avatar_url")), "bio": bio[:600] or None, "website": data.get("website"),
        "followers": fol, "posts_total": data.get("posts_total"), "views": data.get("views"), **stats,
        "source": data.get("source"), "source_label": data.get("source_label"), "fields": data.get("fields") or [],
        "notes": data.get("notes") or [], "sources": list(p.sources),
        "account": {"id": str(p.account_id), "platform": p.account_platform, "status": p.account_status} if p.account_id else None,
        "linked_from_website": linked_from, "links_to_website": links_back,
        "checks": checks, "parts": parts, "score": score, "importance": IMPORTANCE.get(plat, 0.5),
        "confidence": round(total_w / sum(PROFILE_WEIGHTS.values()), 2),
    }


SAMPLE_FALLBACK = 10


def completeness_score(checks: list[dict]) -> int | None:
    known = [c for c in checks if c["group"] == "completeness" and c["status"] != "unknown"]
    if len(known) < 3:  # one or two readable checks (e.g. only the name on a sign-in wall) isn't a fair score
        return None
    w = sum(c["weight"] for c in known)
    got = sum(c["weight"] * (1 if c["status"] == "pass" else 0.5 if c["status"] == "warn" else 0) for c in known)
    return round(100 * got / w)


# ------------------------------------------------------------------ company level
def coverage(profiles: list[dict]) -> tuple[int, list[dict]]:
    have = {p["platform"] for p in profiles if not (p["platform"] == "linkedin" and p.get("kind") == "person")}
    rows = [{"platform": k, "label": lbl, "weight": w, "present": k in have} for k, w, lbl in CORE]
    return sum(r["weight"] for r in rows if r["present"]), rows


def cross_linking(profiles: list[dict]) -> tuple[int | None, str]:
    vals = []
    for p in profiles:
        for k in ("linked_from_website", "links_to_website"):
            if p.get(k) is not None:
                vals.append(1.0 if p[k] else 0.0)
    if not vals:
        return None, "Not enough data: crawl your website (Company intelligence) and add your website address."
    linked = sum(1 for p in profiles if p.get("linked_from_website"))
    back = sum(1 for p in profiles if p.get("links_to_website"))
    return round(100 * sum(vals) / len(vals)), (f"The website links to {linked} of {len(profiles)} profiles; "
                                                 f"{back} profiles link back to the website.")


def brand_consistency(profiles: list[dict], avatar_groups: dict[str, int] | None = None,
                      avatar_main: int | None = None) -> tuple[int | None, str, dict]:
    """Name/handle/logo consistency. avatar_groups maps profile key → cluster number (same number = same picture)."""
    if not profiles:
        return None, "No profiles yet.", {}
    ratios, bits = [], []
    names = [next((c for c in p["checks"] if c["id"] == "name_brand"), None) for p in profiles]
    names = [c for c in names if c]
    if names:
        ok = sum(1 for c in names if c["status"] == "pass")
        ratios.append(ok / len(names))
        bits.append(f"{ok} of {len(names)} profiles use the brand name")
    hs = [h for h in (base_handle(p["platform"], p.get("handle")) for p in profiles) if h]
    dominant = None
    if len(hs) >= 2:
        dominant, n = Counter(hs).most_common(1)[0]
        ratios.append(n / len(hs))
        bits.append(f"{n} of {len(hs)} handles are the same ({dominant})")
    extra: dict = {"dominant_handle": dominant}
    if avatar_groups and (len(avatar_groups) >= 2 or avatar_main is not None):
        groups = Counter(avatar_groups.values())
        main = avatar_main if avatar_main is not None else groups.most_common(1)[0][0]
        n = groups.get(main, 0)
        ratios.append(n / len(avatar_groups))
        bits.append(f"{n} of {len(avatar_groups)} profile pictures " + ("match your logo" if avatar_main is not None else "match"))
        extra["odd_avatars"] = [k for k, g in avatar_groups.items() if g != main]
    if not ratios:
        return None, "Not enough profiles to compare.", extra
    return round(100 * sum(ratios) / len(ratios)), "; ".join(bits) + ".", extra


def grade(score: int | None) -> str:
    if score is None:
        return "Not enough data yet"
    return "Excellent" if score >= 85 else "Strong" if score >= 70 else "Fair" if score >= 50 else "Weak" if score >= 30 else "Needs attention"


def overall(profiles: list[dict], ctx: Context, avatar_groups: dict[str, int] | None = None,
            avatar_main: int | None = None) -> dict:
    parts = []
    for k in ("completeness", "activity", "audience", "engagement", "content_mix"):
        vals = [(p["parts"][k], p["importance"]) for p in profiles if p["parts"].get(k) is not None]
        s = round(sum(v * w for v, w in vals) / sum(w for _, w in vals)) if vals else None
        parts.append({"key": k, "label": PART_LABELS[k], "weight": OVERALL_WEIGHTS[k], "score": s, "how": PART_HOW[k],
                      "detail": f"Average of {len(vals)} profile{'s' if len(vals) != 1 else ''}, weighted by how much each platform matters."
                      if vals else "No profile shares this yet."})
    cl, cl_detail = cross_linking(profiles)
    parts.append({"key": "cross_linking", "label": PART_LABELS["cross_linking"], "weight": OVERALL_WEIGHTS["cross_linking"],
                  "score": cl, "how": PART_HOW["cross_linking"], "detail": cl_detail})
    br, br_detail, brand_extra = brand_consistency(profiles, avatar_groups, avatar_main)
    parts.append({"key": "brand", "label": PART_LABELS["brand"], "weight": OVERALL_WEIGHTS["brand"], "score": br,
                  "how": PART_HOW["brand"], "detail": br_detail})
    cov, cov_rows = coverage(profiles)
    missing = [r["label"] for r in cov_rows if not r["present"]]
    parts.append({"key": "coverage", "label": PART_LABELS["coverage"], "weight": OVERALL_WEIGHTS["coverage"], "score": cov,
                  "how": PART_HOW["coverage"], "detail": ("Missing: " + ", ".join(missing) + ".") if missing else "All key platforms are covered."})
    known = [p for p in parts if p["score"] is not None]
    score = round(sum(p["score"] * p["weight"] for p in known) / sum(p["weight"] for p in known)) if known and profiles else None
    if not profiles:
        score = 0
    return {"score": score, "grade": grade(score), "parts": parts, "coverage": cov_rows, "brand": brand_extra}


# ------------------------------------------------------------------ rule-based suggestions
def _prio(impact: float) -> str:
    return "high" if impact >= 6 else "medium" if impact >= 2.5 else "low"


def _action_connect(p: dict) -> dict:
    return {"to": "/settings/accounts", "label": "Connect the account"}


def _action_schedule(p: dict) -> dict:
    if p.get("account") and p["account"]["status"] == "connected":
        return {"to": "/settings/schedules", "label": "Create a posting schedule"}
    return _action_connect(p)


def suggestions(profiles: list[dict], result: dict, ctx: Context) -> list[dict]:
    out: list[dict] = []
    dom = ctx.domain or "your website"
    multi = {k for k, n in Counter(p["platform"] for p in profiles).items() if n > 1}

    def add(key, title, detail, impact, category, platform=None, profile_key=None, action=None):
        out.append({"key": key[:300], "title": title, "detail": detail, "priority": _prio(impact), "points": round(impact, 2),
                    "category": category, "platform": platform, "profile_key": profile_key, "action": action, "source": "rules"})

    for p in profiles:
        imp = p["importance"]
        lbl = f"{p['label']} ({p['handle']})" if p["platform"] in ("other", "mastodon") or p["platform"] in multi else p["label"]
        checks = {c["id"]: c for c in p["checks"]}
        open_profile = {"href": p["url"], "label": "Open the profile"} if p.get("url") else None

        def failed(cid, warn_ok=False):
            c = checks.get(cid)
            return c is not None and (c["status"] == "fail" or (c["status"] == "warn" and not warn_ok))

        acct = p.get("account")
        if acct and acct["status"] in ("expired", "error"):
            add(f"{p['key']}:reconnect", f"Reconnect {lbl}", "The connection stopped working, so only public data was used.",
                imp * 3, "data", p["platform"], p["key"], {"to": "/settings/accounts", "label": "Reconnect"})
        if failed("website_link"):
            where = "bio" if p["platform"] in ("instagram", "threads", "tiktok", "x", "bluesky") else "profile"
            add(f"{p['key']}:website_link", f"Add {dom} link to {lbl} {where}",
                checks["website_link"]["detail"] + " A link back brings visitors to your website and confirms the profile is yours.",
                imp * 3, "cross_linking", p["platform"], p["key"], open_profile)
        if failed("bio"):
            add(f"{p['key']}:bio", f"Write a bio on {lbl}", f"No useful bio yet. Say what {ctx.org_name} offers in one or two lines "
                f"and end with a link to {dom}.", imp * 2, "completeness", p["platform"], p["key"], open_profile)
        elif failed("bio_offerings"):
            add(f"{p['key']}:bio_offerings", f"Mention what you offer in the {lbl} bio", checks["bio_offerings"]["detail"],
                imp * 2, "completeness", p["platform"], p["key"], open_profile)
        if failed("name_brand"):
            add(f"{p['key']}:name_brand", f"Use “{ctx.org_name}” in the {lbl} name", checks["name_brand"]["detail"] +
                " People search for the brand name; a matching name builds trust.", imp * 2, "brand", p["platform"], p["key"], open_profile)
        if failed("avatar"):
            add(f"{p['key']}:avatar", f"Add your logo as the {lbl} profile picture", "No profile picture was found.",
                imp * 1.5, "completeness", p["platform"], p["key"], open_profile)
        if failed("cover"):
            add(f"{p['key']}:cover", f"Add a cover image on {lbl}", "Use a banner that shows what you do and your website address.",
                imp * 1, "completeness", p["platform"], p["key"], open_profile)
        if failed("contact"):
            add(f"{p['key']}:contact", f"Add contact details on {lbl}", "Add a business email or phone so customers can reach you.",
                imp * 1, "completeness", p["platform"], p["key"], open_profile)
        fq, rc = checks.get("frequency"), checks.get("recency")
        if (fq and fq["status"] in ("fail", "warn")) or (rc and rc["status"] in ("fail", "warn")):
            days = p.get("days_since_last_post")
            tail = f" — last post {days} days ago" if days is not None and days > 14 else (" — no posts yet" if days is None else "")
            n = p.get("posts_30d") or 0
            add(f"{p['key']}:activity", f"Post {CADENCE.get(p['platform'], 'every week')} on {lbl}{tail}",
                f"{n} post{'s' if n != 1 else ''} in the last 30 days. Regular posting keeps the profile visible in feeds and search.",
                imp * (3 if (fq and fq["status"] == "fail") or (rc and rc["status"] == "fail") else 1.5), "activity",
                p["platform"], p["key"], _action_schedule(p))
        elif failed("consistency"):
            add(f"{p['key']}:consistency", f"Post on {lbl} every week", checks["consistency"]["detail"] +
                " A steady rhythm works better than bursts.", imp, "activity", p["platform"], p["key"], _action_schedule(p))
        if failed("engagement"):
            add(f"{p['key']}:engagement", f"Get more comments and shares on {lbl}", checks["engagement"]["detail"] +
                " Ask a question, reply to every comment in the first hour and tag partners when relevant.",
                imp * 1.5, "engagement", p["platform"], p["key"], {"to": "/inbox", "label": "Open the inbox"})
        cm = checks.get("content_mix")
        if cm and cm["status"] in ("fail", "warn") and p.get("missing_formats"):
            fmts = [FORMAT_LABELS.get(f, f) for f in p["missing_formats"] if f != "image"][:3] or \
                [FORMAT_LABELS.get(f, f) for f in p["missing_formats"]][:3]
            add(f"{p['key']}:content_mix", f"Try {' and '.join(fmts)} on {lbl}", cm["detail"], imp * 1.2, "content_mix",
                p["platform"], p["key"], {"to": "/media", "label": "Open Media studio"})
        if checks.get("audience", {}).get("status") == "fail":
            add(f"{p['key']}:audience", f"Grow your {lbl} audience", checks["audience"]["detail"] +
                " Link the profile from your website, email signature and team members' profiles, and post consistently.",
                imp * 0.8, "audience", p["platform"], p["key"], None)
        if p["source"] == "limited" and p["platform"] in ("linkedin", "facebook", "instagram", "threads"):
            add(f"{p['key']}:connect", f"Connect {lbl} for full analysis",
                "Only the public page could be read, so posting, engagement and links are partly unknown.",
                imp * 1.5, "data", p["platform"], p["key"], _action_connect(p))
        elif p["source"] == "limited" and p["platform"] == "youtube":
            add(f"{p['key']}:api_key", "Add a free YouTube Data API key for full analysis",
                "Only the public YouTube page could be read. A YouTube Data API key is free (daily quota) and lets the "
                "check read subscribers, uploads and engagement.", imp * 0.8, "data", p["platform"], p["key"],
                {"to": "/settings/integrations", "label": "Open Integrations"})
        elif p["source"] == "limited" and p["platform"] == "x":
            add(f"{p['key']}:api_key", "Optional: add an X API bearer token for full X analysis",
                "X's API is paid, so only the public page was read and X counts as limited data. Your score isn't "
                "lowered for this; add a token in Settings → Integrations only if you already have one.",
                0.5, "data", p["platform"], p["key"], {"to": "/settings/integrations", "label": "Open Integrations"})
        if "organization" not in p["sources"] and p["platform"] != "other":
            add(f"{p['key']}:org_link", f"Add your {lbl} link in Settings → Organization",
                "This profile was found " + ("through your connected accounts" if "connected" in p["sources"] else "on your website")
                + " but isn't listed in your organization profile.", 1.0, "data", p["platform"], p["key"],
                {"to": "/settings/organization", "label": "Edit social links"})
        if p.get("linked_from_website") is False:
            add(f"{p['key']}:site_link", f"Link your {lbl} profile from {dom}",
                "The website doesn't link to this profile. Add it to the header or footer next to your other profiles.",
                imp * 1.5, "cross_linking", p["platform"], p["key"], None)

    handle = (result.get("brand") or {}).get("dominant_handle") or (ctx.brand[0] if ctx.brand else None)
    for r in result["coverage"]:
        if r["present"]:
            continue
        plat = r["platform"]
        imp = IMPORTANCE.get(plat, 0.5) * r["weight"] / 10
        if plat == "x" and handle:
            title = f"Claim the X handle @{handle}"
        elif plat == "linkedin":
            title = f"Create a LinkedIn company page for {ctx.org_name}"
        else:
            title = f"Create a {r['label']}" + (f" (@{handle})" if handle and plat in ("instagram", "youtube") else "")
        why = {"linkedin": "LinkedIn is the main channel for B2B buyers and hiring in India.",
               "youtube": "Short demos and explainer videos rank in Google and YouTube search.",
               "instagram": "Instagram reaches younger decision makers and job seekers; Reels get wide reach.",
               "x": "X is where tech news and founders talk; even a quiet account protects your name.",
               "facebook": "A Facebook page helps with local search, reviews and WhatsApp-linked audiences.",
               "github": "A GitHub organization shows engineering credibility to technical buyers and hires."}.get(plat, "")
        add(f"coverage:{plat}", title, why + " Add the link in Settings → Organization once it exists.", imp, "coverage", plat, None,
            {"to": "/settings/organization", "label": "Add the link"})
    odd = (result.get("brand") or {}).get("odd_avatars") or []
    if odd:
        names = [p["label"] for p in profiles if p["key"] in odd]
        add("brand:logo", "Use the same logo on every profile", "The profile picture on " + ", ".join(names) +
            " looks different from the others. One logo everywhere makes you easy to recognise.", 3, "brand")
    dh = (result.get("brand") or {}).get("dominant_handle")
    if dh:
        diff = [f"{p['label']} ({p['handle']})" for p in profiles if base_handle(p["platform"], p.get("handle")) not in (dh, "")]
        if diff:
            add("brand:handle", f"Use the same handle (@{dh}) everywhere", "Different handles on " + ", ".join(diff[:5]) + ".",
                2, "brand")
    return sorted(out, key=lambda s: (-s["points"], s["title"]))
