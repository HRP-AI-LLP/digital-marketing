"""Site audit API: overview, runs (run now, list, detail), issues with affected URLs, pages explorer,
run comparison, CSV export, settings and the check catalog."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import asc, desc, func, or_, select

from app.core.deps import Context, require
from app.models import Job, SiteAuditIssue, SiteAuditPage, SiteAuditRun, SiteAuditSettings
from app.services import audit, crawler
from app.services.siteaudit import checks
from app.services.siteaudit import runs as svc

router = APIRouter(prefix="/site-audit", tags=["site-audit"])

VIEW = "siteaudit.view"
MANAGE = "siteaudit.manage"
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


async def _run(ctx: Context, run_id: uuid.UUID) -> SiteAuditRun:
    r = await ctx.session.scalar(select(SiteAuditRun).where(SiteAuditRun.id == run_id))
    if r is None:
        raise _bad("Audit not found.", 404)
    return r


async def _progress(ctx: Context, r: SiteAuditRun) -> str | None:
    if not r.job_id:
        return None
    return await ctx.session.scalar(select(Job.progress).where(Job.id == r.job_id))


def _ai_card(site: dict) -> dict:
    access = site.get("ai_access") or []
    return {
        "bots": access,
        "blocked": sum(1 for a in access if a.get("status") == "blocked"),
        "allowed": sum(1 for a in access if a.get("status") == "allowed"),
        "partial": sum(1 for a in access if a.get("status") == "partial"),
        "llms_txt": site.get("llms_txt") or {},
        "robots_found": bool((site.get("robots") or {}).get("found")),
    }


def run_full(r: SiteAuditRun) -> dict:
    site = r.site or {}
    robots = dict(site.get("robots") or {})
    robots.pop("text", None)
    groups = svc.issue_groups(r)
    cats: dict[str, dict] = {}
    for g in groups:
        c = cats.setdefault(g["category"], {"id": g["category"], "label": g["category_label"], "error": 0, "warning": 0,
                                            "notice": 0, "checks": 0})
        if g["count"]:
            c[g["severity"]] += g["count"]
            c["checks"] += 1
    return {
        **svc.run_brief(r), "stats": r.stats or {}, "config": r.config or {},
        "issues": groups, "categories": sorted(cats.values(), key=lambda c: (-c["error"], -c["warning"], -c["notice"])),
        "site": {**site, "robots": robots}, "ai": _ai_card(site),
        "performance": {"summary": (r.performance or {}).get("summary") or {}, "pages": (r.performance or {}).get("pages") or [],
                        "quota_reached": (r.performance or {}).get("quota_reached", False),
                        "with_key": (r.performance or {}).get("with_key", False), "error": (r.performance or {}).get("error")},
    }


def _next_run(cfg: dict, org, last_key: str | None) -> str | None:
    from app.modules.siteaudit import week_key

    if not cfg["enabled"]:
        return None
    tz = svc.org_tz(org)
    local = datetime.now(UTC).astimezone(tz)
    days = (cfg["weekday"] - local.weekday()) % 7
    at = datetime.combine(local.date() + timedelta(days=days), svc.hhmm(cfg["run_time"]), tzinfo=tz)
    if at <= local or week_key(at) == last_key:
        at += timedelta(days=7)
    return at.astimezone(UTC).isoformat()


# ------------------------------------------------------------------ overview
@router.get("/overview")
async def overview(ctx: Context = Depends(require(VIEW))):
    s = ctx.session
    row = await svc.get_settings_row(s)
    cfg = svc.effective_config(row)
    latest = (await s.scalars(select(SiteAuditRun).where(SiteAuditRun.status == "done")
                              .order_by(SiteAuditRun.created_at.desc()).limit(1))).first()
    active = (await s.scalars(select(SiteAuditRun).where(SiteAuditRun.status.in_(("queued", "running")))
                              .order_by(SiteAuditRun.created_at.desc()).limit(1))).first()
    last_failed = (await s.scalars(select(SiteAuditRun).order_by(SiteAuditRun.created_at.desc()).limit(1))).first()
    trend_rows = (await s.scalars(select(SiteAuditRun).where(SiteAuditRun.status == "done")
                                  .order_by(SiteAuditRun.created_at.desc()).limit(20))).all()
    last_sched = await s.scalar(select(SiteAuditRun.run_key).where(SiteAuditRun.run_key.is_not(None))
                                .order_by(SiteAuditRun.created_at.desc()).limit(1))
    active_out = None
    if active:
        active_out = {**svc.run_brief(active), "progress": await _progress(ctx, active)}
    return {
        "website": cfg.get("start_url") or (ctx.org.website if ctx.org else None),
        "settings": _settings_out(cfg), "next_run_at": _next_run(cfg, ctx.org, last_sched),
        "latest": run_full(latest) if latest else None, "active": active_out,
        "last_failed": svc.run_brief(last_failed) if last_failed and last_failed.status == "failed" else None,
        "trend": [svc.run_brief(r) for r in reversed(trend_rows)],
        "timezone": ctx.org.timezone if ctx.org else "Asia/Kolkata",
    }


# ------------------------------------------------------------------ runs
@router.post("/runs", status_code=202)
async def run_now(ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    active = (await s.scalars(select(SiteAuditRun).where(SiteAuditRun.status.in_(("queued", "running"))).limit(1))).first()
    if active is not None:
        return {"run": svc.run_brief(active), "job_id": active.job_id, "already_running": True}
    cfg = svc.effective_config(await svc.get_settings_row(s))
    if not (cfg.get("start_url") or (ctx.org.website if ctx.org else None)):
        raise _bad(svc.NO_WEBSITE)
    run = await svc.start_run(s, ctx.org_id, trigger="manual", created_by=ctx.user.id)
    audit.record(s, action="siteaudit.run", organization_id=ctx.org_id, actor=ctx.user, entity_type="site_audit_run",
                 entity_id=run.id, summary="Started a site audit", ip=ctx.ip)
    await s.commit()
    return {"run": svc.run_brief(run), "job_id": run.job_id, "already_running": False}


@router.get("/runs")
async def list_runs(limit: int = Query(30, ge=1, le=100), ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(SiteAuditRun).order_by(SiteAuditRun.created_at.desc()).limit(limit))).all()
    return [svc.run_brief(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    r = await _run(ctx, run_id)
    out = run_full(r)
    if r.status in ("queued", "running"):
        out["progress"] = await _progress(ctx, r)
    return out


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    r = await _run(ctx, run_id)
    if r.status in ("queued", "running"):
        raise _bad("Wait for the audit to finish before deleting it.")
    await ctx.session.delete(r)
    audit.record(ctx.session, action="siteaudit.delete", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="site_audit_run", entity_id=r.id, summary="Deleted a site audit", ip=ctx.ip)
    await ctx.session.commit()


@router.get("/runs/{run_id}/issues/{check_id}")
async def issue_urls(run_id: uuid.UUID, check_id: str, q: str | None = Query(None, max_length=200),
                     only_new: bool = False, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                     ctx: Context = Depends(require(VIEW))):
    chk = checks.CHECKS.get(check_id)
    if chk is None:
        raise _bad("Unknown check.", 404)
    r = await _run(ctx, run_id)
    conds = [SiteAuditIssue.run_id == r.id, SiteAuditIssue.check_id == check_id]
    if q:
        conds.append(SiteAuditIssue.url.ilike(f"%{q.strip()}%"))
    if only_new:
        conds.append(SiteAuditIssue.is_new.is_(True))
    total = await ctx.session.scalar(select(func.count()).select_from(SiteAuditIssue).where(*conds)) or 0
    rows = (await ctx.session.scalars(select(SiteAuditIssue).where(*conds).order_by(SiteAuditIssue.url)
                                      .offset(offset).limit(limit))).all()
    page_ids = dict((await ctx.session.execute(select(SiteAuditPage.url, SiteAuditPage.id).where(
        SiteAuditPage.run_id == r.id, SiteAuditPage.url.in_([i.url for i in rows])))).all()) if rows else {}
    fixed: list[str] = []
    prev_id = (r.stats or {}).get("previous_run_id")
    if prev_id and offset == 0:
        prev_urls = set((await ctx.session.scalars(select(SiteAuditIssue.url).where(
            SiteAuditIssue.run_id == uuid.UUID(prev_id), SiteAuditIssue.check_id == check_id))).all())
        cur = set((await ctx.session.scalars(select(SiteAuditIssue.url).where(
            SiteAuditIssue.run_id == r.id, SiteAuditIssue.check_id == check_id))).all())
        fixed = sorted(prev_urls - cur)[:100]
    return {
        "check": {**chk.out(), **(r.summary or {}).get(check_id, {"count": 0, "new": 0, "fixed": 0})},
        "total": total, "fixed_urls": fixed,
        "items": [{"id": i.id, "url": i.url, "detail": i.detail or {}, "is_new": i.is_new, "page_id": page_ids.get(i.url)}
                  for i in rows],
    }


SORTS = {"url": SiteAuditPage.url, "status": SiteAuditPage.status, "depth": SiteAuditPage.depth,
         "inlinks": SiteAuditPage.inlinks, "words": SiteAuditPage.word_count, "response": SiteAuditPage.response_ms,
         "size": SiteAuditPage.size_bytes, "issues": SiteAuditPage.errors, "title": SiteAuditPage.title}


@router.get("/runs/{run_id}/pages")
async def pages(run_id: uuid.UUID, q: str | None = Query(None, max_length=200),
                status_: Literal["all", "2xx", "3xx", "4xx", "5xx", "failed"] = Query("all", alias="status"),
                indexable: bool | None = None, has: Literal["any", "errors", "warnings", "notices", "none"] = "any",
                check: str | None = None, sort: str = "issues", order: Literal["asc", "desc"] = "desc",
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), ctx: Context = Depends(require(VIEW))):
    r = await _run(ctx, run_id)
    conds = [SiteAuditPage.run_id == r.id]
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(SiteAuditPage.url.ilike(like), SiteAuditPage.title.ilike(like)))
    if status_ == "3xx":
        conds.append(SiteAuditPage.final_url != SiteAuditPage.url)
    elif status_ == "failed":
        conds.append(SiteAuditPage.status == 0)
    elif status_ != "all":
        lo = int(status_[0]) * 100
        conds += [SiteAuditPage.status >= lo, SiteAuditPage.status < lo + 100]
        if status_ == "2xx":
            conds.append(or_(SiteAuditPage.final_url == SiteAuditPage.url, SiteAuditPage.final_url.is_(None)))
    if indexable is not None:
        conds.append(SiteAuditPage.indexable.is_(indexable))
    if has == "errors":
        conds.append(SiteAuditPage.errors > 0)
    elif has == "warnings":
        conds.append(SiteAuditPage.warnings > 0)
    elif has == "notices":
        conds.append(SiteAuditPage.notices > 0)
    elif has == "none":
        conds += [SiteAuditPage.errors == 0, SiteAuditPage.warnings == 0, SiteAuditPage.notices == 0]
    if check:
        conds.append(SiteAuditPage.url.in_(select(SiteAuditIssue.url).where(
            SiteAuditIssue.run_id == r.id, SiteAuditIssue.check_id == check)))
    col = SORTS.get(sort, SiteAuditPage.errors)
    direction = desc if order == "desc" else asc
    extra = [desc(SiteAuditPage.warnings), desc(SiteAuditPage.notices)] if sort == "issues" else []
    total = await ctx.session.scalar(select(func.count()).select_from(SiteAuditPage).where(*conds)) or 0
    rows = (await ctx.session.scalars(select(SiteAuditPage).where(*conds)
                                      .order_by(direction(col).nulls_last(), *extra, SiteAuditPage.url)
                                      .offset(offset).limit(limit))).all()
    return {"total": total, "items": [page_row(p) for p in rows]}


def page_row(p: SiteAuditPage) -> dict:
    d = p.data or {}
    return {"id": p.id, "url": p.url, "final_url": p.final_url, "status": p.status, "redirect": bool(d.get("chain")),
            "title": p.title, "depth": p.depth, "inlinks": p.inlinks, "words": p.word_count, "response_ms": p.response_ms,
            "size_bytes": p.size_bytes, "indexable": p.indexable, "in_sitemap": p.in_sitemap, "errors": p.errors,
            "warnings": p.warnings, "notices": p.notices, "content_type": p.content_type}


@router.get("/runs/{run_id}/pages/{page_id}")
async def page_detail(run_id: uuid.UUID, page_id: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    r = await _run(ctx, run_id)
    p = await ctx.session.scalar(select(SiteAuditPage).where(SiteAuditPage.id == page_id, SiteAuditPage.run_id == r.id))
    if p is None:
        raise _bad("Page not found.", 404)
    rows = (await ctx.session.scalars(select(SiteAuditIssue).where(SiteAuditIssue.run_id == r.id,
                                                                    SiteAuditIssue.url == p.url))).all()
    order = {"error": 0, "warning": 1, "notice": 2}
    issues = sorted(({**checks.CHECKS[i.check_id].out(), "detail": i.detail or {}, "is_new": i.is_new}
                     for i in rows if i.check_id in checks.CHECKS), key=lambda x: (order[x["severity"]], x["title"]))
    speed = next((x for x in (r.performance or {}).get("pages", []) if x.get("url") == p.url), None)
    return {**page_row(p), "data": p.data or {}, "issues": issues, "pagespeed": speed}


@router.get("/compare")
async def compare(a: uuid.UUID, b: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    ra, rb = await _run(ctx, a), await _run(ctx, b)
    if ra.status != "done" or rb.status != "done":
        raise _bad("Only finished audits can be compared.")
    if ra.created_at > rb.created_at:
        ra, rb = rb, ra
    return await svc.compare(ctx.session, ra, rb)


@router.get("/runs/{run_id}/export")
async def export(run_id: uuid.UUID, kind: Literal["issues", "pages"] = "issues", as_json: bool = False,
                 ctx: Context = Depends(require(VIEW))):
    r = await _run(ctx, run_id)
    if r.status != "done":
        raise _bad("The audit hasn't finished yet.")
    content = await svc.export_csv(ctx.session, r, kind)
    filename = f"site-audit-{kind}-{(r.finished_at or r.created_at).date().isoformat()}.csv"
    if as_json:
        return {"filename": filename, "media_type": "text/csv", "content": content}
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ settings & catalog
def _settings_out(cfg: dict) -> dict:
    keys = ("enabled", "start_url", "max_pages", "max_depth", "include", "exclude", "user_agent", "psi_pages", "weekday",
            "run_time", "drop_threshold", "check_external")
    return {k: cfg.get(k) for k in keys}


class SettingsIn(BaseModel):
    enabled: bool | None = None
    start_url: str | None = Field(None, max_length=500)
    max_pages: int | None = Field(None, ge=1, le=2000)
    max_depth: int | None = Field(None, ge=1, le=50)
    include: list[str] | None = Field(None, max_length=30)
    exclude: list[str] | None = Field(None, max_length=30)
    user_agent: str | None = Field(None, max_length=200)
    psi_pages: int | None = Field(None, ge=0, le=25)
    weekday: int | None = Field(None, ge=0, le=6)
    run_time: str | None = None
    drop_threshold: int | None = Field(None, ge=1, le=50)
    check_external: bool | None = None

    @field_validator("run_time")
    @classmethod
    def _time(cls, v):
        if v is not None and not HHMM.match(v):
            raise ValueError("Use HH:MM, for example 06:00")
        return v

    @field_validator("include", "exclude")
    @classmethod
    def _patterns(cls, v):
        if v is None:
            return v
        return [x.strip()[:200] for x in v if x and x.strip()]


@router.get("/settings")
async def get_settings(ctx: Context = Depends(require(VIEW))):
    cfg = svc.effective_config(await svc.get_settings_row(ctx.session))
    return {"settings": _settings_out(cfg), "website": ctx.org.website if ctx.org else None, "weekdays": svc.WEEKDAYS,
            "default_user_agent": crawler.USER_AGENT}


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    row = await svc.get_settings_row(s)
    if row is None:
        row = SiteAuditSettings(organization_id=ctx.org_id, enabled=True, config={})
        s.add(row)
    data = body.model_dump(exclude_unset=True)
    if "start_url" in data and data["start_url"]:
        try:
            data["start_url"] = await crawler.check_public_url(data["start_url"])
        except crawler.UnsafeURLError as e:
            raise _bad(str(e))
    if "enabled" in data:
        row.enabled = bool(data.pop("enabled"))
    row.config = {**(row.config or {}), **data}
    await s.flush()
    audit.record(s, action="siteaudit.settings", organization_id=ctx.org_id, actor=ctx.user, entity_type="site_audit_settings",
                 entity_id=row.id, summary="Changed site audit settings", ip=ctx.ip)
    await s.commit()
    return {"settings": _settings_out(svc.effective_config(row))}


@router.get("/checks")
async def check_catalog(ctx: Context = Depends(require(VIEW))):
    return {"checks": checks.catalog(), "categories": checks.CATEGORIES}
