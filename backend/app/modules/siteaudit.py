"""Site audit background work.

Job kinds
  siteaudit.run {run_id}   crawl the website, measure speed, run every check, score and save the run

Periodic: weekly scheduled audit (per organization, on the configured weekday and local time), with a
notification when it finishes and an urgent one when the health score drops.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant
from app.models import Organization, SiteAuditRun
from app.services.jobs import JobContext, PermanentError, handler, org_ids_active, periodic
from app.services.notify import notify_org
from app.services.siteaudit import runs as svc

log = logging.getLogger(__name__)
LINK = "/site-audit"


async def _psi_key(s) -> str | None:
    try:
        from app.modules.intelligence import _pagespeed_key

        return await _pagespeed_key(s)
    except Exception:  # noqa: BLE001 — the key is optional; PageSpeed works without one at low volume
        return None


async def _notify(org_id: uuid.UUID, event: str, title: str, body: str) -> None:
    try:
        await notify_org(org_id, event, title, body, link=LINK, permission="siteaudit.view")
    except Exception:  # noqa: BLE001
        log.exception("site audit notification failed")


@handler("siteaudit.run")
async def h_run(ctx: JobContext) -> dict:
    s = ctx.session
    try:
        run_id = uuid.UUID(str(ctx.payload.get("run_id")))
    except ValueError:
        raise PermanentError("Missing audit run.")
    run = await s.get(SiteAuditRun, run_id)
    if run is None:
        raise PermanentError("The audit run was deleted.")
    org = await s.get(Organization, ctx.org_id) if ctx.org_id else None
    try:
        result = await svc.execute(s, run, org, ctx.progress, await _psi_key(s))
    except Exception as e:
        await s.rollback()
        run = await s.get(SiteAuditRun, run_id)
        if run is not None:
            run.status, run.finished_at = "failed", datetime.now(UTC)
            run.error = "The audit stopped unexpectedly. Try again; if it keeps failing, lower the page limit."
            await s.commit()
        log.exception("site audit failed")
        raise PermanentError(f"Site audit failed: {type(e).__name__}")
    if result.get("status") == "done" and ctx.org_id:
        await _after(ctx.org_id, run, result)
    elif result.get("status") == "failed":
        raise PermanentError(result.get("error") or "The audit failed.")
    return result


async def _after(org_id: uuid.UUID, run: SiteAuditRun, result: dict) -> None:
    cfg = run.config or {}
    prev = result.get("previous_score")
    score = result.get("score")
    parts = [f"{result['pages']} pages checked: {result['errors']} errors, {result['warnings']} warnings, "
             f"{result['notices']} notices."]
    if prev is not None:
        parts.append(f"{result['new_issues']} new and {result['fixed_issues']} fixed since the last audit.")
    await _notify(org_id, "siteaudit_ready", f"Site audit finished: health {score}/100", " ".join(parts))
    threshold = int(cfg.get("drop_threshold") or svc.DEFAULTS["drop_threshold"])
    if prev is not None and score is not None and prev - score >= threshold:
        await _notify(org_id, "siteaudit_drop", f"Website health dropped from {prev} to {score}",
                      f"{result['new_issues']} new issues were found. Open Site audit to see what changed.")


# ------------------------------------------------------------------ weekly schedule
def week_key(local: datetime) -> str:
    y, w, _ = local.isocalendar()
    return f"weekly:{y}-W{w:02d}"


async def schedule_org(org_id: uuid.UUID, at: datetime | None = None) -> SiteAuditRun | None:
    """Start this week's scheduled audit once the configured weekday/time has passed locally."""
    at = at or datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None or org.status != "active":
            return None
        row = await svc.get_settings_row(s)
        cfg = svc.effective_config(row)
        if not cfg["enabled"] or not (cfg.get("start_url") or org.website):
            return None
        any_run = await s.scalar(select(SiteAuditRun.id).limit(1))
        if row is None and any_run is None:
            return None  # never used: don't crawl until someone runs it or saves settings
        local = at.astimezone(svc.org_tz(org))
        if local.weekday() != cfg["weekday"] or local.time() < svc.hhmm(cfg["run_time"]):
            return None
        key = week_key(local)
        if await s.scalar(select(SiteAuditRun.id).where(SiteAuditRun.run_key == key)):
            return None
        if await s.scalar(select(SiteAuditRun.id).where(SiteAuditRun.status.in_(("queued", "running"))).limit(1)):
            return None
        run = await svc.start_run(s, org_id, trigger="scheduled", run_key=key)
        await s.commit()
        return run


@periodic(900, "siteaudit_weekly")
async def siteaudit_weekly() -> None:
    for org_id in await org_ids_active():
        try:
            await schedule_org(org_id)
        except Exception:  # noqa: BLE001
            log.exception("weekly site audit scheduling failed for %s", org_id)
