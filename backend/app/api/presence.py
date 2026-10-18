"""Social presence audit API: latest run with profiles, scores and suggestions; history; run now; mark suggestions done."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import Job, PresenceRun, PresenceSuggestion, ProviderConfig
from app.services import audit, presence
from app.services.jobs import enqueue

router = APIRouter(prefix="/presence", tags=["presence"])
VIEW, MANAGE = "presence.view", "presence.manage"


async def _running(ctx: Context) -> dict | None:
    j = await ctx.session.scalar(select(Job).where(Job.kind == "presence.audit", Job.status.in_(("queued", "running")))
                                 .order_by(Job.created_at.desc()).limit(1))
    return {"id": str(j.id), "status": j.status, "progress": j.progress} if j else None


async def _sources(ctx: Context) -> dict:
    rows = (await ctx.session.scalars(select(ProviderConfig.provider).where(
        ProviderConfig.provider.in_(("youtube_data", "x_api", "github_api")), ProviderConfig.enabled.is_(True)))).all()
    return {p: p in rows for p in ("youtube_data", "x_api", "github_api")}


@router.get("")
async def get_presence(ctx: Context = Depends(require(VIEW))):
    run = await presence.latest(ctx.session)
    sugs = []
    if run is not None:
        sugs = [presence.suggestion_out(s) for s in (await ctx.session.scalars(
            select(PresenceSuggestion).where(PresenceSuggestion.run_id == run.id).order_by(PresenceSuggestion.rank))).all()]
    org_links = {k: v for k, v in (ctx.org.social_links or {}).items() if v}
    return {
        "run": presence.run_out(run) if run else None,
        "suggestions": sugs,
        "history": await presence.history(ctx.session),
        "running_job": await _running(ctx),
        "inputs": {"organization_links": org_links, "website": ctx.org.website,
                   "missing_org_links": [k for k in presence.SOCIAL_KEYS if not org_links.get(k)],
                   "optional_sources": await _sources(ctx)},
        "can_manage": MANAGE in ctx.permissions,
    }


@router.post("/run", status_code=202)
async def run_now(ctx: Context = Depends(require(MANAGE))):
    job = await enqueue(ctx.session, "presence.audit", ctx.org_id, {"trigger": "manual", "created_by": str(ctx.user.id)},
                        created_by=ctx.user.id, dedupe_key=f"presence.audit:{ctx.org_id}", max_attempts=1)
    await ctx.session.commit()
    return {"job_id": job.id}


@router.get("/runs")
async def list_runs(limit: int = Query(52, ge=1, le=200), ctx: Context = Depends(require(VIEW))):
    return await presence.history(ctx.session, limit)


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    run = await ctx.session.scalar(select(PresenceRun).where(PresenceRun.id == run_id))
    if run is None:
        raise HTTPException(404, "That check wasn't found.")
    sugs = (await ctx.session.scalars(select(PresenceSuggestion).where(PresenceSuggestion.run_id == run.id)
                                      .order_by(PresenceSuggestion.rank))).all()
    return {"run": presence.run_out(run), "suggestions": [presence.suggestion_out(s) for s in sugs]}


class SuggestionIn(BaseModel):
    status: Literal["open", "done"]


@router.patch("/suggestions/{sid}")
async def set_suggestion(sid: uuid.UUID, body: SuggestionIn, ctx: Context = Depends(require(MANAGE))):
    s = await ctx.session.scalar(select(PresenceSuggestion).where(PresenceSuggestion.id == sid))
    if s is None:
        raise HTTPException(404, "That suggestion wasn't found.")
    s.status, s.status_changed_at, s.status_changed_by = body.status, datetime.now(UTC), ctx.user.id
    if body.status == "done":
        s.reopened = False
    audit.record(ctx.session, action="presence.suggestion", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="presence_suggestion", entity_id=s.id, summary=f"Marked “{s.title[:120]}” as {body.status}",
                 ip=ctx.ip)
    await ctx.session.commit()
    return presence.suggestion_out(s)
