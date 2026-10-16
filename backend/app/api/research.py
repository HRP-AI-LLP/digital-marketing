"""Research & ideas API: daily research runs, idea briefs (with content actions), settings and occasions."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select

from app.api.content import _account, _aware, _check_link, _tz
from app.core.deps import Context, get_context, require
from app.models import IdeaBrief, Organization, Post, ProviderConfig, ResearchRun
from app.modules.research import _hhmm, start_run
from app.services import audit, occasions
from app.services import content as content_svc
from app.services import research as svc
from app.services.jobs import enqueue
from app.services.llm.router import resolve_chain
from app.services.platforms import POST_TYPES, supported_types

router = APIRouter(prefix="/research", tags=["research"])

VIEW = "research.view"
MANAGE = "research.manage"
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


# ------------------------------------------------------------------ serializers
def brief_out(b: IdeaBrief) -> dict:
    data = b.data or {}
    return {
        "id": b.id, "run_id": b.run_id, "run_date": b.run_date, "title": b.title, "angle": b.angle, "why_now": b.why_now,
        "theme": b.theme, "theme_label": svc.THEME_LABELS.get(b.theme, b.theme.replace("_", " ").capitalize()),
        "audience": b.audience, "facts": b.facts or [], "formats": b.formats or [], "article": b.article or {},
        "cta": b.cta or {}, "hashtags": b.hashtags or [], "scores": b.scores or {}, "score": round(b.score or 0, 3),
        "status": b.status, "occasion": b.occasion, "pillar_id": b.pillar_id, "opportunity_id": b.opportunity_id,
        "used": b.used or [], "reason": data.get("reason"), "greeting": data.get("greeting"),
        "followed_up": bool(data.get("followed_up")), "created_at": b.created_at,
    }


def run_out(r: ResearchRun, full: bool = False) -> dict:
    out = {
        "id": r.id, "run_date": r.run_date, "trigger": r.trigger, "depth": r.depth, "status": r.status, "job_id": r.job_id,
        "started_at": r.started_at, "finished_at": r.finished_at, "stats": r.stats or {}, "error": r.error,
        "created_at": r.created_at, "last_step": (r.steps or [{}])[-1] if r.steps else None,
    }
    if full:
        out["steps"] = r.steps or []
    return out


async def _brief(ctx: Context, bid: uuid.UUID) -> IdeaBrief:
    b = await ctx.session.scalar(select(IdeaBrief).where(IdeaBrief.id == bid))
    if b is None:
        raise _bad("Idea brief not found.", 404)
    return b


# ------------------------------------------------------------------ overview
async def _providers(ctx: Context) -> dict:
    search_cfgs = (await ctx.session.scalars(select(ProviderConfig).where(
        ProviderConfig.kind == "search", ProviderConfig.enabled.is_(True)))).all()
    return {
        "ai": bool(await resolve_chain(ctx.session, "plan")),
        "embeddings": bool(await resolve_chain(ctx.session, "embed")),
        "search": [{"name": c.name, "provider": c.provider} for c in search_cfgs],
        "search_fallback": not search_cfgs,  # DuckDuckGo without a key
    }


def _next_run(cfg: dict, org: Organization | None, now: datetime, ran_today: bool) -> str | None:
    if not cfg["enabled"]:
        return None
    tz = svc.org_tz(org)
    local = now.astimezone(tz)
    at = datetime.combine(local.date(), _hhmm(cfg["run_time"]), tzinfo=tz)
    if ran_today or at <= local:
        at = datetime.combine(local.date() + timedelta(days=1), _hhmm(cfg["run_time"]), tzinfo=tz)
    return at.astimezone(UTC).isoformat()


@router.get("/overview")
async def overview(ctx: Context = Depends(require(VIEW))):
    s = ctx.session
    await svc.sync_used_from_topics(s)
    row = await svc.get_settings_row(s, ctx.org_id)
    cfg = svc.effective_config(row)
    today = svc.local_today(ctx.org)
    latest = (await s.scalars(select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(1))).first()
    active = (await s.scalars(select(ResearchRun).where(ResearchRun.status.in_(("queued", "running")))
                              .order_by(ResearchRun.created_at.desc()).limit(1))).first()
    ran_today = bool(await s.scalar(select(ResearchRun.id).where(ResearchRun.run_key == f"daily:{today.isoformat()}")))
    counts = dict((await s.execute(select(IdeaBrief.status, func.count()).group_by(IdeaBrief.status))).all())
    today_count = await s.scalar(select(func.count()).select_from(IdeaBrief).where(IdeaBrief.run_date == today.isoformat())) or 0
    latest_date = await s.scalar(select(func.max(IdeaBrief.run_date)))
    await s.commit()
    return {
        "today": today.isoformat(), "timezone": ctx.org.timezone if ctx.org else "Asia/Kolkata",
        "settings": {k: cfg[k] for k in ("enabled", "run_time", "depth", "ideas_per_day")},
        "next_run_at": _next_run(cfg, ctx.org, datetime.now(UTC), ran_today),
        "latest_run": run_out(latest, full=True) if latest else None, "active_run": run_out(active, full=True) if active else None,
        "counts": {"today": today_count, **{k: counts.get(k, 0) for k in ("new", "saved", "used", "dismissed")}},
        "latest_brief_date": latest_date, "providers": await _providers(ctx),
    }


# ------------------------------------------------------------------ runs
class RunIn(BaseModel):
    depth: Literal["quick", "standard", "deep"] | None = None


@router.post("/runs", status_code=202)
async def run_now(body: RunIn | None = None, ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    active = (await s.scalars(select(ResearchRun).where(ResearchRun.status.in_(("queued", "running"))).limit(1))).first()
    if active is not None:
        return {"run": run_out(active, full=True), "job_id": active.job_id, "already_running": True}
    if not await resolve_chain(s, "plan"):
        raise _bad(svc.NO_AI)
    cfg = svc.effective_config(await svc.get_settings_row(s, ctx.org_id))
    depth = (body.depth if body and body.depth else None) or cfg["depth"]
    run = await start_run(s, ctx.org_id, trigger="manual", run_date=svc.local_today(ctx.org).isoformat(), depth=depth,
                          created_by=ctx.user.id)
    audit.record(s, action="research.run", organization_id=ctx.org_id, actor=ctx.user, entity_type="research_run",
                 entity_id=run.id, summary=f"Started {depth} research", ip=ctx.ip)
    await s.commit()
    return {"run": run_out(run, full=True), "job_id": run.job_id, "already_running": False}


@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=100), ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(limit))).all()
    return [run_out(r) for r in rows]


@router.get("/runs/{rid}")
async def get_run(rid: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    r = await ctx.session.scalar(select(ResearchRun).where(ResearchRun.id == rid))
    if r is None:
        raise _bad("Research run not found.", 404)
    return run_out(r, full=True)


# ------------------------------------------------------------------ briefs
@router.get("/briefs")
async def list_briefs(
    day: str | None = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    theme: str | None = None, fmt: str | None = Query(None, alias="format"),
    status_: str | None = Query(None, alias="status"), q: str | None = Query(None, max_length=200),
    occasion: bool | None = None, limit: int = Query(100, ge=1, le=300),
    ctx: Context = Depends(require(VIEW)),
):
    stmt = select(IdeaBrief)
    if day:
        stmt = stmt.where(IdeaBrief.run_date == day)
    if theme:
        stmt = stmt.where(IdeaBrief.theme == theme)
    if status_ == "active":
        stmt = stmt.where(IdeaBrief.status.in_(("new", "saved")))
    elif status_:
        stmt = stmt.where(IdeaBrief.status == status_)
    else:
        stmt = stmt.where(IdeaBrief.status != "dismissed")
    if occasion is not None:
        stmt = stmt.where(IdeaBrief.occasion.is_not(None) if occasion else IdeaBrief.occasion.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(IdeaBrief.title.ilike(like), IdeaBrief.angle.ilike(like)))
    rows = (await ctx.session.scalars(stmt.order_by(IdeaBrief.run_date.desc(), IdeaBrief.score.desc()).limit(limit))).all()
    if fmt:
        rows = [b for b in rows if any(f.get("format") == fmt for f in b.formats or [])]
    return [brief_out(b) for b in rows]


@router.get("/briefs/days")
async def brief_days(ctx: Context = Depends(require(VIEW))):
    return await svc.brief_counts_by_day(ctx.session, 60)


@router.get("/briefs/{bid}")
async def get_brief(bid: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    return brief_out(await _brief(ctx, bid))


class BriefPatch(BaseModel):
    status: Literal["new", "saved", "dismissed"]


@router.patch("/briefs/{bid}")
async def update_brief(bid: uuid.UUID, body: BriefPatch, ctx: Context = Depends(require(MANAGE))):
    b = await _brief(ctx, bid)
    b.status = body.status
    await ctx.session.commit()
    return brief_out(b)


@router.post("/briefs/{bid}/topic")
async def brief_to_topic(bid: uuid.UUID, ctx: Context = Depends(require(VIEW, "content.create"))):
    b = await _brief(ctx, bid)
    opp = await svc.add_to_topics(ctx.session, b)
    audit.record(ctx.session, action="research.topic", organization_id=ctx.org_id, actor=ctx.user, entity_type="idea_brief",
                 entity_id=b.id, summary=f"Added “{b.title[:80]}” to planner topics", ip=ctx.ip)
    await ctx.session.commit()
    return {"brief": brief_out(b), "opportunity_id": opp.id}


@router.get("/briefs/{bid}/composer")
async def composer_prefill(bid: uuid.UUID, ctx: Context = Depends(require(VIEW, "content.create"))):
    """Everything the post composer needs to write a grounded post from this brief."""
    b = await _brief(ctx, bid)
    fmts = [f.get("format") for f in b.formats or []]
    post_type = "carousel" if "instagram_carousel" in fmts else "video" if "short_video" in fmts else ""
    if b.occasion:
        post_type = "image"
    return {"brief_id": b.id, "title": b.title, "brief": svc.brief_text(b), "link_url": (b.cta or {}).get("url") or None,
            "post_type": post_type, "pillar_id": b.pillar_id, "occasion": b.occasion}


class ComposeFromBrief(BaseModel):
    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    brief: str | None = Field(None, min_length=3, max_length=4000)
    post_type: str | None = None
    pillar_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    then: Literal["draft", "review"] = "draft"
    link_url: str | None = Field(None, max_length=2000)

    @field_validator("link_url")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        return _check_link(v)


@router.post("/briefs/{bid}/compose")
async def compose_from_brief(bid: uuid.UUID, body: ComposeFromBrief, ctx: Context = Depends(require(VIEW, "content.create"))):
    """Create AI-written posts grounded in the brief's facts (same pipeline as the composer), then mark it used."""
    b = await _brief(ctx, bid)
    at = _aware(body.scheduled_at, _tz(ctx))
    if at is not None and at <= content_svc.now_utc():
        raise _bad("Pick a future date and time, or leave it empty.")
    text = (body.brief or "").strip() or svc.brief_text(b)
    link = (body.link_url or "").strip() or None
    out = []
    for aid in dict.fromkeys(body.account_ids):
        account = await _account(ctx, aid)
        supported = supported_types(account.platform)
        post_type = body.post_type or ("text" if "text" in supported else supported[0])
        if post_type not in supported:
            label = content_svc.platform_of(account.platform).label
            raise _bad(f"{label} ({account.name}) doesn't support {POST_TYPES.get(post_type, post_type).lower()} posts. "
                       "Pick another post type or remove that account.")
        meta = {"source": "research", "brief": text, "post_type_fixed": True, "research_brief_id": str(b.id)}
        if b.angle:
            meta["angle"] = b.angle
        if b.occasion:
            meta["occasion"] = b.occasion
            meta["occasion_id"] = (b.occasion or {}).get("id")
        p = Post(
            organization_id=ctx.org_id, social_account_id=account.id, platform=account.platform, post_type=post_type,
            topic=b.title[:300], pillar_id=body.pillar_id or b.pillar_id, scheduled_at=at, link_url=link, status="planned",
            mode="manual" if body.then == "draft" else "approve_first", created_by=ctx.user.id, hashtags=[], media_ids=[],
            qa={}, meta=meta,
        )
        ctx.session.add(p)
        await ctx.session.flush()
        content_svc.add_activity(ctx.session, p, "planned", "Composed with AI from a research brief", user=ctx.user)
        job = await enqueue(ctx.session, "post.generate", ctx.org_id, {"post_id": str(p.id)}, dedupe_key=f"gen:{p.id}",
                            priority=1, created_by=ctx.user.id)
        svc.mark_used(b, "post", p.id)
        out.append({"post_id": p.id, "job_id": job.id, "account_id": account.id})
    audit.record(ctx.session, action="research.compose", organization_id=ctx.org_id, actor=ctx.user, entity_type="idea_brief",
                 entity_id=b.id, summary=f"Created {len(out)} post(s) from “{b.title[:80]}”", ip=ctx.ip)
    await ctx.session.commit()
    return {"posts": out, "brief": brief_out(b)}


class ArticleFromBrief(BaseModel):
    keyword: str | None = Field(None, min_length=2, max_length=200)
    word_target: int = Field(1500, ge=500, le=5000)


@router.post("/briefs/{bid}/article", status_code=201)
async def article_from_brief(bid: uuid.UUID, body: ArticleFromBrief | None = None,
                             ctx: Context = Depends(require(VIEW, "seo.manage"))):
    """Start an SEO article (seo.write job) from the brief's keyword, outline, facts and sources."""
    from app.api.seo import ArticleIn, create_article

    b = await _brief(ctx, bid)
    art = b.article or {}
    keyword = (body.keyword if body and body.keyword else None) or art.get("keyword") or b.title[:120]
    notes = [f"Angle: {b.angle}" if b.angle else "", f"Why now: {b.why_now}" if b.why_now else ""]
    if art.get("outline"):
        notes.append("Suggested outline: " + " | ".join(art["outline"][:10]))
    if b.facts:
        notes.append("Use these sourced facts (link the sources): " + " ".join(
            f"- {f.get('fact')} ({f.get('publisher') or ''} {f.get('source_url') or ''})" for f in b.facts[:6]))
    if (b.cta or {}).get("url"):
        notes.append(f"Call to action: {b.cta.get('text') or ''} {b.cta['url']}")
    payload = ArticleIn(
        keyword=" ".join(str(keyword).split())[:200],
        secondary_keywords=[k for k in (art.get("secondary_keywords") or []) if isinstance(k, str) and k.strip()][:15],
        audience=(b.audience or None) and b.audience[:500], word_target=body.word_target if body else 1500,
        notes="\n".join(n for n in notes if n)[:2000] or None, search_intent="informational",
    )
    result = await create_article(payload, ctx)
    b = await _brief(ctx, bid)
    svc.mark_used(b, "article", result["article"]["id"])
    await ctx.session.commit()
    return {**result, "brief": brief_out(b)}


# ------------------------------------------------------------------ settings
class CustomTheme(BaseModel):
    label: str = Field(min_length=2, max_length=80)
    focus: str | None = Field(None, max_length=300)


class GreetingIn(BaseModel):
    enabled: bool = False
    account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    time: str = "09:00"
    mode: Literal["manual", "approve_first", "auto_with_window", "full_auto"] = "approve_first"
    post_type: str = "image"
    categories: list[str] = Field(default_factory=lambda: ["national", "festival"])

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        if not HHMM.match(v):
            raise ValueError("Use a 24-hour time like 09:00.")
        return v


class OccasionsIn(BaseModel):
    enabled: bool = True
    categories: list[str] = Field(default_factory=lambda: list(occasions.CATEGORIES))
    regions: list[str] = Field(default_factory=lambda: ["all"])
    lead_days: int = Field(10, ge=1, le=60)
    greeting_posts: GreetingIn = Field(default_factory=GreetingIn)

    @field_validator("categories")
    @classmethod
    def _cats(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in occasions.CATEGORIES]
        if bad:
            raise ValueError(f"Unknown occasion category: {bad[0]}")
        return list(dict.fromkeys(v))

    @field_validator("regions")
    @classmethod
    def _regions(cls, v: list[str]) -> list[str]:
        bad = [r for r in v if r != "all" and r not in occasions.REGIONS]
        if bad:
            raise ValueError(f"Unknown region: {bad[0]}")
        return list(dict.fromkeys(v)) or ["all"]


class AutoTopicsIn(BaseModel):
    enabled: bool = True
    min_score: float = Field(0.75, ge=0, le=1)
    max_per_day: int = Field(3, ge=0, le=20)


def _clean_list(v: list[str], n: int, item: int) -> list[str]:
    out: list[str] = []
    for x in v:
        s = " ".join(str(x).split())[:item]
        if s and s.lower() not in {o.lower() for o in out}:
            out.append(s)
    return out[:n]


class SettingsIn(BaseModel):
    enabled: bool = True
    run_time: str = "07:00"
    depth: Literal["quick", "standard", "deep"] = "standard"
    ideas_per_day: int = Field(8, ge=1, le=30)
    themes: dict[str, bool] = Field(default_factory=dict)
    custom_themes: list[CustomTheme] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    trusted_domains: list[str] = Field(default_factory=list, max_length=50)
    blocked_domains: list[str] = Field(default_factory=list, max_length=200)
    excluded_topics: list[str] = Field(default_factory=list, max_length=50)
    auto_topics: AutoTopicsIn = Field(default_factory=AutoTopicsIn)
    occasions: OccasionsIn = Field(default_factory=OccasionsIn)

    @field_validator("run_time")
    @classmethod
    def _time(cls, v: str) -> str:
        if not HHMM.match(v):
            raise ValueError("Use a 24-hour time like 07:00.")
        return v

    @field_validator("themes")
    @classmethod
    def _themes(cls, v: dict[str, bool]) -> dict[str, bool]:
        return {k: bool(val) for k, val in v.items() if k in svc.THEME_LABELS}

    @field_validator("trusted_domains", "blocked_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out = []
        for d in v:
            d = d.strip().lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
            if d and "." in d and re.fullmatch(r"[a-z0-9.-]+", d) and d not in out:
                out.append(d)
        return out


def settings_meta() -> dict:
    return {
        "themes": [{"key": t["key"], "label": t["label"], "about": t["about"]} for t in svc.THEMES],
        "depths": [
            {"id": "quick", "label": "Quick", "about": f"About {svc.DEPTHS['quick'].pages} pages, a few minutes"},
            {"id": "standard", "label": "Standard", "about": f"About {svc.DEPTHS['standard'].pages} pages"},
            {"id": "deep", "label": "Deep", "about": f"About {svc.DEPTHS['deep'].pages} pages plus follow-up searches on the best ideas"},
        ],
        "formats": [{"id": k, "label": v} for k, v in svc.FORMATS.items()],
        "occasion_categories": [{"id": k, "label": v} for k, v in occasions.CATEGORIES.items()],
        "regions": [{"id": k, "label": v} for k, v in sorted(occasions.REGIONS.items(), key=lambda kv: kv[1])],
    }


@router.get("/settings")
async def get_settings(ctx: Context = Depends(require(VIEW))):
    cfg = svc.effective_config(await svc.get_settings_row(ctx.session, ctx.org_id))
    return {"settings": cfg, "meta": settings_meta(), "providers": await _providers(ctx)}


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: Context = Depends(require(MANAGE))):
    row = await svc.get_settings_row(ctx.session, ctx.org_id, create=True)
    g = body.occasions.greeting_posts
    if g.enabled and g.account_ids:
        for aid in g.account_ids:
            await _account(ctx, aid)
    if g.enabled and g.mode in ("auto_with_window", "full_auto") and "content.approve" not in ctx.permissions:
        raise _bad("You need the 'content.approve' permission to publish greetings without review.", 403)
    row.enabled, row.run_time, row.depth, row.ideas_per_day = body.enabled, body.run_time, body.depth, body.ideas_per_day
    occ = body.occasions.model_dump()
    occ["greeting_posts"]["account_ids"] = [str(a) for a in g.account_ids]
    row.config = {
        "themes": body.themes, "custom_themes": [c.model_dump() for c in body.custom_themes],
        "keywords": _clean_list(body.keywords, 30, 80), "trusted_domains": body.trusted_domains,
        "blocked_domains": body.blocked_domains, "excluded_topics": _clean_list(body.excluded_topics, 50, 120),
        "auto_topics": body.auto_topics.model_dump(), "occasions": occ,
    }
    audit.record(ctx.session, action="research.settings", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="research_settings", entity_id=row.id, summary="Changed research settings", ip=ctx.ip)
    await ctx.session.commit()
    return {"settings": svc.effective_config(row), "meta": settings_meta(), "providers": await _providers(ctx)}


# ------------------------------------------------------------------ occasions
@router.get("/occasions")
async def list_occasions(
    start: date | None = Query(None, alias="from"), end: date | None = Query(None, alias="to"),
    days: int = Query(45, ge=1, le=400), include_all: bool = Query(False, alias="all"),
    ctx: Context = Depends(get_context),
):
    """Upcoming occasions with the organization's observance settings applied (also used by the Calendar)."""
    if ctx.org is None:
        raise _bad("Select an organization first.")
    if VIEW not in ctx.permissions and "content.view" not in ctx.permissions:
        raise _bad("You need the 'research.view' permission for this.", 403)
    row = await svc.get_settings_row(ctx.session, ctx.org_id)
    cfg = svc.effective_config(row)["occasions"]
    start = start or svc.local_today(ctx.org)
    end = end or start + timedelta(days=days)
    if (end - start).days > 400:
        raise _bad("Choose a range of up to 400 days.")
    items = occasions.occasions_between(start, end, svc.verified_dates(row))
    briefs = await svc.existing_occasion_ids(ctx.session)
    greeted = set((await ctx.session.scalars(select(Post.meta["occasion_id"].as_string()).where(
        Post.meta["occasion_id"].as_string().is_not(None)))).all())
    out = []
    for o in items:
        observed = occasions.matches_settings(o, cfg)
        if not observed and not include_all:
            continue
        out.append({**o.to_dict(), "observed": observed, "has_brief": o.id in briefs, "greeting_planned": o.id in greeted})
    return {"from": start.isoformat(), "to": end.isoformat(), "lead_days": cfg["lead_days"], "items": out,
            "data_years": occasions.bundled_years()}
