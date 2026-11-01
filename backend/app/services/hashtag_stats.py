"""Hashtag analytics: how posts using each hashtag did compared with the usual results of the same account.

Every number comes from published posts and their latest metrics. Each post is compared with its own account's
average for the period (a big account doesn't make its hashtags look better), and suggestions are rules on those
numbers only:
- keep: used on at least MIN_POSTS posts with results, engagement rate at least 10% above the account average
- drop: used on at least MIN_POSTS posts with results, engagement rate at least 20% below the account average
- try:  promising on one or two posts (20%+ above average), or in the brand kit but not used in the period
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BrandProfile, Post, PostMetric, SocialAccount
from app.services import content as svc
from app.services.platforms import PLATFORMS

MIN_POSTS = 3
KEEP_LIFT = 0.10
DROP_LIFT = -0.20
TRY_LIFT = 0.20


def _eng(m: PostMetric) -> int:
    return int((m.likes or 0) + (m.comments or 0) + (m.shares or 0) + (m.saves or 0) + (m.clicks or 0))


def _rate(e: float, i: float) -> float | None:
    return round(e / i, 6) if i else None


def period(start: date | None, end: date | None, tz, default_days: int = 90) -> tuple[date, date]:
    today = datetime.now(tz).date()
    end = end or today
    start = start or end - timedelta(days=default_days - 1)
    if start > end:
        start, end = end, start
    if (end - start).days > 730:
        start = end - timedelta(days=730)
    return start, end


async def hashtag_stats(session: AsyncSession, *, start: date, end: date, tz, platform: str | None = None) -> dict[str, Any]:
    """start/end are local dates (end included)."""
    s_utc = datetime.combine(start, time.min).replace(tzinfo=tz).astimezone(UTC)
    e_utc = datetime.combine(end + timedelta(days=1), time.min).replace(tzinfo=tz).astimezone(UTC)
    conds = [Post.status == "published", Post.published_at.is_not(None), Post.published_at >= s_utc, Post.published_at < e_utc]
    if platform:
        conds.append(Post.platform == platform)
    posts = list((await session.scalars(select(Post).where(*conds).order_by(Post.published_at))).all())
    metrics: dict[uuid.UUID, PostMetric] = {}
    if posts:
        rows = (await session.scalars(
            select(PostMetric).where(PostMetric.post_id.in_([p.id for p in posts]))
            .distinct(PostMetric.post_id).order_by(PostMetric.post_id, PostMetric.fetched_at.desc())
        )).all()
        metrics = {m.post_id: m for m in rows}
    accounts = {a.id: a for a in (await session.scalars(select(SocialAccount))).all()}

    # account baselines (posts with impressions only)
    base: dict[Any, dict] = defaultdict(lambda: {"posts": 0, "impressions": 0, "engagements": 0})
    for p in posts:
        m = metrics.get(p.id)
        if m and m.impressions:
            b = base[p.social_account_id]
            b["posts"] += 1
            b["impressions"] += int(m.impressions)
            b["engagements"] += _eng(m)
    for b in base.values():
        b["avg_impressions"] = b["impressions"] / b["posts"]
        b["avg_engagements"] = b["engagements"] / b["posts"]
        b["engagement_rate"] = _rate(b["engagements"], b["impressions"])

    midpoint = s_utc + (e_utc - s_utc) / 2
    tags: dict[str, dict] = {}
    for p in posts:
        m = metrics.get(p.id)
        b = base.get(p.social_account_id)
        seen_here = set()
        for raw in p.hashtags or []:
            t = svc._clean_tag(raw)
            if not t or t.lower() in seen_here:
                continue
            seen_here.add(t.lower())
            d = tags.setdefault(t.lower(), {
                "tag": t, "posts": 0, "posts_with_metrics": 0, "impressions": 0, "engagements": 0, "clicks": 0,
                "rate_lifts": [], "imp_lifts": [], "first": {"e": 0, "i": 0, "n": 0}, "second": {"e": 0, "i": 0, "n": 0},
                "platforms": set(), "accounts": set(), "last_used": None,
            })
            d["posts"] += 1
            d["platforms"].add(p.platform)
            if p.social_account_id:
                d["accounts"].add(p.social_account_id)
            d["last_used"] = p.published_at
            if not (m and m.impressions):
                continue
            e, i = _eng(m), int(m.impressions)
            d["posts_with_metrics"] += 1
            d["impressions"] += i
            d["engagements"] += e
            d["clicks"] += int(m.clicks or 0)
            half = d["first"] if p.published_at < midpoint else d["second"]
            half["e"] += e
            half["i"] += i
            half["n"] += 1
            if b and b["engagement_rate"]:
                d["rate_lifts"].append((e / i) / b["engagement_rate"] - 1)
            if b and b["avg_impressions"]:
                d["imp_lifts"].append(i / b["avg_impressions"] - 1)

    out_tags = []
    for d in tags.values():
        n = d["posts_with_metrics"]
        lift = sum(d["rate_lifts"]) / len(d["rate_lifts"]) if d["rate_lifts"] else None
        imp_lift = sum(d["imp_lifts"]) / len(d["imp_lifts"]) if d["imp_lifts"] else None
        f, s = d["first"], d["second"]
        trend = None
        if f["n"] and s["n"] and f["i"] and s["i"]:
            r1, r2 = f["e"] / f["i"], s["e"] / s["i"]
            trend = "flat" if r1 == 0 and r2 == 0 else "up" if r2 > r1 * 1.1 else "down" if r2 < r1 * 0.9 else "flat"
        suggestion, reason = suggest(n, lift)
        out_tags.append({
            "tag": d["tag"], "posts": d["posts"], "posts_with_metrics": n,
            "avg_impressions": round(d["impressions"] / n, 1) if n else None,
            "avg_engagements": round(d["engagements"] / n, 1) if n else None,
            "engagement_rate": _rate(d["engagements"], d["impressions"]),
            "clicks": d["clicks"],
            "lift": round(lift, 4) if lift is not None else None,
            "impressions_lift": round(imp_lift, 4) if imp_lift is not None else None,
            "trend": trend,
            "trend_detail": ({"first_half_rate": _rate(f["e"], f["i"]), "second_half_rate": _rate(s["e"], s["i"]),
                              "first_half_posts": f["n"], "second_half_posts": s["n"]} if trend else None),
            "platforms": sorted(d["platforms"]),
            "accounts": [accounts[a].name for a in d["accounts"] if a in accounts],
            "last_used": d["last_used"].isoformat() if d["last_used"] else None,
            "suggestion": suggestion, "reason": reason,
        })
    out_tags.sort(key=lambda r: (r["posts_with_metrics"], r["posts"], r["lift"] or 0), reverse=True)

    ranked = [r for r in out_tags if r["posts_with_metrics"] >= MIN_POSTS and r["lift"] is not None]
    top = sorted([r for r in ranked if r["lift"] > 0], key=lambda r: r["lift"], reverse=True)[:10]
    bottom = sorted([r for r in ranked if r["lift"] < 0], key=lambda r: r["lift"])[:10]

    # brand-kit hashtags not used in the period → "try"
    brand = await session.scalar(select(BrandProfile))
    used = set(tags)
    unused_brand = []
    for raw in (brand.hashtags if brand else None) or []:
        t = svc._clean_tag(raw)
        if t and t.lower() not in used and t.lower() not in {u["tag"].lower() for u in unused_brand}:
            unused_brand.append({"tag": t, "suggestion": "try", "reason": "In your brand kit but not used in this period."})

    suggestions = {
        "keep": [r for r in out_tags if r["suggestion"] == "keep"],
        "drop": [r for r in out_tags if r["suggestion"] == "drop"],
        "try": [r for r in out_tags if r["suggestion"] == "try"] + unused_brand,
    }
    with_metrics = sum(1 for p in posts if (m := metrics.get(p.id)) and m.impressions)
    tagged = sum(1 for p in posts if p.hashtags)
    note = None
    if not posts:
        note = "No published posts in this period."
    elif not with_metrics:
        note = "Published posts don't have results from the platforms yet, so hashtags can't be compared."
    elif not ranked:
        note = f"Suggestions to keep or drop a hashtag need it on at least {MIN_POSTS} posts with results."
    return {
        "start": start.isoformat(), "end": end.isoformat(), "platform": platform,
        "platform_label": PLATFORMS[platform].label if platform in PLATFORMS else None,
        "totals": {"posts": len(posts), "posts_with_metrics": with_metrics, "posts_with_hashtags": tagged,
                   "hashtags": len(out_tags)},
        "accounts": [{"id": str(aid), "name": accounts[aid].name if aid in accounts else "No account",
                      "platform": accounts[aid].platform if aid in accounts else None, "posts": b["posts"],
                      "avg_impressions": round(b["avg_impressions"], 1), "avg_engagements": round(b["avg_engagements"], 1),
                      "engagement_rate": b["engagement_rate"]} for aid, b in base.items()],
        "hashtags": out_tags, "top": top, "bottom": bottom, "suggestions": suggestions,
        "min_posts": MIN_POSTS, "note": note,
        "method": ("Each post is compared with the average engagement rate of its own account in this period; a hashtag's "
                   "difference is the average of its posts' differences. Engagements = likes + comments + shares + saves + clicks."),
    }


def suggest(n: int, lift: float | None) -> tuple[str, str]:
    if lift is None or n == 0:
        return "wait", "No results yet."
    pct = f"{abs(lift) * 100:.0f}%"
    if n >= MIN_POSTS:
        if lift >= KEEP_LIFT:
            return "keep", f"Posts with it got {pct} more engagement than usual for the account ({n} posts)."
        if lift <= DROP_LIFT:
            return "drop", f"Posts with it got {pct} less engagement than usual for the account ({n} posts)."
        return "neutral", f"About the same as usual for the account ({n} posts)."
    if lift >= TRY_LIFT:
        return "try", f"Promising: {pct} above usual, but only on {n} post{'s' if n != 1 else ''}. Use it more to find out."
    return "wait", f"Only {n} post{'s' if n != 1 else ''} with results so far."
