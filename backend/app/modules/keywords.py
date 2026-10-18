"""Keywords & rankings background work.

Job kinds
  keywords.research    {report_id}           keyword research report (provider data, or free GSC + AI fallback)
  keywords.gap         {report_id}           competitor keyword gap report
  keywords.cluster     {list_id, use_serp}   group a keyword list into topic clusters
  keywords.rank_check  {force?, ids?}        check Google positions for tracked keywords (today)

Periodic
  keyword_ranks_daily  (30 min)  start each organization's daily rank check once its local check time has passed
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, time

from sqlalchemy import select

from app.api.integrations import tester
from app.core.crypto import decrypt_json
from app.core.db import SessionLocal, set_tenant
from app.models import KeywordList, KeywordReport, Organization, ProviderConfig, TrackedKeyword
from app.services.jobs import JobContext, PermanentError, enqueue, handler, org_ids_active, periodic
from app.services.keywords import clusters, ranks
from app.services.keywords import service as svc
from app.services.keywords.provider import DataForSEO, ProviderError
from app.services.notify import notify_org

log = logging.getLogger(__name__)


async def _org(ctx: JobContext) -> Organization:
    org = await ctx.session.get(Organization, ctx.org_id)
    if org is None:
        raise PermanentError("Organization not found.")
    return org


async def _report(ctx: JobContext) -> KeywordReport:
    try:
        rid = uuid.UUID(str(ctx.payload.get("report_id")))
    except ValueError:
        raise PermanentError("Missing report.")
    rep = await ctx.session.scalar(select(KeywordReport).where(KeywordReport.id == rid))
    if rep is None:
        raise PermanentError("This report was deleted.")
    return rep


async def _run_report(ctx: JobContext, fn) -> dict:
    s = ctx.session
    org = await _org(ctx)
    rep = await _report(ctx)
    rid = rep.id
    rep.status, rep.error = "running", None
    await s.commit()
    try:
        out = await fn(s, org, rep, progress=ctx.progress)
    except (ValueError, ProviderError, svc.BudgetError) as e:
        await s.commit()  # keep the spend log of calls that did go through
        rep = await s.get(KeywordReport, rid)
        rep.status, rep.error = "failed", str(e)[:1000]
        await s.commit()
        raise PermanentError(str(e))
    except Exception as e:
        await s.rollback()
        rep = await s.get(KeywordReport, rid)
        if ctx.attempt >= 2:
            rep.status, rep.error = "failed", "Something went wrong while building the report. Try again."
        await s.commit()
        raise
    rep.rows, rep.meta, rep.status = out["rows"], out["meta"], "done"
    await s.commit()
    return {"report_id": str(rep.id), "count": len(out["rows"]), "cost_usd": out["meta"].get("cost_usd", 0),
            "estimated": out["meta"].get("estimated", False), "message": f"{len(out['rows'])} keywords."}


@handler("keywords.research")
async def h_research(ctx: JobContext) -> dict:
    return await _run_report(ctx, svc.run_research)


@handler("keywords.gap")
async def h_gap(ctx: JobContext) -> dict:
    return await _run_report(ctx, svc.run_gap)


@handler("keywords.cluster")
async def h_cluster(ctx: JobContext) -> dict:
    s = ctx.session
    org = await _org(ctx)
    try:
        lid = uuid.UUID(str(ctx.payload.get("list_id")))
    except ValueError:
        raise PermanentError("Missing keyword list.")
    kl = await s.scalar(select(KeywordList).where(KeywordList.id == lid))
    if kl is None:
        raise PermanentError("This keyword list was deleted.")
    try:
        out = await clusters.cluster_list(s, org, kl, use_serp=bool(ctx.payload.get("use_serp", True)), progress=ctx.progress)
    except ValueError as e:
        raise PermanentError(str(e))
    await s.commit()
    return {**out, "list_id": str(kl.id), "message": f"{out['clusters']} topic clusters."}


@handler("keywords.rank_check")
async def h_rank_check(ctx: JobContext) -> dict:
    s = ctx.session
    org = await _org(ctx)
    ids = [uuid.UUID(str(i)) for i in (ctx.payload.get("ids") or [])] or None
    stats = await ranks.run_checks(s, org, ids=ids, force=bool(ctx.payload.get("force")), progress=ctx.progress)
    await s.commit()
    if stats["alerts"]:
        title, body = ranks.alert_message(stats["alerts"], stats.get("depth", 100))
        try:
            await notify_org(org.id, "rank_change", title, body, link="/keywords?tab=rankings", permission="keywords.view")
        except Exception:  # noqa: BLE001
            log.exception("rank alert notification failed")
    if stats["skipped_budget"]:
        try:
            await notify_org(org.id, "rank_change", "Some rankings weren't checked today",
                             f"{stats['skipped_budget']} keywords were skipped because today's keyword data budget was reached.",
                             link="/keywords?tab=rankings", permission="keywords.manage")
        except Exception:  # noqa: BLE001
            log.exception("rank budget notification failed")
    stats["message"] = f"Checked {stats['checked']} keywords." + (f" {stats['note']}" if stats.get("note") else "")
    return stats


# ------------------------------------------------------------------ daily schedule
def _hhmm(v: str) -> time:
    try:
        h, m = str(v).split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return time(6, 0)


async def schedule_org(org_id: uuid.UUID, at: datetime | None = None) -> bool:
    """Queue today's rank check once the org's local check time has passed. Returns True when queued."""
    at = at or datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None or org.status != "active":
            return False
        row = await s.scalar(select(svc.KeywordSettings).where(svc.KeywordSettings.organization_id == org_id))
        cfg = svc.effective_config(row)
        if not cfg["rank_tracking_enabled"]:
            return False
        local = at.astimezone(svc.org_tz(org))
        if local.time() < _hhmm(cfg["check_time"]):
            return False
        today = local.date().isoformat()
        if row is not None and (row.data or {}).get("last_daily_check") == today:
            return False
        if not await s.scalar(select(TrackedKeyword.id).where(TrackedKeyword.active.is_(True)).limit(1)):
            return False
        row = row or await svc.get_settings_row(s, org_id)
        row.data = {**(row.data or {}), "last_daily_check": today}
        await enqueue(s, "keywords.rank_check", org_id, {"scheduled": True, "date": today},
                      dedupe_key=f"keywords.rank_check:{org_id}:{today}", priority=7, max_attempts=2)
        await s.commit()
        return True


@periodic(1800, "keyword_ranks_daily")
async def keyword_ranks_daily() -> None:
    for org_id in await org_ids_active():
        try:
            await schedule_org(org_id)
        except Exception:
            log.exception("daily rank check scheduling failed for %s", org_id)


# ------------------------------------------------------------------ connection test
@tester("dataforseo")
async def _test_dataforseo(cfg: ProviderConfig) -> str:
    secrets = decrypt_json(cfg.secrets_enc)
    p = DataForSEO(secrets.get("login") or "", secrets.get("password") or "", cfg.base_url)
    acc = await p.account()
    bal = acc.get("balance")
    return f"Connected as {acc.get('login') or 'your account'}." + (f" Balance: ${float(bal):.2f}." if bal is not None else "")
