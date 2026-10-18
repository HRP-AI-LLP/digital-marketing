"""AI search visibility background work.

Job kinds
  aivis.run        {run_id}    ask every tracked prompt on every chosen engine, analyse, score and recommend
  aivis.suggest    {count}     suggest prompts to track (returned in the job result)
  aivis.recommend  {run_id}    rewrite a run's recommendations
  aivis.llms       {}          generate llms.txt / llms-full.txt from the company profile and crawled pages

Periodic
  aivis_weekly  (15 min)  start each organization's weekly run at its chosen weekday and local time, once per week
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionLocal, set_tenant
from app.models import AivisRun, Organization
from app.services import aivis as svc
from app.services import llms_txt
from app.services.jobs import JobContext, PermanentError, handler, org_ids_active, periodic
from app.services.llm.router import NoProviderError
from app.services.notify import notify_org

log = logging.getLogger(__name__)


async def _load_run(ctx: JobContext) -> AivisRun:
    try:
        rid = uuid.UUID(str(ctx.payload.get("run_id")))
    except ValueError:
        raise PermanentError("Missing visibility run.")
    run = await ctx.session.scalar(select(AivisRun).where(AivisRun.id == rid))
    if run is None:
        raise PermanentError("This visibility run was deleted.")
    return run


async def _fail(ctx: JobContext, run_id: uuid.UUID, message: str, final: bool = True) -> None:
    s = ctx.session
    await s.rollback()
    run = await s.get(AivisRun, run_id)
    if run is None:
        return
    run.status = "failed" if final else "queued"
    run.error = message
    run.finished_at = svc.now() if final else None
    svc._step(run, message + ("" if final else " Retrying shortly."), "error")
    await s.commit()


@handler("aivis.run")
async def h_run(ctx: JobContext) -> dict:
    s = ctx.session
    run = await _load_run(ctx)
    if run.status in ("done", "partial"):
        return dict(run.metrics or {})
    run_id = run.id
    try:
        m = await svc.run_pipeline(s, run, progress=ctx.progress)
        await s.commit()
    except ValueError as e:
        await _fail(ctx, run_id, str(e))
        raise PermanentError(str(e))
    except Exception as e:
        log.exception("aivis run failed")
        await _fail(ctx, run_id, "Something went wrong while checking AI answers. " + type(e).__name__,
                    final=ctx.attempt >= 2)
        raise
    await _alert_on_drop(ctx, run_id)
    return {"visibility": m.get("visibility"), "answers": m.get("answers"), "failed": m.get("failed")}


async def _alert_on_drop(ctx: JobContext, run_id: uuid.UUID) -> None:
    s = ctx.session
    run = await s.get(AivisRun, run_id)
    if run is None or run.status not in ("done", "partial"):
        return
    cfg = svc.effective_config(await svc.get_settings_row(s, ctx.org_id))
    drop = svc.drop_points(await svc.previous_run(s, run), run)
    limit = float(cfg.get("drop_alert_points") or 0)
    if drop is None or limit <= 0 or drop < limit:
        return
    try:
        await notify_org(ctx.org_id, "aivis_drop", f"AI visibility dropped {drop:g} points",
                         f"You were mentioned in {run.metrics.get('visibility', 0)}% of AI answers this run, down from "
                         f"{round(run.metrics.get('visibility', 0) + drop, 1)}%. See which prompts you lost.",
                         link="/ai-visibility", permission="aivis.view")
    except Exception:  # noqa: BLE001
        log.exception("aivis drop notification failed")


@handler("aivis.suggest")
async def h_suggest(ctx: JobContext) -> dict:
    try:
        n = max(3, min(25, int(ctx.payload.get("count") or 15)))
    except (TypeError, ValueError):
        n = 15
    await ctx.progress("Reading your company profile, offerings and competitors")
    try:
        return {"prompts": await svc.suggest_prompts(ctx.session, n)}
    except NoProviderError:
        raise PermanentError(svc.NO_AI)
    except ValueError as e:
        raise PermanentError("The AI model didn't return usable suggestions. Try again. " + str(e)[:100])


@handler("aivis.recommend")
async def h_recommend(ctx: JobContext) -> dict:
    s = ctx.session
    run = await _load_run(ctx)
    if not run.metrics or not run.metrics.get("answers"):
        raise PermanentError("This run has no answers to learn from.")
    row = await svc.get_settings_row(s, ctx.org_id)
    await ctx.progress("Looking at the gaps and writing recommendations")
    run.recommendations = await svc.recommendations(s, run.metrics, (row.data or {}).get("site_check") if row else None)
    await s.commit()
    return {"count": len(run.recommendations)}


@handler("aivis.llms")
async def h_llms(ctx: JobContext) -> dict:
    s = ctx.session
    await ctx.progress("Reading your company profile and website pages")
    out = await llms_txt.generate(s, use_ai=bool(ctx.payload.get("use_ai", True)))
    row = await svc.ensure_settings_row(s, ctx.org_id)
    data = dict(row.data or {})
    data["llms"] = {**out, "edited_at": None, "source": "generated"}
    row.data = data
    await s.commit()
    return {"pages": out["pages"]}


# ------------------------------------------------------------------ weekly schedule
def week_key(local: datetime) -> str:
    y, w, _ = local.isocalendar()
    return f"weekly:{y}-W{w:02d}"


def _hhmm(v: str) -> tuple[int, int]:
    try:
        h, m = str(v).split(":")
        return int(h), int(m)
    except (ValueError, TypeError):
        return 9, 0


def due(cfg: dict, local: datetime) -> bool:
    """True once this week's run time has passed (runs later in the week if the laptop was off)."""
    wd = int(cfg.get("weekday") or 0)
    h, m = _hhmm(cfg.get("run_time") or "09:00")
    return (local.weekday(), local.hour, local.minute) >= (wd, h, m)


async def schedule_org(org_id: uuid.UUID, at: datetime | None = None) -> AivisRun | None:
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
        if not due(cfg, local):
            return None
        key = week_key(local)
        if await s.scalar(select(AivisRun.id).where(AivisRun.run_key == key)):
            return None
        if await s.scalar(select(AivisRun.id).where(AivisRun.status.in_(("queued", "running")))):
            return None
        try:
            run = await svc.start_run(s, org_id, trigger="scheduled", run_key=key)
            await s.commit()
        except ValueError:  # no prompts or no engines yet: nothing to run
            await s.rollback()
            return None
        except IntegrityError:
            await s.rollback()
            return None
        return run


@periodic(900, "aivis_weekly")
async def aivis_weekly() -> None:
    for org_id in await org_ids_active():
        try:
            await schedule_org(org_id)
        except Exception:
            log.exception("weekly AI visibility scheduling failed for %s", org_id)
