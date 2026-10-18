"""Marketing health score (0–100, transparent sub-scores) and the AI growth advisor.

Score: each part is 0–100 with a one-line "how it's computed" and the current value. Parts without enough data
are left out and the remaining weights are re-balanced, so a new account isn't punished for missing history.

Advisor: the numbers behind the score, growth, best times, formats and competitor benchmarks go to the text
model (task "plan") which returns ranked, specific tips. Each tip may carry a one-click action that the API
validates and applies through the existing schedule / post / idea logic. When no AI model is available,
built-in rules produce grounded tips instead and the response says so.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnalyticsSnapshot, ContentPillar, Organization, Post, PresenceAudit, Recommendation, Schedule, SocialAccount,
)
from app.services import analytics as base
from app.services import analytics_competitors as comp
from app.services import analytics_growth as growth
from app.services.platforms import PLATFORMS, POST_TYPES, supported_types

log = logging.getLogger(__name__)

WEIGHTS = {"consistency": 20, "engagement": 20, "growth": 15, "reach": 10, "content_mix": 10, "competitors": 15, "website": 10}
LABELS = {"consistency": "Posting consistency", "engagement": "Engagement rate", "growth": "Audience growth",
          "reach": "Reach", "content_mix": "Content mix", "competitors": "Competitor position", "website": "Website health"}
TARGET_ER = 0.035  # 3.5% engagement rate scores 100
TARGET_REACH = 0.30  # average impressions per post = 30% of followers scores 100
DEFAULT_WEEKLY = 3  # posts a week per account when there are no schedules
VISUAL = {"image", "carousel", "video", "gif", "animation", "document"}
MIN_POSTS_ER = 3
WINDOW = 28

ACTION_TYPES = {"add_schedule_times", "set_post_mix", "create_schedule", "draft_posts", "add_topic_ideas"}
ACTION_PERMS = {"add_schedule_times": {"schedule.manage"}, "set_post_mix": {"schedule.manage"},
                "create_schedule": {"schedule.manage"}, "draft_posts": {"content.create"},
                "add_topic_ideas": {"intel.manage"}}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def clamp(v: float) -> int:
    return int(round(max(0.0, min(100.0, v))))


def grade(score: int | None) -> str:
    if score is None:
        return "Not enough data yet"
    return "Strong" if score >= 80 else "Good" if score >= 60 else "Needs work" if score >= 40 else "Getting started"


def _part(key: str, score: float | None, value: str, how: str, detail: str | None = None) -> dict:
    return {"key": key, "label": LABELS[key], "weight": WEIGHTS[key], "score": None if score is None else clamp(score),
            "value": value, "how": how, "detail": detail}


def combine(parts: list[dict]) -> int | None:
    scored = [p for p in parts if p["score"] is not None]
    w = sum(p["weight"] for p in scored)
    return round(sum(p["score"] * p["weight"] for p in scored) / w) if w else None


# ------------------------------------------------------------------ inputs
async def expected_posts(session: AsyncSession, org: Organization, start: datetime, end: datetime) -> tuple[int, str]:
    from app.services import scheduler

    zone = growth.org_zone(org)
    scheds = (await session.scalars(select(Schedule).where(Schedule.active.is_(True)))).all()
    if scheds:
        blackout = await scheduler.blackout_dates(session)
        n = 0
        for s in scheds:
            since = max(start, s.created_at) if s.created_at else start
            n += len(scheduler.slot_times(s, zone, since, end, blackout))
        return n, "your active posting schedules"
    accounts = [a for a in await growth.tracked_accounts(session) if a.status in ("connected", "manual")]
    return len(accounts) * DEFAULT_WEEKLY * WINDOW // 7, f"{DEFAULT_WEEKLY} posts a week per connected account (no schedules yet)"


async def compute(session: AsyncSession, org: Organization) -> dict:
    now = datetime.now(UTC)
    today = growth.local_today(org)
    start = now - timedelta(days=WINDOW)
    rows = await growth.post_rows(session, start, now)
    parts: list[dict] = []

    # consistency
    expected, basis = await expected_posts(session, org, start, now)
    published = len(rows)
    missed = (await session.scalars(select(Post.id).where(Post.status == "failed", Post.updated_at >= start))).all()
    if expected:
        parts.append(_part("consistency", min(1.0, published / expected) * 100, f"{published} of {expected} posts" if published <= expected else f"{published} posts ({expected} planned)",
                           f"Posts published in the last {WINDOW} days ÷ posts expected from {basis}.",
                           f"{len(missed)} post(s) failed to publish." if missed else None))
    else:
        parts.append(_part("consistency", None, "No accounts yet", "Connect an account or set up a posting schedule."))

    # engagement rate
    t = growth.window_totals(rows)
    if t["with_metrics"] >= MIN_POSTS_ER and t["impressions"]:
        er = t["engagement_rate"] or 0
        parts.append(_part("engagement", er / TARGET_ER * 100, f"{er * 100:.1f}%",
                           f"Engagements ÷ impressions over the last {WINDOW} days; {TARGET_ER * 100:.1f}% or more scores 100."))
    else:
        parts.append(_part("engagement", None, "Not enough data",
                           f"Needs at least {MIN_POSTS_ER} published posts with metrics in the last {WINDOW} days."))

    # audience growth
    series = await growth.account_series(session, today - timedelta(days=60))
    f_now, _ = growth.followers_at(series, today)
    f_then, _ = growth.followers_at(series, today - timedelta(days=30))
    rate = growth.growth_rate(f_now, f_then)
    if rate is not None:
        parts.append(_part("growth", 40 + rate * 12, f"{rate:+.1f}% in 30 days",
                           "Follower change over 30 days: no change scores 40, each +1% adds 12 points (5% or more ≈ 100)."))
    else:
        parts.append(_part("growth", None, "Collecting history",
                           "Needs follower counts from 30 days ago; the app saves them daily."))

    # reach
    with_m = [m for _, m in rows if m]
    if f_now and with_m and t["impressions"]:
        ratio = (t["impressions"] / len(with_m)) / f_now
        parts.append(_part("reach", ratio / TARGET_REACH * 100, f"{ratio * 100:.0f}% of followers per post",
                           f"Average impressions per post ÷ total followers; {TARGET_REACH * 100:.0f}% or more scores 100."))
    else:
        parts.append(_part("reach", None, "Not enough data", "Needs follower counts and post impressions."))

    # content mix
    if rows:
        types = {p.post_type or "text" for p, _ in rows}
        visual = sum(1 for p, _ in rows if (p.post_type or "text") in VISUAL) / len(rows)
        parts.append(_part("content_mix", min(len(types) / 3, 1) * 60 + min(visual / 0.6, 1) * 40,
                           f"{len(types)} format(s), {visual * 100:.0f}% visual",
                           "Up to 60 points for using 3+ formats, up to 40 for 60%+ posts with images, video or slides."))
    else:
        parts.append(_part("content_mix", None, "No posts yet", "Based on the formats of posts published recently."))

    # competitor position
    bench = await comp.benchmark(session, org)
    allrows = bench["channels"]["all"]["rows"]
    us = next((r for r in allrows if r["is_us"]), None)
    others = [r for r in allrows if not r["is_us"]]
    pos = []
    for key, *_ in comp.METRICS:
        vals = [r[key] for r in others if r.get(key) is not None]
        if us and us.get(key) is not None and vals:
            pos.append(sum(1 for v in vals if us[key] >= v) / len(vals))
    if pos:
        parts.append(_part("competitors", sum(pos) / len(pos) * 100, f"Ahead on {sum(pos) / len(pos) * 100:.0f}% of comparisons",
                           "Share of competitor comparisons (audience, posting, engagement, growth) where you match or beat them."))
    else:
        parts.append(_part("competitors", None, "No competitor data",
                           "Add competitors' social handles on the Competitors tab and refresh."))

    # website
    snap = await base.latest(session, "pagespeed")
    scores = (((snap.data or {}).get("mobile") or {}).get("scores") or {}) if snap else {}
    vals = [scores[k] for k in ("performance", "seo") if scores.get(k) is not None]
    if vals:
        parts.append(_part("website", sum(vals) / len(vals), f"Speed {scores.get('performance', '—')}, SEO {scores.get('seo', '—')}",
                           "Average of the PageSpeed mobile performance and SEO scores."))
    else:
        audit = await session.scalar(select(PresenceAudit).order_by(PresenceAudit.created_at.desc()))
        if audit:
            parts.append(_part("website", audit.score, f"Presence audit {audit.score}/100",
                               "The latest digital presence audit score (Company intelligence)."))
        else:
            parts.append(_part("website", None, "No website data", "Refresh analytics or run a presence audit."))

    score = combine(parts)
    return {"score": score, "grade": grade(score), "parts": parts, "computed_at": now.isoformat(),
            "day": today.isoformat(), "window_days": WINDOW}


async def save_snapshot(session: AsyncSession, org_id: uuid.UUID, result: dict) -> None:
    day = result["day"]
    await session.execute(delete(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "health",
                                                          AnalyticsSnapshot.period_start == day))
    session.add(AnalyticsSnapshot(organization_id=org_id, source="health", key="score", period_start=day, period_end=day,
                                  data=result))
    await session.flush()


async def history(session: AsyncSession, days: int = 90) -> list[dict]:
    since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
    rows = (await session.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "health",
                                                                  AnalyticsSnapshot.period_start >= since)
                                  .order_by(AnalyticsSnapshot.period_start))).all()
    return [{"day": r.period_start, "score": (r.data or {}).get("score")} for r in rows]


async def health(session: AsyncSession, org: Organization) -> dict:
    cur = await compute(session, org)
    hist = await history(session)
    prev = next((h["score"] for h in reversed(hist)
                 if h["day"] <= (date.fromisoformat(cur["day"]) - timedelta(days=7)).isoformat() and h["score"] is not None), None)
    return {**cur, "history": hist, "change_7d": (cur["score"] - prev) if cur["score"] is not None and prev is not None else None}


# ------------------------------------------------------------------ advisor
SYSTEM = """You are a senior B2B social media strategist advising a marketing person (not technical).
Give specific, practical tips grounded ONLY in the numbers provided. Quote the evidence (numbers) in "why".
Never invent numbers, competitors, clients or features. Plain, friendly English, sentence case.
Return ONLY JSON: {"recommendations": [{"title": "imperative, max 80 chars", "why": "evidence from the data, 1-2 sentences",
"action": "the concrete next step, 1-2 sentences", "impact": "high|medium|low", "effort": "low|medium|high",
"category": "consistency|engagement|growth|reach|content_mix|competitors|website|timing",
"one_click": null or {"type": "...", "params": {...}}}]}
one_click types you may use (only with ids from the data):
- add_schedule_times: {"schedule_id": "...", "times": ["HH:MM"]}  add posting times to a schedule
- set_post_mix: {"schedule_id": "...", "post_types": {"image": 2, "carousel": 1}}  only types the schedule's platform supports
- create_schedule: {"social_account_id": "...", "days_of_week": [0-6, Mon=0], "times": ["HH:MM"], "post_types": {"image": 1}}
- draft_posts: {"topic": "specific topic", "account_ids": ["..."], "post_type": "optional supported type"}
- add_topic_ideas: {"ideas": [{"title": "...", "angle": "..."}]}
Rank by expected impact. 5 to 8 recommendations."""


async def facts(session: AsyncSession, org: Organization, score: dict | None = None) -> dict:
    score = score or await compute(session, org)
    g = await growth.growth(session, org, 30)
    bench = await comp.benchmark(session, org)
    zone = growth.org_zone(org)
    accounts = await growth.tracked_accounts(session)
    scheds = (await session.scalars(select(Schedule).order_by(Schedule.created_at))).all()
    acc_by = {a.id: a for a in accounts}
    pillars = (await session.scalars(select(ContentPillar).where(ContentPillar.active.is_(True)))).all()
    gsc = await base.latest(session, "gsc")
    return {
        "organization": org.name, "timezone": str(zone),
        "health": {"score": score["score"], "parts": [{k: p[k] for k in ("key", "label", "score", "value", "how")}
                                                      for p in score["parts"]]},
        "last_30_days": g["windows"]["30"], "last_7_days": g["windows"]["7"],
        "followers_total": g["totals"]["followers"],
        "accounts": [{"id": a["id"], "name": a["name"], "platform": a["platform"], "followers": (a["latest"] or {}).get("followers"),
                      "growth_30d": a["deltas"]["30"], "supported_post_types": list(supported_types(a["platform"])),
                      "has_schedule": any(str(s.social_account_id) == a["id"] for s in scheds)} for a in g["accounts"]],
        "schedules": [{"id": str(s.id), "name": s.name, "active": s.active,
                       "account": acc_by[s.social_account_id].name if s.social_account_id in acc_by else None,
                       "platform": acc_by[s.social_account_id].platform if s.social_account_id in acc_by else None,
                       "days_of_week": s.days_of_week, "times": s.times, "post_types": s.post_types} for s in scheds],
        "best_times": g["best_times"], "best_times_note": g["best_times_message"],
        "formats": [{"type": r["key"], "posts": r["posts"], "engagement_rate": r["engagement_rate"]} for r in g["by_post_type"]],
        "top_posts": [{"title": p["title"][:120], "platform": p["platform"], "type": p["post_type"],
                       "engagement_rate": p["engagement_rate"]} for p in g["top_posts"][:5]],
        "competitors": {"insights": bench["channels"]["all"]["insights"],
                        "rows": [{k: r.get(k) for k in ("name", "is_us", "followers", "posts_30d", "avg_engagement", "net_30d")}
                                 for r in bench["channels"]["all"]["rows"]]},
        "pillars": [p.name for p in pillars],
        "search_opportunities": [{"query": q.get("query"), "impressions": q.get("impressions"), "position": q.get("position")}
                                 for q in ((gsc.data or {}).get("opportunities") or [])[:5]] if gsc else [],
    }


def _times(v) -> list[str]:
    out = []
    for t in v or []:
        t = str(t).strip()
        if re.match(r"^\d:\d\d$", t):
            t = "0" + t
        if TIME_RE.match(t):
            out.append(t)
    return sorted(set(out))[:6]


async def validate_action(session: AsyncSession, raw) -> dict | None:
    """Keep a one-click action only if it is well-formed and refers to things that exist."""
    if not isinstance(raw, dict) or raw.get("type") not in ACTION_TYPES:
        return None
    t, p = raw["type"], raw.get("params") if isinstance(raw.get("params"), dict) else {}

    async def schedule(sid):
        try:
            return await session.get(Schedule, uuid.UUID(str(sid)))
        except (ValueError, TypeError):
            return None

    async def account(aid):
        try:
            a = await session.get(SocialAccount, uuid.UUID(str(aid)))
        except (ValueError, TypeError):
            return None
        return a if a is not None and a.platform in PLATFORMS and a.status != "removed" else None

    if t == "add_schedule_times":
        s, times = await schedule(p.get("schedule_id")), _times(p.get("times"))
        new = [x for x in times if s is not None and x not in (s.times or [])]
        if s is None or not new:
            return None
        return {"type": t, "params": {"schedule_id": str(s.id), "times": new},
                "label": f"Add {', '.join(new)} to “{s.name}”"}
    if t == "set_post_mix":
        s = await schedule(p.get("schedule_id"))
        acc = await session.get(SocialAccount, s.social_account_id) if s else None
        mix = {k: float(v) for k, v in (p.get("post_types") or {}).items()
               if acc and k in supported_types(acc.platform) and isinstance(v, (int, float)) and v > 0}
        if not s or not mix:
            return None
        return {"type": t, "params": {"schedule_id": str(s.id), "post_types": mix},
                "label": f"Change the format mix of “{s.name}”"}
    if t == "create_schedule":
        acc = await account(p.get("social_account_id"))
        days = sorted({int(d) for d in p.get("days_of_week") or [] if isinstance(d, int) and 0 <= d <= 6})
        times = _times(p.get("times"))
        mix = {k: float(v) for k, v in (p.get("post_types") or {}).items()
               if acc and k in supported_types(acc.platform) and isinstance(v, (int, float)) and v > 0}
        if acc and not mix:
            first = supported_types(acc.platform)[0]
            mix = {"image": 1.0} if "image" in supported_types(acc.platform) else {first: 1.0}
        if not acc or not days or not times:
            return None
        return {"type": t, "params": {"social_account_id": str(acc.id), "days_of_week": days, "times": times, "post_types": mix},
                "label": f"Create a schedule for {acc.name}"}
    if t == "draft_posts":
        topic = str(p.get("topic") or "").strip()[:300]
        accs = [a for a in [await account(x) for x in (p.get("account_ids") or [])[:3]] if a]
        ptype = p.get("post_type") if p.get("post_type") in POST_TYPES else None
        if ptype:
            accs = [a for a in accs if ptype in supported_types(a.platform)]
        if len(topic) < 5 or not accs:
            return None
        return {"type": t, "params": {"topic": topic, "account_ids": [str(a.id) for a in accs], "post_type": ptype},
                "label": f"Draft {'a post' if len(accs) == 1 else f'{len(accs)} posts'} on this"}
    if t == "add_topic_ideas":
        ideas = [{"title": str(i.get("title")).strip()[:200], "angle": str(i.get("angle") or "").strip()[:300] or None}
                 for i in (p.get("ideas") or []) if isinstance(i, dict) and str(i.get("title") or "").strip()][:5]
        if not ideas:
            return None
        return {"type": t, "params": {"ideas": ideas}, "label": f"Add {len(ideas)} topic idea{'s' if len(ideas) > 1 else ''}"}
    return None


def _choice(v, allowed: tuple[str, ...], default: str) -> str:
    v = str(v or "").lower().strip()
    return v if v in allowed else default


def rule_based(f: dict) -> list[dict]:
    """Deterministic, number-grounded tips used when no AI model is available."""
    recs: list[dict] = []
    parts = {p["key"]: p for p in f["health"]["parts"]}
    scheds = [s for s in f["schedules"] if s["active"]]
    best = f.get("best_times") or []

    def add(title, why, action, impact, effort, category, one_click=None):
        recs.append({"title": title, "why": why, "action": action, "impact": impact, "effort": effort,
                     "category": category, "one_click": one_click})

    no_sched = [a for a in f["accounts"] if not a["has_schedule"] and a["platform"] not in ("x", "manual")]
    if no_sched:
        a = no_sched[0]
        mix = {"image": 2, "text": 1} if "image" in a["supported_post_types"] and "text" in a["supported_post_types"] else {a["supported_post_types"][0]: 1}
        add(f"Set up a posting schedule for {a['name']}",
            f"{a['name']} has no posting schedule, so it only gets posts you write by hand.",
            "Start with three posts a week (Tuesday, Wednesday, Thursday at 10:00) and review drafts before they go out.",
            "high", "low", "consistency",
            {"type": "create_schedule", "params": {"social_account_id": a["id"], "days_of_week": [1, 2, 3], "times": ["10:00"],
                                                   "post_types": mix}})
    c = parts.get("consistency")
    if c and c["score"] is not None and c["score"] < 70:
        add("Publish the posts your schedule plans", f"Only {c['value']} went out in the last {WINDOW} days.",
            "Check the approval queue daily, or switch reliable schedules to publish automatically after a review window.",
            "high", "low", "consistency")
    if best and scheds:
        slot = best[0]
        hh = f"{int(slot['hour']):02d}:00"
        s = next((s for s in scheds if hh not in (s["times"] or [])), None)
        if s:
            add(f"Post at {hh} on {slot['day']}s",
                f"Posts at {slot['day']} {hh} average a {slot['avg_engagement_rate'] * 100:.1f}% engagement rate across {slot['posts']} posts, your best slot.",
                f"Add {hh} to the “{s['name']}” schedule.", "medium", "low", "timing",
                {"type": "add_schedule_times", "params": {"schedule_id": s["id"], "times": [hh]}})
    m = parts.get("content_mix")
    if m and m["score"] is not None and m["score"] < 70:
        s = next((s for s in scheds if s["platform"] and {"image", "carousel"} <= set(supported_types(s["platform"]))), None)
        add("Use more visual formats", f"Your recent mix is {m['value']}. Images, carousels and short videos usually earn more engagement.",
            "Make at least half of your posts images or carousels.", "medium", "low", "content_mix",
            {"type": "set_post_mix", "params": {"schedule_id": s["id"], "post_types": {"image": 2, "carousel": 1, "text": 1}
                                                if "text" in supported_types(s["platform"]) else {"image": 2, "carousel": 1}}} if s else None)
    e = parts.get("engagement")
    if e and e["score"] is not None and e["score"] < 60:
        top = (f.get("top_posts") or [None])[0]
        ids = [a["id"] for a in f["accounts"] if a["platform"] not in ("x", "manual")][:2]
        add("Ask questions and invite replies", f"Your engagement rate is {e['value']}, below the {TARGET_ER * 100:.1f}% target.",
            "End posts with a simple question and reply to every comment within a day."
            + (f" Build on your best post: “{top['title']}”." if top else ""), "high", "medium", "engagement",
            {"type": "draft_posts", "params": {"topic": f"A question-led post following up on: {top['title']}" if top else
                                               "A question to our audience about their biggest HR challenge this quarter",
                                               "account_ids": ids}} if ids else None)
    for lag in (f["competitors"]["insights"] or {}).get("lag", [])[:2]:
        add(f"Close the gap on {lag['label'].lower()}", lag["text"],
            "Match the typical competitor first, then aim for the leader.", "medium", "medium", "competitors")
    gr = parts.get("growth")
    if gr and gr["score"] is not None and gr["score"] < 50:
        add("Grow your audience", f"Followers changed {gr['value']}.",
            "Share posts from your team's personal profiles, add follow links to your website and email signature.",
            "medium", "low", "growth")
    w = parts.get("website")
    if w and w["score"] is not None and w["score"] < 60:
        add("Speed up your website", f"Website health is {w['value']}.", "Compress images and remove unused scripts on the home page.",
            "medium", "medium", "website")
    if f.get("search_opportunities"):
        q = f["search_opportunities"][:3]
        add("Write about what people already search for",
            "Search Console shows queries where you appear but rarely get clicks: " + ", ".join(f"“{x['query']}”" for x in q) + ".",
            "Turn these into posts and articles.", "medium", "low", "reach",
            {"type": "add_topic_ideas", "params": {"ideas": [{"title": x["query"].capitalize(), "angle": "Answer this search directly"} for x in q]}})
    if not recs:
        add("Keep going", "Your numbers look healthy across the board.", "Keep your schedule and check back next week.",
            "low", "low", "consistency")
    return recs


async def generate(session: AsyncSession, org: Organization) -> dict:
    """Make a fresh batch of tips. Returns {batch_id, count, source, message}."""
    from app.services.llm import router as llm

    score = await compute(session, org)
    f = await facts(session, org, score)
    source, message = "ai", None
    try:
        import json

        out = await llm.complete_json(session, "plan", "Here are this organization's marketing numbers as JSON. "
                                      "Recommend how to improve its analytics score.\n\n" + json.dumps(f, default=str)[:14000],
                                      system=SYSTEM, temperature=0.4, max_tokens=3000)
        items = out.get("recommendations") if isinstance(out, dict) else out
        if not isinstance(items, list) or not items:
            raise ValueError("The model returned no recommendations")
    except (llm.NoProviderError, llm.AllProvidersFailed, ValueError) as e:
        source = "rules"
        message = ("No AI model is set up, so these tips come from built-in rules. Add one in Settings → Integrations "
                   "for tailored advice." if isinstance(e, llm.NoProviderError)
                   else "The AI model didn't answer, so these tips come from built-in rules. Try again later.")
        log.info("advisor fallback: %s", e)
        items = rule_based(f)

    batch = uuid.uuid4()
    await session.execute(delete(Recommendation).where(Recommendation.status == "new"))
    n = 0
    for i, r in enumerate(items[:10]):
        if not isinstance(r, dict) or not str(r.get("title") or "").strip():
            continue
        session.add(Recommendation(
            organization_id=org.id, batch_id=batch, rank=i, title=str(r["title"]).strip()[:300],
            why=str(r.get("why") or "").strip()[:2000] or None, action_text=str(r.get("action") or "").strip()[:2000] or None,
            impact=_choice(r.get("impact"), ("high", "medium", "low"), "medium"),
            effort=_choice(r.get("effort"), ("low", "medium", "high"), "medium"),
            category=_choice(r.get("category"), tuple(WEIGHTS) + ("timing",), "content_mix"),
            action=await validate_action(session, r.get("one_click")), source=source, status="new",
        ))
        n += 1
    await save_snapshot(session, org.id, score)
    session.add(AnalyticsSnapshot(organization_id=org.id, source="advisor", key="last_run",
                                  data={"batch_id": str(batch), "source": source, "message": message, "count": n}))
    await session.flush()
    return {"batch_id": str(batch), "count": n, "source": source, "message": message}


def rec_out(r: Recommendation) -> dict:
    return {"id": str(r.id), "title": r.title, "why": r.why, "action": r.action_text, "impact": r.impact, "effort": r.effort,
            "category": r.category, "one_click": r.action, "source": r.source, "status": r.status, "rank": r.rank,
            "result": r.result, "created_at": r.created_at.isoformat() if r.created_at else None,
            "status_changed_at": r.status_changed_at.isoformat() if r.status_changed_at else None}
