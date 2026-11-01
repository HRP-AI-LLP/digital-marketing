"""Blog autopilot: writes SEO articles on a schedule, with no one starting them.

Settings live in Organization.publishing["blog_autopilot"] = {"enabled": bool, "days": [0..6] (Mon=0),
"time": "HH:MM" (organization time zone), "last_run": "YYYY-MM-DD"}.

On each chosen day, once that time has passed, it takes the next unused keyword from the latest keyword ideas
(asking the AI for fresh ideas when they are used up), creates the article and queues seo.write. What happens once
it's written is the existing "Publish finished articles to the website" setting (app.modules.seo._auto_publish).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import AnalyticsSnapshot, Organization, SeoArticle

log = logging.getLogger(__name__)

DEFAULT_DAYS = [0, 3]  # Monday and Thursday
DEFAULT_TIME = "10:00"
DIFFICULTY = {"low": 0, "medium": 1, "high": 2}


def settings(org: Organization | None) -> dict:
    cfg = dict(((org.publishing if org is not None else None) or {}).get("blog_autopilot") or {})
    days = [d for d in (cfg.get("days") or DEFAULT_DAYS) if isinstance(d, int) and 0 <= d <= 6]
    t = str(cfg.get("time") or DEFAULT_TIME)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        t = DEFAULT_TIME
    return {"enabled": bool(cfg.get("enabled")), "days": sorted(set(days)) or DEFAULT_DAYS, "time": t,
            "last_run": cfg.get("last_run")}


def _norm(k: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (k or "").lower()).split())


def is_due(cfg: dict, local_now: datetime) -> bool:
    if not cfg["enabled"] or local_now.weekday() not in cfg["days"]:
        return False
    if cfg.get("last_run") == local_now.date().isoformat():
        return False
    hh, mm = (int(x) for x in cfg["time"].split(":"))
    return (local_now.hour, local_now.minute) >= (hh, mm)


async def next_keyword(session, org_id: uuid.UUID) -> dict | None:
    """The best unused idea: lowest difficulty first, in the order the AI suggested them."""
    from app.services import seo

    used = {_norm(k) for k in (await session.scalars(select(SeoArticle.keyword))).all()}

    async def pick() -> dict | None:
        snaps = (await session.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "seo_ideas")
                                       .order_by(AnalyticsSnapshot.created_at.desc()).limit(5))).all()
        seen: set[str] = set()
        ideas = []
        for s in snaps:
            for i in (s.data or {}).get("ideas") or []:
                k = _norm(i.get("keyword") or "")
                if k and k not in used and k not in seen:
                    seen.add(k)
                    ideas.append(i)
        ideas.sort(key=lambda i: DIFFICULTY.get(str(i.get("difficulty_guess") or "medium"), 1))
        return ideas[0] if ideas else None

    idea = await pick()
    if idea is None:
        fresh = await seo.keyword_ideas(session, None)
        session.add(AnalyticsSnapshot(organization_id=org_id, source="seo_ideas", key="",
                                      data={"seed": None, "ideas": fresh, "by": "blog autopilot"}))
        await session.flush()
        idea = await pick()
    return idea


async def run_org(org_id: uuid.UUID, now: datetime | None = None) -> dict:
    from app.core.db import SessionLocal, set_tenant
    from app.services import seo
    from app.services.content import org_tz
    from app.services.jobs import enqueue
    from app.services.llm import router as llm_router

    now = now or datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None or org.status != "active":
            return {"created": None}
        cfg = settings(org)
        local = now.astimezone(org_tz(org))
        if not is_due(cfg, local):
            return {"created": None}
        # Mark today first, so a failure doesn't retry every half hour (it tries again on the next chosen day).
        bp = {**((org.publishing or {}).get("blog_autopilot") or {}), "last_run": local.date().isoformat()}
        org.publishing = {**(org.publishing or {}), "blog_autopilot": bp}
        await s.commit()
        try:
            idea = await next_keyword(s, org_id)
        except llm_router.NoProviderError:
            await s.rollback()
            return {"created": None, "reason": "Add an AI text model in Settings → Integrations."}
        if idea is None:
            return {"created": None, "reason": "No keyword ideas left."}
        keyword = " ".join(str(idea["keyword"]).split())
        a = SeoArticle(organization_id=org_id, title=keyword[:1].upper() + keyword[1:], keyword=keyword,
                       slug=await seo.unique_slug(s, seo.slugify(keyword)), secondary_keywords=[], status="queued",
                       outline=[])
        intent = str(idea.get("intent") or "informational").lower()
        seo.set_article_meta(a, word_target=1500, tone=None, notes="Started by the blog autopilot.",
                             search_intent=intent if intent in ("informational", "commercial", "transactional",
                                                                 "navigational") else None)
        s.add(a)
        await s.flush()
        await enqueue(s, "seo.write", org_id, {"article_id": str(a.id)}, dedupe_key=f"seo.write:{a.id}", max_attempts=2)
        await s.commit()
        log.info("blog autopilot started an article on %r for org %s", keyword, org_id)
        return {"created": str(a.id), "keyword": keyword}
