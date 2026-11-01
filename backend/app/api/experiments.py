"""A/B tests API (under /content/experiments) and hashtag analytics (GET /analytics/hashtags)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import Experiment, Post, SocialAccount
from app.services import audit
from app.services import content as content_svc
from app.services import experiments as svc
from app.services import hashtag_stats
from app.services.platforms import PLATFORMS

router = APIRouter(prefix="/content/experiments", tags=["experiments"])
analytics_router = APIRouter(prefix="/analytics", tags=["analytics"])


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


def _aware(dt: datetime | None, ctx: Context) -> datetime | None:
    if dt is None:
        return None
    return (dt.replace(tzinfo=content_svc.org_tz(ctx.org)) if dt.tzinfo is None else dt).astimezone(UTC)


async def _exp(ctx: Context, eid: uuid.UUID) -> Experiment:
    e = await ctx.session.get(Experiment, eid)
    if e is None:
        raise _bad("A/B test not found.", 404)
    return e


async def _outs(ctx: Context, exps: list[Experiment]) -> list[dict]:
    pairs = await svc.variants_of(ctx.session, [e.id for e in exps])
    post_ids = [p.id for v in pairs.values() for _, p in v]
    metrics = await svc.latest_metrics(ctx.session, post_ids)
    aids = {p.social_account_id for v in pairs.values() for _, p in v if p.social_account_id}
    accounts = {a.id: a for a in (await ctx.session.scalars(select(SocialAccount).where(SocialAccount.id.in_(aids)))).all()} if aids else {}
    return [svc.out(e, pairs.get(e.id, []), metrics, accounts) for e in exps]


@router.get("/meta")
async def meta(ctx: Context = Depends(require("content.view"))):
    return {"metrics": svc.METRICS, "modes": svc.MODES, "default_days": svc.DEFAULT_DAYS,
            "auto_approve": content_svc.auto_approve_posts(ctx.org)}


@router.get("")
async def list_experiments(status_: str | None = Query(None, alias="status"), ctx: Context = Depends(require("content.view"))):
    q = select(Experiment).order_by(Experiment.created_at.desc()).limit(200)
    if status_:
        q = q.where(Experiment.status == status_)
    return {"items": await _outs(ctx, list((await ctx.session.scalars(q)).all()))}


@router.get("/{eid}")
async def get_experiment(eid: uuid.UUID, ctx: Context = Depends(require("content.view"))):
    return (await _outs(ctx, [await _exp(ctx, eid)]))[0]


class VariantBody(BaseModel):
    text: str = Field("", max_length=20000)
    hashtags: list[str] = []
    account_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    post_id: uuid.UUID | None = None
    kind: Literal["original", "manual", "ai"] = "manual"
    change: str | None = Field(None, max_length=300)


class CreateBody(BaseModel):
    name: str = Field("", max_length=200)
    metric: Literal["engagement_rate", "clicks", "impressions"] = "engagement_rate"
    mode: Literal["time", "accounts"] = "time"
    evaluate_after_days: int = Field(svc.DEFAULT_DAYS, ge=1, le=30)
    spacing_days: int = Field(7, ge=1, le=28)
    source_post_id: uuid.UUID | None = None
    then: Literal["draft", "review", "schedule"] = "review"
    variants: list[VariantBody] = Field(min_length=2, max_length=3)
    notes: str | None = Field(None, max_length=2000)


@router.post("", status_code=201)
async def create(body: CreateBody, ctx: Context = Depends(require("content.create"))):
    try:
        exp = await svc.create(
            ctx.session, ctx.org, ctx.user, name=body.name, metric=body.metric, mode=body.mode,
            evaluate_after_days=body.evaluate_after_days, spacing_days=body.spacing_days, then=body.then,
            source_post_id=body.source_post_id, notes=body.notes,
            variants=[svc.VariantIn(text=v.text, hashtags=v.hashtags, account_id=v.account_id,
                                    scheduled_at=_aware(v.scheduled_at, ctx), post_id=v.post_id, kind=v.kind, change=v.change)
                      for v in body.variants],
        )
    except svc.ExperimentError as e:
        await ctx.session.rollback()
        raise _bad(str(e)) from None
    audit.record(ctx.session, action="experiment.created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="experiment", entity_id=exp.id, summary=f"Started the A/B test “{exp.name}”", ip=ctx.ip)
    await ctx.session.commit()
    for _, p in (await svc.variants_of(ctx.session, [exp.id])).get(exp.id, []):
        if p.status == "in_review":
            await content_svc.notify_review(p, content_svc.org_tz(ctx.org))
    return (await _outs(ctx, [exp]))[0]


class SuggestBody(BaseModel):
    text: str | None = Field(None, max_length=20000)
    post_id: uuid.UUID | None = None
    platform: str | None = None
    account_id: uuid.UUID | None = None
    focus: Literal["hook", "cta", "both"] = "hook"
    count: int = Field(2, ge=1, le=2)


@router.post("/suggest")
async def suggest(body: SuggestBody, ctx: Context = Depends(require("content.create"))):
    """AI alternatives of a post that change only the hook and/or the call to action (no new facts)."""
    text, platform, tags = body.text, body.platform, []
    if body.post_id:
        p = await ctx.session.get(Post, body.post_id)
        if p is None:
            raise _bad("Post not found.", 404)
        text, platform, tags = text or p.text, platform or p.platform, list(p.hashtags or [])
    if body.account_id and not platform:
        a = await ctx.session.get(SocialAccount, body.account_id)
        platform = a.platform if a else None
    if platform not in PLATFORMS:
        raise _bad("Choose the account first.")
    try:
        items = await svc.suggest_variants(ctx.session, text or "", platform, focus=body.focus, count=body.count, hashtags=tags)
    except svc.ExperimentError as e:
        await ctx.session.commit()  # keep the AI usage records
        raise _bad(str(e), status.HTTP_422_UNPROCESSABLE_ENTITY) from None
    await ctx.session.commit()
    return {"variants": items}


class EvaluateBody(BaseModel):
    force: bool = False


@router.post("/{eid}/evaluate")
async def evaluate(eid: uuid.UUID, body: EvaluateBody | None = None, ctx: Context = Depends(require("content.create"))):
    exp = await _exp(ctx, eid)
    if exp.status != "running":
        raise _bad("This A/B test has already ended.", 409)
    finished = await svc.evaluate(ctx.session, exp, force=bool(body and body.force))
    await ctx.session.commit()
    if finished:
        await svc.notify_done(exp)
    return (await _outs(ctx, [exp]))[0]


@router.post("/{eid}/cancel")
async def cancel(eid: uuid.UUID, ctx: Context = Depends(require("content.create"))):
    exp = await _exp(ctx, eid)
    if exp.status != "running":
        raise _bad("This A/B test has already ended.", 409)
    exp.status, exp.ended_at = "cancelled", content_svc.now_utc()
    exp.result = {**(exp.result or {}), "state": "cancelled", "message": "Stopped by hand. The posts are kept."}
    audit.record(ctx.session, action="experiment.cancelled", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="experiment", entity_id=exp.id, summary=f"Stopped the A/B test “{exp.name}”", ip=ctx.ip)
    await ctx.session.commit()
    return (await _outs(ctx, [exp]))[0]


@router.delete("/{eid}", status_code=204)
async def delete(eid: uuid.UUID, ctx: Context = Depends(require("content.create"))):
    exp = await _exp(ctx, eid)
    audit.record(ctx.session, action="experiment.deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="experiment", entity_id=exp.id, summary=f"Deleted the A/B test “{exp.name}” (posts kept)", ip=ctx.ip)
    await ctx.session.delete(exp)
    await ctx.session.commit()


class ReuseBody(BaseModel):
    account_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None


@router.post("/{eid}/reuse-winner", status_code=201)
async def reuse_winner(eid: uuid.UUID, body: ReuseBody | None = None, ctx: Context = Depends(require("content.create"))):
    exp = await _exp(ctx, eid)
    try:
        p = await svc.reuse_winner(ctx.session, exp, ctx.user, account_id=body.account_id if body else None,
                                   scheduled_at=_aware(body.scheduled_at, ctx) if body else None)
    except svc.ExperimentError as e:
        await ctx.session.rollback()
        raise _bad(str(e)) from None
    await ctx.session.commit()
    return {"post_id": str(p.id), "message": "Created a draft from the winning variant."}


# ------------------------------------------------------------------ hashtag analytics
@analytics_router.get("/hashtags")
async def hashtags(start: date | None = None, end: date | None = None, platform: str | None = None,
                   ctx: Context = Depends(require("analytics.view"))):
    if platform and platform not in PLATFORMS:
        raise _bad("Unknown platform.")
    tz = content_svc.org_tz(ctx.org)
    s, e = hashtag_stats.period(start, end, tz)
    return await hashtag_stats.hashtag_stats(ctx.session, start=s, end=e, tz=tz, platform=platform or None)
