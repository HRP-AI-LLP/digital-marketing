"""Research & ideas background work.

Job kinds
  research.run   {run_id}            run the deep-research pipeline for one ResearchRun
  brand.suggest  {kind: voice|pillars}  AI suggestions for the brand page (result returned in the job)

Periodic
  research_daily      (5 min)   start each organization's daily run at its local time, once per day
  occasion_greetings  (30 min)  plan greeting posts for observed occasions (when turned on), once per occasion
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal, set_tenant
from app.models import Organization, Post, ResearchRun, SocialAccount
from app.services import brand_suggest, occasions
from app.services import research as svc
from app.services.jobs import JobContext, PermanentError, enqueue, handler, org_ids_active, periodic
from app.services.llm.router import NoProviderError, resolve_chain
from app.services.notify import notify_org

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ research.run
async def _load_run(ctx: JobContext) -> ResearchRun:
    try:
        rid = uuid.UUID(str(ctx.payload.get("run_id")))
    except ValueError:
        raise PermanentError("Missing research run.")
    run = await ctx.session.scalar(select(ResearchRun).where(ResearchRun.id == rid))
    if run is None:
        raise PermanentError("This research run was deleted.")
    return run


@handler("research.run")
async def h_run(ctx: JobContext) -> dict:
    s = ctx.session
    run = await _load_run(ctx)
    if run.status in ("done", "partial"):
        return dict(run.stats or {})
    run_id = run.id
    try:
        stats = await svc.run_pipeline(s, run, progress=ctx.progress)
        await s.commit()
    except svc.AIError as e:
        await _fail(ctx, run_id, str(e))
        raise PermanentError(str(e))
    except PermanentError as e:
        await _fail(ctx, run_id, str(e))
        raise
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:800]
        await _fail(ctx, run_id, msg, final=ctx.attempt >= 2)
        raise
    if stats.get("briefs"):
        try:
            await notify_org(ctx.org_id, "research_ready", f"{stats['briefs']} new idea briefs are ready",
                             "Today's research found fresh topics, posts and articles to write.", link="/research",
                             permission="research.view")
        except Exception:  # noqa: BLE001
            log.exception("research notification failed")
    return stats


async def _fail(ctx: JobContext, run_id: uuid.UUID, message: str, final: bool = True) -> None:
    s = ctx.session
    await s.rollback()
    run = await s.get(ResearchRun, run_id)
    if run is None:
        return
    run.status = "failed" if final else "queued"
    run.error = message
    run.finished_at = svc.now() if final else None
    run.steps = [*(run.steps or []), {"at": svc.now().isoformat(), "stage": "error", "level": "error",
                                      "message": message[:500] + ("" if final else " Retrying shortly.")}]
    await s.commit()


async def start_run(session, org_id: uuid.UUID, *, trigger: str, run_date: str, depth: str,
                    created_by: uuid.UUID | None = None) -> ResearchRun:
    """Create a run and its job in the caller's transaction (caller commits)."""
    run = ResearchRun(organization_id=org_id, run_key=f"daily:{run_date}" if trigger == "scheduled" else None,
                      run_date=run_date, trigger=trigger, depth=depth, status="queued", steps=[], stats={}, data={},
                      created_by=created_by)
    session.add(run)
    await session.flush()
    job = await enqueue(session, "research.run", org_id, {"run_id": str(run.id)}, dedupe_key=f"research.run:{run.id}",
                        priority=6, max_attempts=2, created_by=created_by)
    run.job_id = job.id
    return run


# ------------------------------------------------------------------ daily schedule
def _hhmm(v: str) -> time:
    try:
        h, m = str(v).split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return time(7, 0)


async def schedule_org(org_id: uuid.UUID, at: datetime | None = None) -> ResearchRun | None:
    """Start today's scheduled run when the org's local run time has passed and none exists yet."""
    at = at or datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None or org.status != "active":
            return None
        row = await svc.get_settings_row(s, org_id)
        cfg = svc.effective_config(row)
        if not cfg["enabled"]:
            return None
        local = at.astimezone(svc.org_tz(org))
        if local.time() < _hhmm(cfg["run_time"]):
            return None
        run_date = local.date().isoformat()
        if await s.scalar(select(ResearchRun.id).where(ResearchRun.run_key == f"daily:{run_date}")):
            return None
        if not await resolve_chain(s, "plan"):
            return None  # no AI model yet: nothing to run (the page explains how to set one up)
        try:
            run = await start_run(s, org_id, trigger="scheduled", run_date=run_date, depth=cfg["depth"])
            await s.commit()
        except IntegrityError:  # another worker created it first
            await s.rollback()
            return None
        return run


@periodic(300, "research_daily")
async def research_daily() -> None:
    for org_id in await org_ids_active():
        try:
            await schedule_org(org_id)
        except Exception:
            log.exception("daily research scheduling failed for %s", org_id)


# ------------------------------------------------------------------ occasion greeting posts
GREETING_BRIEF = ("Write a {tone} greeting post for {name} ({date}) from {org}. {guidance} "
                  "Keep it short, warm and inclusive. Mention the company only lightly, and only where it fits the tone.")


async def schedule_greetings(org_id: uuid.UUID, at: datetime | None = None) -> list[Post]:
    """Plan one greeting post per observed occasion and chosen account, `create_hours_before` the posting time."""
    from app.services import scheduler
    from app.services.platforms import supported_types

    at = at or datetime.now(UTC)
    created: list[Post] = []
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None or org.status != "active":
            return []
        row = await svc.get_settings_row(s, org_id)
        cfg = svc.effective_config(row)["occasions"]
        g = cfg["greeting_posts"]
        if not cfg.get("enabled") or not g.get("enabled") or not g.get("account_ids"):
            return []
        tz = svc.org_tz(org)
        today = at.astimezone(tz).date()
        post_at_time = _hhmm(g.get("time") or "09:00")
        ahead = timedelta(hours=max(1, int(g.get("create_hours_before") or 30)))
        greet_cfg = {**cfg, "categories": [c for c in (g.get("categories") or []) if c in (cfg.get("categories") or [])]}
        blackout = await scheduler.blackout_dates(s)
        accounts = (await s.scalars(select(SocialAccount).where(
            SocialAccount.id.in_([uuid.UUID(str(a)) for a in g["account_ids"]])))).all()
        for o in occasions.upcoming(today, greet_cfg, svc.verified_dates(row), days=3):
            if o.category == "season" or o.date.isoformat() in blackout:
                continue
            when = datetime.combine(o.date, post_at_time, tzinfo=tz).astimezone(UTC)
            if when <= at or when - at > ahead:
                continue
            for acc in accounts:
                if not scheduler.account_ready(acc):
                    continue
                exists = await s.scalar(select(Post.id).where(
                    Post.social_account_id == acc.id, Post.meta["occasion_id"].as_string() == o.id))
                if exists:
                    continue
                types = supported_types(acc.platform)
                post_type = g.get("post_type") if g.get("post_type") in types else ("image" if "image" in types else types[0])
                brief = GREETING_BRIEF.format(tone=o.tone, name=o.name, date=o.date.strftime("%d %B %Y"), org=org.name,
                                              guidance=occasions.TONE_GUIDANCE.get(o.tone, ""))
                post = Post(
                    organization_id=org_id, social_account_id=acc.id, platform=acc.platform, post_type=post_type,
                    topic=f"{o.name} greetings", status="planned", mode=g.get("mode") or "approve_first", scheduled_at=when,
                    hashtags=[], media_ids=[], qa={},
                    meta={"source": "occasion", "brief": f"{brief}\nIdeas: {o.hint}", "post_type_fixed": True,
                          "occasion": o.brief_ref(), "occasion_id": o.id},
                )
                s.add(post)
                await s.flush()
                from app.services.content import add_activity

                add_activity(s, post, "planned", f"Greeting for {o.name}")
                await enqueue(s, "post.generate", org_id, {"post_id": str(post.id)}, dedupe_key=f"gen:{post.id}")
                created.append(post)
        await s.commit()
    return created


@periodic(1800, "occasion_greetings")
async def occasion_greetings() -> None:
    for org_id in await org_ids_active():
        try:
            await schedule_greetings(org_id)
        except Exception:
            log.exception("occasion greeting scheduling failed for %s", org_id)


# ------------------------------------------------------------------ brand suggestions
@handler("brand.suggest")
async def h_brand_suggest(ctx: JobContext) -> dict:
    kind = ctx.payload.get("kind")
    try:
        if kind == "voice":
            await ctx.progress("Reading your company profile and writing a voice guide")
            return await brand_suggest.suggest_voice(ctx.session)
        if kind == "pillars":
            await ctx.progress("Working out the topic mix")
            return await brand_suggest.suggest_pillars(ctx.session)
    except NoProviderError:
        raise PermanentError(svc.NO_AI)
    except ValueError as e:
        raise PermanentError(str(e))
    raise PermanentError("Unknown suggestion type.")
