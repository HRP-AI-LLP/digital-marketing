"""Analytics: social performance, Google Analytics 4, Search Console, PageSpeed, and their settings."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.deps import Context, require
from app.services import analytics, audit
from app.services.jobs import enqueue

log = logging.getLogger(__name__)
router = APIRouter(prefix="/analytics", tags=["analytics"])


class SettingsIn(BaseModel):
    ga4_property: str | None = Field(None, max_length=200)
    gsc_site: str | None = Field(None, max_length=300)


async def _connection(ctx: Context) -> dict:
    g = await analytics.google_status(ctx.session)
    return {
        **g, "website": ctx.org.website if ctx.org else None,
        "pagespeed_key": bool(await analytics.pagespeed_key(ctx.session)),
    }


def _summary_ga4(s) -> dict | None:
    if s is None:
        return None
    t = (s.data or {}).get("totals") or {}
    return {"period_start": s.period_start, "period_end": s.period_end, "updated_at": s.created_at,
            "current": t.get("current"), "previous": t.get("previous"), "deltas": t.get("deltas"),
            "conversion_metric": t.get("conversion_metric")}


def _summary_gsc(s) -> dict | None:
    if s is None:
        return None
    d = s.data or {}
    t = d.get("totals") or {}
    return {"period_start": s.period_start, "period_end": s.period_end, "updated_at": s.created_at,
            "current": t.get("current"), "previous": t.get("previous"), "deltas": t.get("deltas"),
            "opportunities": (d.get("opportunities") or [])[:5]}


def _summary_ps(s) -> dict | None:
    if s is None:
        return None
    d = s.data or {}
    return {"updated_at": s.created_at, "url": s.key,
            **{k: {"scores": (d.get(k) or {}).get("scores"), "field_overall": (d.get(k) or {}).get("field_overall")}
               for k in ("mobile", "desktop")}}


@router.get("/overview")
async def overview(ctx: Context = Depends(require("analytics.view"))):
    social = await analytics.social_performance(ctx.session, 30, ctx.org.timezone)
    last = await analytics.latest(ctx.session, "refresh")
    return {
        "connection": await _connection(ctx),
        "ga4": _summary_ga4(await analytics.latest(ctx.session, "ga4")),
        "gsc": _summary_gsc(await analytics.latest(ctx.session, "gsc")),
        "pagespeed": _summary_ps(await analytics.latest(ctx.session, "pagespeed")),
        "social": {"totals": social["totals"], "by_platform": social["by_platform"], "best_times": social["best_times"]},
        "last_refresh": {"at": last.created_at, **(last.data or {})} if last else None,
    }


@router.get("/social")
async def social(days: int = Query(30, ge=7, le=365), ctx: Context = Depends(require("analytics.view"))):
    return await analytics.social_performance(ctx.session, days, ctx.org.timezone)


@router.get("/website")
async def website(ctx: Context = Depends(require("analytics.view"))):
    return {"snapshot": analytics.snapshot_out(await analytics.latest(ctx.session, "ga4")),
            "connection": await _connection(ctx)}


@router.get("/search")
async def search(ctx: Context = Depends(require("analytics.view"))):
    return {"snapshot": analytics.snapshot_out(await analytics.latest(ctx.session, "gsc")),
            "connection": await _connection(ctx)}


@router.get("/pagespeed")
async def pagespeed(ctx: Context = Depends(require("analytics.view"))):
    return {"snapshot": analytics.snapshot_out(await analytics.latest(ctx.session, "pagespeed")),
            "connection": await _connection(ctx)}


@router.post("/refresh")
async def refresh(ctx: Context = Depends(require("analytics.view"))):
    job = await enqueue(ctx.session, "intel.analytics", ctx.org_id, {"trigger": "manual"}, created_by=ctx.user.id,
                        dedupe_key=f"intel.analytics:{ctx.org_id}")
    await ctx.session.commit()
    return {"job_id": job.id}


def _options(items, kind: str) -> list[dict]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"id": it, "label": it})
            continue
        if not isinstance(it, dict):
            continue
        if kind == "ga4":
            pid = it.get("id") or it.get("property") or it.get("name") or ""
            pid = analytics.normalize_property(str(pid)) or ""
            label = it.get("label") or it.get("displayName") or it.get("display_name") or pid
            acc = it.get("account_name") or it.get("account")
            out.append({"id": pid, "label": f"{label} ({acc})" if acc and acc not in str(label) else str(label)})
        else:
            sid = it.get("id") or it.get("siteUrl") or it.get("site_url") or it.get("url") or ""
            perm = it.get("permissionLevel") or it.get("permission")
            out.append({"id": sid, "label": f"{sid} · {perm}" if perm else sid})
    return [o for o in out if o["id"]]


@router.get("/settings")
async def get_settings_(ctx: Context = Depends(require("analytics.view"))):
    conn = await _connection(ctx)
    ga4_opts, gsc_opts, errors = [], [], []
    g = await analytics._google()
    if g is not None and conn["connected"]:
        try:
            ga4_opts = _options(await g.list_ga4_properties(ctx.session), "ga4")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Couldn't list Analytics properties: {e}")
        try:
            gsc_opts = _options(await g.list_gsc_sites(ctx.session), "gsc")
        except Exception as e:  # noqa: BLE001
            errors.append(f"Couldn't list Search Console sites: {e}")
    return {"connection": conn, "ga4_property": conn.get("ga4_property"), "gsc_site": conn.get("gsc_site"),
            "ga4_options": ga4_opts, "gsc_options": gsc_opts, "errors": errors,
            "can_manage": "integration.manage" in ctx.permissions}


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: Context = Depends(require("integration.manage"))):
    g = await analytics._google()
    account = await g.google_account(ctx.session) if g is not None else None
    if account is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connect Google first in Settings → Social accounts.")
    s = dict(account.settings or {})
    data = body.model_dump(exclude_unset=True)
    if "ga4_property" in data:
        s["ga4_property"] = analytics.normalize_property(data["ga4_property"]) or None
    if "gsc_site" in data:
        s["gsc_site"] = (data["gsc_site"] or "").strip() or None
    account.settings = s
    audit.record(ctx.session, action="analytics.settings_changed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="social_account", entity_id=account.id,
                 summary=f"Analytics property {s.get('ga4_property') or '—'}, Search Console site {s.get('gsc_site') or '—'}",
                 ip=ctx.ip)
    await ctx.session.commit()
    return {"ga4_property": s.get("ga4_property"), "gsc_site": s.get("gsc_site")}
