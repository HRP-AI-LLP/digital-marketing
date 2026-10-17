"""AI suggestions for the brand voice and topic pillars, grounded in the company profile, knowledge base,
competitors and recent research. Suggestions are returned for review; nothing is saved here."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BrandProfile, Campaign, Competitor, ContentPillar, IdeaBrief, Organization
from app.services import knowledge
from app.services.knowledge import normalize_name
from app.services.llm import router as llm_router

VOICE_FIELDS = ("voice", "audience", "do_list", "dont_list", "banned_words", "hashtags", "ctas", "emoji_policy")
PILLAR_COLORS = ["#075492", "#0f8dde", "#06a6c7", "#22d3ee", "#0b6d86", "#083f73", "#35a9f4", "#12576d"]

VOICE_SYSTEM = """You are a brand strategist for B2B technology companies in India.
Write a practical brand voice guide for the company's social posts and articles, based only on the facts given.
Be specific to this company (its products, buyers and market), not generic marketing advice.
Return ONLY JSON:
{"voice": "3-5 sentences: personality, tone, how technical, how formal",
 "audience": "2-4 sentences: primary and secondary audiences with roles, company sizes, industries",
 "do_list": ["5-8 concrete writing rules"], "dont_list": ["5-8 things to avoid"],
 "banned_words": ["overused or risky words to avoid"], "hashtags": ["#BrandTag", "5-10 relevant hashtags"],
 "ctas": ["3-5 calls to action that point to the website"], "emoji_policy": "none|light|rich",
 "sample_posts": ["2 short example LinkedIn posts in this voice"],
 "notes": {"voice": "why", "audience": "why", "do_list": "why", "dont_list": "why", "banned_words": "why",
           "hashtags": "why", "ctas": "why", "emoji_policy": "why"}}"""

PILLARS_SYSTEM = """You are a B2B content strategist. Propose 4-7 content pillars (recurring themes) for the company's
social media, with weights that add up to 100. Base them on the company's offerings, audiences, competitors' content
gaps and recent research. Keep names short (2-4 words). Reuse an existing pillar's exact name when it should stay.
Return ONLY JSON: {"pillars": [{"name": "", "description": "one sentence on what it covers", "weight": 20,
 "reason": "why this pillar matters for the company"}], "notes": "one or two sentences on the overall mix"}"""


def _s(v: Any, n: int = 2000) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return re.sub(r"[ \t]+", " ", str(v)).strip()[:n]


def _ls(v: Any, items: int = 12, n: int = 200) -> list[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[\n;]", v) if x.strip()]
    out: dict[str, str] = {}
    for x in v if isinstance(v, list) else []:
        s = _s(x, n).lstrip("-• ").strip()
        if s and normalize_name(s) not in out:
            out[normalize_name(s)] = s
        if len(out) >= items:
            break
    return list(out.values())


def _tag(t: str) -> str:
    return re.sub(r"[^\w]", "", t.lstrip("#"))


async def _context(session: AsyncSession) -> str:
    org_id = session.info.get("org_id")
    org = await session.get(Organization, org_id) if org_id else None
    parts = [await knowledge.company_context(session, max_chars=3000)]
    comps = (await session.scalars(select(Competitor).where(Competitor.status != "rejected")
                                   .order_by(Competitor.overlap_score.desc()).limit(6))).all()
    if comps:
        lines = []
        for c in comps:
            d = c.data or {}
            themes = ", ".join((d.get("content_themes") or [])[:5])
            lines.append(f"- {c.name}: {d.get('positioning') or c.reason or ''}" + (f" Themes: {themes}" if themes else ""))
        parts.append("Competitors:\n" + "\n".join(lines))
    briefs = (await session.scalars(select(IdeaBrief).where(IdeaBrief.status != "dismissed")
                                    .order_by(IdeaBrief.created_at.desc()).limit(15))).all()
    if briefs:
        parts.append("Recent research ideas:\n" + "\n".join(f"- [{b.theme}] {b.title}" for b in briefs))
    if org is not None and org.website:
        parts.append(f"Website: {org.website}")
    return "\n\n".join(p for p in parts if p)


def current_voice(b: BrandProfile | None) -> dict:
    if b is None:
        return {"voice": None, "audience": None, "do_list": [], "dont_list": [], "banned_words": [], "hashtags": [],
                "ctas": [], "emoji_policy": "light"}
    return {"voice": b.voice, "audience": b.audience, "do_list": b.do_list or [], "dont_list": b.dont_list or [],
            "banned_words": b.banned_words or [], "hashtags": b.hashtags or [], "ctas": b.ctas or [],
            "emoji_policy": b.emoji_policy or "light"}


async def suggest_voice(session: AsyncSession) -> dict:
    ctx = await _context(session)
    if len(ctx) < 80:
        raise ValueError("Analyze your website on the Company intelligence page first, so the AI knows what you do.")
    brand = await session.scalar(select(BrandProfile))
    cur = current_voice(brand)
    prompt = f"{ctx}\n\nCurrent brand settings (improve them; keep what is already good):\n" + "\n".join(
        f"- {k}: {v}" for k, v in cur.items() if v)
    raw = await llm_router.complete_json(session, "plan", prompt[:12000], system=VOICE_SYSTEM, temperature=0.5, max_tokens=2500)
    raw = raw if isinstance(raw, dict) else {}
    emoji = _s(raw.get("emoji_policy"), 10).lower()
    suggested = {
        "voice": _s(raw.get("voice"), 4000) or None,
        "audience": _s(raw.get("audience"), 4000) or None,
        "do_list": _ls(raw.get("do_list"), 10, 300),
        "dont_list": _ls(raw.get("dont_list"), 10, 300),
        "banned_words": _ls(raw.get("banned_words"), 30, 60),
        "hashtags": [t for t in (_tag(h) for h in _ls(raw.get("hashtags"), 12, 60)) if t],
        "ctas": _ls(raw.get("ctas"), 6, 300),
        "emoji_policy": emoji if emoji in ("none", "light", "rich") else cur["emoji_policy"],
    }
    notes = raw.get("notes") if isinstance(raw.get("notes"), dict) else {}
    return {
        "current": cur,
        "suggested": {k: v for k, v in suggested.items() if v not in (None, [], "")},
        "notes": {k: _s(notes.get(k), 400) for k in VOICE_FIELDS if _s(notes.get(k))},
        "samples": _ls(raw.get("sample_posts"), 2, 1500),
    }


async def suggest_pillars(session: AsyncSession) -> dict:
    ctx = await _context(session)
    if len(ctx) < 80:
        raise ValueError("Analyze your website on the Company intelligence page first, so the AI knows what you do.")
    pillars = (await session.scalars(select(ContentPillar).order_by(ContentPillar.weight.desc()))).all()
    cur = [{"id": str(p.id), "name": p.name, "description": p.description, "weight": p.weight, "active": p.active,
            "color": p.color} for p in pillars]
    prompt = ctx + ("\n\nCurrent pillars:\n" + "\n".join(f"- {p['name']} ({p['weight']}): {p['description'] or ''}" for p in cur)
                    if cur else "\n\nThere are no pillars yet.")
    raw = await llm_router.complete_json(session, "plan", prompt[:12000], system=PILLARS_SYSTEM, temperature=0.5, max_tokens=1800)
    rows = raw.get("pillars") if isinstance(raw, dict) else raw
    by_name = {normalize_name(p["name"]): p for p in cur}
    out: list[dict] = []
    seen: set[str] = set()
    for i, r in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(r, dict):
            continue
        name = _s(r.get("name"), 120)
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            weight = int(round(float(r.get("weight") or 0)))
        except (TypeError, ValueError):
            weight = 0
        match = by_name.get(key)
        out.append({"name": name, "description": _s(r.get("description"), 500) or None, "weight": max(0, min(100, weight)),
                    "reason": _s(r.get("reason"), 400), "existing_id": match["id"] if match else None,
                    "color": match["color"] if match else PILLAR_COLORS[i % len(PILLAR_COLORS)]})
        if len(out) >= 8:
            break
    total = sum(p["weight"] for p in out)
    if out and total != 100:  # normalise to 100 in steps of 5 (largest remainder)
        raw = [(p["weight"] or 1) for p in out]
        units = [w * 20 / sum(raw) for w in raw]
        alloc = [max(1, int(u)) for u in units]
        order = sorted(range(len(out)), key=lambda i: -(units[i] - int(units[i])))
        i = 0
        while sum(alloc) < 20 and order:
            alloc[order[i % len(order)]] += 1
            i += 1
        while sum(alloc) > 20:
            j = max(range(len(alloc)), key=lambda k: alloc[k])
            alloc[j] -= 1
        for p, a in zip(out, alloc, strict=True):
            p["weight"] = a * 5
    return {"current": cur, "suggested": out, "notes": _s(raw.get("notes") if isinstance(raw, dict) else "", 600)}


CAMPAIGNS_SYSTEM = """You are a B2B marketing strategist for an Indian technology company.
Plan 4-6 social media campaigns for the coming months. Mix:
- occasion campaigns tied to the festivals, national days, awareness days and business seasons listed (only when the
  tie-in is natural and respectful; never sell on solemn or commemorative days),
- product or offering pushes (a launch, a feature, a free trial, a demo offer),
- thought-leadership series on topics the buyers care about right now (use the research ideas),
- proof campaigns (customer stories, results, behind the scenes).
Dates must fall inside the planning window, use YYYY-MM-DD, and start a few days BEFORE an occasion so posts can
build up. Campaigns may overlap but avoid more than two running at once. Do not repeat an existing campaign.
Each brief is what the AI writer gets for every campaign post: the message, the angle, the offer or call to action
and any link from the facts. Never invent discounts, prices, customers or statistics.
Return ONLY JSON: {"campaigns": [{"name": "short name", "goal": "awareness|leads|launch|engagement|hiring|community",
 "starts_on": "YYYY-MM-DD", "ends_on": "YYYY-MM-DD", "boost": 30, "occasion": "occasion name or empty",
 "brief": "3-6 sentences", "post_ideas": ["3-5 concrete post ideas"], "channels": ["linkedin", "instagram"],
 "reason": "one sentence on why now and why it fits the company"}],
 "notes": "one or two sentences on the overall plan"}"""


async def suggest_campaigns(session: AsyncSession, horizon_days: int = 120) -> dict:
    """Campaign ideas for the next few months, grounded in the company, research ideas and the occasions calendar."""
    from datetime import date, timedelta

    from app.services import occasions
    from app.services import research as rsvc

    ctx = await _context(session)
    if len(ctx) < 80:
        raise ValueError("Analyze your website on the Company intelligence page first, so the AI knows what you do.")
    org_id = session.info.get("org_id")
    org = await session.get(Organization, org_id) if org_id else None
    today = date.today()
    try:
        from datetime import datetime

        today = datetime.now(rsvc.org_tz(org)).date()
    except Exception:  # noqa: BLE001
        pass
    end = today + timedelta(days=horizon_days)
    row = await rsvc.get_settings_row(session, org_id) if org_id else None
    occ_cfg = rsvc.effective_config(row)["occasions"]
    occ = [o for o in occasions.occasions_between(today, end, rsvc.verified_dates(row))
           if occasions.matches_settings(o, {**occ_cfg, "enabled": True})][:40]
    existing = (await session.scalars(select(Campaign).order_by(Campaign.starts_on.desc().nullslast()).limit(20))).all()
    parts = [ctx, f"Planning window: {today.isoformat()} to {end.isoformat()} (today is {today.strftime('%A %d %B %Y')})."]
    if occ:
        parts.append("Occasions in the window (date, name, tone, how to tie in):\n" + "\n".join(
            f"- {o.date.isoformat()}{(' to ' + o.end_date.isoformat()) if o.end_date else ''}: {o.name} "
            f"[{o.category}, {o.tone}] {o.hint}" for o in occ))
    if existing:
        parts.append("Existing campaigns (do not repeat):\n" + "\n".join(
            f"- {c.name} ({c.starts_on or '?'} to {c.ends_on or '?'})" for c in existing))
    raw = await llm_router.complete_json(session, "plan", "\n\n".join(parts)[:14000], system=CAMPAIGNS_SYSTEM,
                                         temperature=0.6, max_tokens=3000)
    rows = raw.get("campaigns") if isinstance(raw, dict) else raw
    taken = {normalize_name(c.name) for c in existing}
    out: list[dict] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        name = _s(r.get("name"), 200)
        if not name or normalize_name(name) in taken:
            continue
        taken.add(normalize_name(name))
        s_on, e_on = _date(r.get("starts_on")), _date(r.get("ends_on"))
        if s_on and s_on < today:
            s_on = today
        if s_on and s_on > end:
            continue
        if s_on and (not e_on or e_on < s_on):
            e_on = s_on + timedelta(days=14)
        try:
            boost = int(round(float(r.get("boost") or 40)))
        except (TypeError, ValueError):
            boost = 40
        ideas = _ls(r.get("post_ideas"), 6, 300)
        brief = _s(r.get("brief"), 3000)
        if ideas:
            brief = (brief + "\nPost ideas:\n" + "\n".join(f"- {i}" for i in ideas)).strip()
        out.append({
            "name": name, "brief": brief[:4000] or None,
            "starts_on": s_on.isoformat() if s_on else None, "ends_on": e_on.isoformat() if e_on else None,
            "boost": max(10, min(100, boost)), "goal": _s(r.get("goal"), 40), "occasion": _s(r.get("occasion"), 120),
            "channels": _ls(r.get("channels"), 6, 40), "post_ideas": ideas, "reason": _s(r.get("reason"), 400),
        })
        if len(out) >= 8:
            break
    out.sort(key=lambda c: c["starts_on"] or "9999")
    if not out:
        raise ValueError("The AI didn't return usable campaign ideas. Try again, or pick a different model in Task routing.")
    return {"suggested": out, "notes": _s(raw.get("notes") if isinstance(raw, dict) else "", 600),
            "window": {"start": today.isoformat(), "end": end.isoformat()}}


def _date(v):
    from datetime import date

    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None
