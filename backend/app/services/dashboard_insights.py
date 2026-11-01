"""Dashboard insights: one filtered snapshot of how marketing is doing, for the home dashboard.

Filters (all optional): period (days, or an explicit start/end date), platforms, account, post type, content
pillar. Every number on the dashboard comes from the same filtered slice so they always agree, and each KPI is
compared with the period of the same length just before it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentPillar, Post, PostMetric, SocialAccount
from app.models.analytics_ext import AccountMetric
from app.models.links import LinkClick, TrackedLink
from app.models.listening import ListeningMention
from app.services.analytics import WEEKDAYS, engagements
from app.services.platforms import PLATFORMS, POST_TYPES

HIDDEN = ("google",)
PIPELINE = ("planned", "generating", "draft", "in_review", "approved", "scheduled", "publishing", "published",
            "manual_pending", "failed")


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


def _delta(cur: float, prev: float) -> float | None:
    if not prev:
        return None if not cur else None
    return round((cur - prev) / prev * 100, 1)


def _rate(eng: float, imp: float) -> float:
    return round(eng / imp, 4) if imp else 0.0


def platform_label(p: str) -> str:
    return PLATFORMS[p].label if p in PLATFORMS else p.replace("_", " ").title()


async def insights(session: AsyncSession, tz: str | None, *, days: int = 30, start: date | None = None,
                   end: date | None = None, platforms: list[str] | None = None, account_id: uuid.UUID | None = None,
                   post_type: str | None = None, pillar_id: uuid.UUID | None = None, now: datetime | None = None) -> dict:
    zone = _zone(tz)
    now = now or datetime.now(UTC)
    today = now.astimezone(zone).date()
    if start and end and start <= end:
        d0, d1 = start, min(end, today)
    else:
        d1 = today
        d0 = d1 - timedelta(days=max(1, days) - 1)
    n = (d1 - d0).days + 1
    p0, p1 = d0 - timedelta(days=n), d0 - timedelta(days=1)
    t0 = datetime.combine(d0, time.min, zone).astimezone(UTC)
    t1 = datetime.combine(d1 + timedelta(days=1), time.min, zone).astimezone(UTC)
    pt0 = datetime.combine(p0, time.min, zone).astimezone(UTC)
    day_list = [d0 + timedelta(days=i) for i in range(n)]

    accounts = (await session.scalars(select(SocialAccount).where(
        SocialAccount.platform.notin_(HIDDEN), SocialAccount.status != "removed"))).all()
    pillars = {p.id: p for p in (await session.scalars(select(ContentPillar))).all()}

    def scoped(q):
        if platforms:
            q = q.where(Post.platform.in_(platforms))
        if account_id:
            q = q.where(Post.social_account_id == account_id)
        if post_type:
            q = q.where(Post.post_type == post_type)
        if pillar_id:
            q = q.where(Post.pillar_id == pillar_id)
        return q

    acc_scope = [a for a in accounts if (not platforms or a.platform in platforms) and (not account_id or a.id == account_id)]

    # ---------------------------------------------------------------- published posts + latest metrics
    pub = (await session.scalars(scoped(select(Post).where(
        Post.status == "published", Post.published_at >= pt0, Post.published_at < t1)))).all()
    metrics: dict[uuid.UUID, PostMetric] = {}
    if pub:
        rows = (await session.scalars(
            select(PostMetric).where(PostMetric.post_id.in_([p.id for p in pub]))
            .distinct(PostMetric.post_id).order_by(PostMetric.post_id, PostMetric.fetched_at.desc()))).all()
        metrics = {m.post_id: m for m in rows}

    def blank() -> dict:
        return {"posts": 0, "impressions": 0, "reach": 0, "engagements": 0, "clicks": 0, "likes": 0, "comments": 0,
                "shares": 0, "saves": 0, "video_views": 0}

    def add(b: dict, p: Post) -> None:
        m = metrics.get(p.id)
        b["posts"] += 1
        if m:
            for k in ("impressions", "reach", "clicks", "likes", "comments", "shares", "saves", "video_views"):
                b[k] += int(getattr(m, k) or 0)
            b["engagements"] += engagements(m)

    cur, prev = blank(), blank()
    daily: dict[date, dict] = {d: blank() for d in day_list}
    daily_platform: dict[date, dict[str, int]] = {d: defaultdict(int) for d in day_list}
    by_platform: dict[str, dict] = defaultdict(blank)
    by_type: dict[str, dict] = defaultdict(blank)
    by_pillar: dict[str, dict] = defaultdict(blank)
    heat: dict[tuple[int, int], dict] = defaultdict(lambda: {"posts": 0, "engagements": 0, "impressions": 0})
    # weekday × hour × week cube for the 3D heatmap (only cells with posts are sent)
    cube: dict[tuple[int, int, int], dict] = defaultdict(lambda: {"posts": 0, "engagements": 0, "impressions": 0, "clicks": 0, "platforms": {}})
    top = []
    for p in pub:
        local = p.published_at.astimezone(zone)
        if p.published_at < t0:
            add(prev, p)
            continue
        add(cur, p)
        d = local.date()
        if d in daily:
            add(daily[d], p)
            daily_platform[d][p.platform] += 1
        add(by_platform[p.platform], p)
        add(by_type[p.post_type or "text"], p)
        add(by_pillar[str(p.pillar_id) if p.pillar_id else "none"], p)
        m = metrics.get(p.id)
        wk = (d - d0).days // 7
        cc = cube[(wk, local.weekday(), local.hour)]
        cc["posts"] += 1
        cc["engagements"] += engagements(m)
        cc["impressions"] += int(m.impressions or 0) if m else 0
        cc["clicks"] += int(m.clicks or 0) if m else 0
        cc["platforms"][p.platform] = cc["platforms"].get(p.platform, 0) + 1
        h = heat[(local.weekday(), local.hour)]
        h["posts"] += 1
        h["engagements"] += engagements(m)
        h["impressions"] += int(m.impressions or 0) if m else 0
        eng, imp = engagements(m), int(m.impressions or 0) if m else 0
        top.append({"id": str(p.id), "platform": p.platform, "platform_label": platform_label(p.platform),
                    "post_type": p.post_type, "title": (p.title or p.topic or (p.text or "")[:100] or "Untitled").strip(),
                    "published_at": p.published_at.isoformat(), "external_url": p.external_url,
                    "impressions": imp, "engagements": eng, "clicks": int(m.clicks or 0) if m else 0,
                    "engagement_rate": _rate(eng, imp), "has_metrics": m is not None})
    top.sort(key=lambda r: (r["engagements"], r["impressions"]), reverse=True)

    # ---------------------------------------------------------------- tracked link clicks
    link_q = select(LinkClick.day, TrackedLink.platform, func.sum(LinkClick.count)).join(
        TrackedLink, TrackedLink.id == LinkClick.link_id).where(LinkClick.day >= p0, LinkClick.day <= d1)
    if platforms:
        link_q = link_q.where(TrackedLink.platform.in_(platforms))
    link_rows = (await session.execute(link_q.group_by(LinkClick.day, TrackedLink.platform))).all()
    link_daily: dict[date, int] = defaultdict(int)
    link_cur = link_prev = 0
    for day, _plat, c in link_rows:
        c = int(c or 0)
        if day >= d0:
            link_cur += c
            link_daily[day] += c
        else:
            link_prev += c

    # ---------------------------------------------------------------- followers (account snapshots, carried forward)
    acc_ids = [a.id for a in acc_scope]
    fol_rows = (await session.execute(select(AccountMetric.social_account_id, AccountMetric.platform, AccountMetric.day,
                                             AccountMetric.followers).where(
        AccountMetric.social_account_id.in_(acc_ids), AccountMetric.day <= d1,
        AccountMetric.day >= p0 - timedelta(days=60)).order_by(AccountMetric.day))).all() if acc_ids else []
    per_acc: dict[uuid.UUID, list[tuple[date, int]]] = defaultdict(list)
    acc_platform: dict[uuid.UUID, str] = {}
    for aid, plat, day, f in fol_rows:
        if f is not None:
            per_acc[aid].append((day, int(f)))
            acc_platform[aid] = plat

    def followers_on(aid: uuid.UUID, d: date) -> int | None:
        val = None
        for day, f in per_acc.get(aid, []):
            if day <= d:
                val = f
            else:
                break
        return val

    fol_daily_platform: dict[str, list[int | None]] = {}
    for aid in per_acc:
        plat = acc_platform[aid]
        series = fol_daily_platform.setdefault(plat, [None] * n)
        for i, d in enumerate(day_list):
            v = followers_on(aid, d)
            if v is not None:
                series[i] = (series[i] or 0) + v
    fol_total = [sum(s[i] or 0 for s in fol_daily_platform.values()) if any(s[i] is not None for s in fol_daily_platform.values())
                 else None for i in range(n)]
    fol_now = next((v for v in reversed(fol_total) if v is not None), None)
    fol_start = sum(v for v in (followers_on(a, p1) for a in per_acc) if v is not None) if per_acc else None
    fol_change = (fol_now - fol_start) if fol_now is not None and fol_start else None

    # ---------------------------------------------------------------- pipeline (posts due in the period)
    pipe_rows = (await session.execute(scoped(select(Post.status, func.count()).where(
        Post.scheduled_at >= t0, Post.scheduled_at < t1)).group_by(Post.status))).all()
    pipe = {s: int(c) for s, c in pipe_rows}
    written = sum(c for s, c in pipe.items() if s not in ("planned", "generating", "skipped"))
    approved = sum(pipe.get(s, 0) for s in ("approved", "scheduled", "publishing", "published", "manual_pending"))
    engaged = sum(1 for p in pub if p.published_at >= t0 and engagements(metrics.get(p.id)) > 0)
    funnel = [
        {"key": "planned", "label": "Planned", "value": sum(c for s, c in pipe.items() if s != "skipped"),
         "hint": "Posts due in this period"},
        {"key": "written", "label": "Written", "value": written, "hint": "Text and media are ready"},
        {"key": "approved", "label": "Approved", "value": approved, "hint": "Approved or scheduled to go out"},
        {"key": "published", "label": "Published", "value": cur["posts"], "hint": "Live on the platform"},
        {"key": "engaged", "label": "Got engagement", "value": engaged, "hint": "At least one like, comment, share, save or click"},
    ]
    status_mix = [{"key": s, "label": s.replace("_", " ").capitalize(), "value": pipe.get(s, 0)} for s in PIPELINE if pipe.get(s)]

    # ---------------------------------------------------------------- upcoming 14 days
    up_end = now + timedelta(days=14)
    up_rows = (await session.execute(scoped(select(Post.scheduled_at, Post.platform, Post.status).where(
        Post.status.in_(("planned", "generating", "in_review", "approved", "scheduled")),
        Post.scheduled_at >= now, Post.scheduled_at < up_end)))).all()
    up_days = [today + timedelta(days=i) for i in range(14)]
    upcoming: dict[date, dict[str, int]] = {d: defaultdict(int) for d in up_days}
    for at, plat, _st in up_rows:
        d = at.astimezone(zone).date()
        if d in upcoming:
            upcoming[d][plat] += 1

    # ---------------------------------------------------------------- listening (mentions in the period)
    men_day = func.date(func.timezone(str(zone), ListeningMention.published_at))
    men_rows = (await session.execute(select(men_day, ListeningMention.sentiment, func.count()).where(
        ListeningMention.relevant.is_(True), ListeningMention.published_at >= t0, ListeningMention.published_at < t1)
        .group_by(men_day, ListeningMention.sentiment))).all()
    men_daily: dict[date, dict[str, int]] = {d: defaultdict(int) for d in day_list}
    men_tot: dict[str, int] = defaultdict(int)
    for d, sent, c in men_rows:
        key = sent if sent in ("positive", "neutral", "negative") else "neutral"
        if d in men_daily:
            men_daily[d][key] += int(c)
        men_tot[key] += int(c)

    # ---------------------------------------------------------------- channels
    last_pub = dict((await session.execute(select(Post.social_account_id, func.max(Post.published_at)).where(
        Post.status == "published").group_by(Post.social_account_id))).all())
    posts_per_acc: dict[uuid.UUID, int] = defaultdict(int)
    eng_per_acc: dict[uuid.UUID, int] = defaultdict(int)
    imp_per_acc: dict[uuid.UUID, int] = defaultdict(int)
    for p in pub:
        if p.published_at >= t0 and p.social_account_id:
            posts_per_acc[p.social_account_id] += 1
            m = metrics.get(p.id)
            eng_per_acc[p.social_account_id] += engagements(m)
            imp_per_acc[p.social_account_id] += int(m.impressions or 0) if m else 0
    channels = []
    for a in acc_scope:
        f_now = followers_on(a.id, d1)
        f_then = followers_on(a.id, p1)
        spark = [followers_on(a.id, d) for d in day_list] if a.id in per_acc else []
        channels.append({
            "id": str(a.id), "platform": a.platform, "platform_label": platform_label(a.platform), "name": a.name,
            "status": a.status, "followers": f_now, "followers_change": (f_now - f_then) if f_now is not None and f_then is not None else None,
            "followers_series": spark, "posts": posts_per_acc.get(a.id, 0), "engagements": eng_per_acc.get(a.id, 0),
            "impressions": imp_per_acc.get(a.id, 0),
            "engagement_rate": _rate(eng_per_acc.get(a.id, 0), imp_per_acc.get(a.id, 0)),
            "last_published_at": last_pub.get(a.id).isoformat() if last_pub.get(a.id) else None,
        })
    channels.sort(key=lambda c: (c["posts"], c["followers"] or 0), reverse=True)

    # ---------------------------------------------------------------- assemble
    def kpi(key: str, label: str, c: float, p: float, spark: list, fmt: str = "num", invert: bool = False) -> dict:
        return {"key": key, "label": label, "value": c, "previous": p, "delta": _delta(c, p), "spark": spark,
                "format": fmt, "invert": invert}

    cur_rate, prev_rate = _rate(cur["engagements"], cur["impressions"]), _rate(prev["engagements"], prev["impressions"])
    kpis = [
        kpi("published", "Posts published", cur["posts"], prev["posts"], [daily[d]["posts"] for d in day_list]),
        kpi("impressions", "Impressions", cur["impressions"], prev["impressions"], [daily[d]["impressions"] for d in day_list]),
        kpi("engagements", "Engagements", cur["engagements"], prev["engagements"], [daily[d]["engagements"] for d in day_list]),
        kpi("engagement_rate", "Engagement rate", cur_rate, prev_rate,
            [_rate(daily[d]["engagements"], daily[d]["impressions"]) for d in day_list], "pct"),
        kpi("clicks", "Link clicks", link_cur + cur["clicks"], link_prev + prev["clicks"],
            [link_daily.get(d, 0) + daily[d]["clicks"] for d in day_list]),
        kpi("followers", "Followers", fol_now or 0, fol_start or 0, [v or 0 for v in fol_total]),
    ]

    def rows(d: dict, label) -> list[dict]:
        out = [{"key": k, "label": label(k), **v, "engagement_rate": _rate(v["engagements"], v["impressions"])} for k, v in d.items()]
        return sorted(out, key=lambda r: (r["posts"], r["engagements"]), reverse=True)

    def pillar_label(k: str) -> str:
        try:
            p = pillars.get(uuid.UUID(k))
        except ValueError:
            p = None
        return p.name if p else "No topic pillar"

    heatmap = [{"weekday": wd, "day": WEEKDAYS[wd], "hour": hr, "posts": v["posts"], "engagements": v["engagements"],
                "engagement_rate": _rate(v["engagements"], v["impressions"])} for (wd, hr), v in sorted(heat.items())]

    return {
        "period": {"start": d0.isoformat(), "end": d1.isoformat(), "days": n,
                   "previous_start": p0.isoformat(), "previous_end": p1.isoformat(), "timezone": str(zone)},
        "kpis": kpis,
        "daily": [{"date": d.isoformat(), **{k: daily[d][k] for k in ("posts", "impressions", "reach", "engagements",
                                                                       "likes", "comments", "shares", "saves", "video_views")},
                   "clicks": daily[d]["clicks"] + link_daily.get(d, 0),
                   "engagement_rate": _rate(daily[d]["engagements"], daily[d]["impressions"]),
                   "by_platform": dict(daily_platform[d])} for d in day_list],
        "followers": {"total": fol_total, "by_platform": [{"platform": k, "label": platform_label(k), "values": v}
                                                          for k, v in fol_daily_platform.items()],
                      "now": fol_now, "change": fol_change},
        "by_platform": rows(by_platform, platform_label),
        "by_post_type": rows(by_type, lambda k: POST_TYPES.get(k, k)),
        "by_pillar": [{**r, "color": (pillars[uuid.UUID(r["key"])].color if r["key"] != "none" and
                                      uuid.UUID(r["key"]) in pillars else None)} for r in rows(by_pillar, pillar_label)],
        "engagement_mix": [{"key": k, "label": k.replace("_", " ").capitalize(), "value": cur[k]}
                           for k in ("likes", "comments", "shares", "saves", "clicks")],
        "funnel": funnel,
        "status_mix": status_mix,
        "heatmap": heatmap,
        "top_posts": top[:40],
        "heat_cube": {
            "weeks": [(d0 + timedelta(days=7 * k)).isoformat() for k in range((n + 6) // 7)],
            "cells": [{"w": wk, "d": wd, "h": hr, **v, "engagement_rate": _rate(v["engagements"], v["impressions"])}
                      for (wk, wd, hr), v in sorted(cube.items())],
        },
        "upcoming": [{"date": d.isoformat(), "total": sum(upcoming[d].values()), "by_platform": dict(upcoming[d])} for d in up_days],
        "mentions": {"daily": [{"date": d.isoformat(), **{s: men_daily[d].get(s, 0) for s in ("positive", "neutral", "negative")}}
                               for d in day_list],
                     "totals": {s: men_tot.get(s, 0) for s in ("positive", "neutral", "negative")}},
        "channels": channels,
        "filters": {
            "platforms": sorted({a.platform for a in accounts}, key=lambda p: platform_label(p)),
            "platform_labels": {a.platform: platform_label(a.platform) for a in accounts},
            "accounts": [{"id": str(a.id), "name": a.name, "platform": a.platform, "platform_label": platform_label(a.platform)}
                         for a in accounts],
            "post_types": [{"key": k, "label": v} for k, v in POST_TYPES.items()],
            "pillars": [{"id": str(p.id), "name": p.name, "color": p.color} for p in pillars.values() if p.active],
        },
    }
