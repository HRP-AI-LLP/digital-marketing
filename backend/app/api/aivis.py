"""AI search visibility API: tracked prompts, runs, answers, competitors, recommendations, settings and llms.txt."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.core.deps import Context, require
from app.models import AivisAnswer, AivisPrompt, AivisRun
from app.modules.aivis import _hhmm, week_key
from app.services import aivis as svc
from app.services import aivis_engines as engines_svc
from app.services import audit, llms_txt
from app.services.jobs import enqueue

router = APIRouter(prefix="/aivis", tags=["ai-visibility"])

VIEW = "aivis.view"
MANAGE = "aivis.manage"
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


# ------------------------------------------------------------------ serializers
def run_out(r: AivisRun, full: bool = False) -> dict:
    m = r.metrics or {}
    out = {
        "id": r.id, "trigger": r.trigger, "status": r.status, "job_id": r.job_id, "created_at": r.created_at,
        "started_at": r.started_at, "finished_at": r.finished_at, "error": r.error,
        "engines": [{"key": e.get("key"), "label": e.get("label"), "web": e.get("web"), "cost": e.get("cost", "free")}
                    for e in r.engines or []],
        "summary": {k: m.get(k) for k in ("visibility", "share_of_voice", "avg_position", "answers", "failed",
                                          "mentioned", "prompts", "missed_count")},
        "last_step": (r.steps or [None])[-1],
    }
    if full:
        out["metrics"] = m
        out["recommendations"] = r.recommendations or []
        out["steps"] = r.steps or []
    return out


def prompt_out(p: AivisPrompt) -> dict:
    return {"id": p.id, "text": p.text, "topic": p.topic, "source": p.source, "enabled": p.enabled,
            "reason": (p.data or {}).get("reason"), "created_at": p.created_at}


def answer_out(a: AivisAnswer, full: bool = False) -> dict:
    out = {
        "id": a.id, "run_id": a.run_id, "prompt_id": a.prompt_id, "prompt_text": a.prompt_text, "topic": a.topic,
        "engine_key": a.engine_key, "engine_label": a.engine_label, "provider": a.provider, "model": a.model, "web": a.web,
        "status": a.status, "mentioned": a.mentioned, "position": a.position, "sentiment": a.sentiment,
        "competitors": a.competitors or [], "own_cited": a.own_cited, "error": a.error,
        "citations_count": len(a.citations or []), "created_at": a.created_at,
    }
    if full:
        out.update(answer=a.answer, citations=a.citations or [], brands=a.brands or [], analysis=a.analysis or {},
                   input_tokens=a.input_tokens, output_tokens=a.output_tokens)
    return out


async def _latest_done(ctx: Context) -> AivisRun | None:
    return (await ctx.session.scalars(select(AivisRun).where(AivisRun.status.in_(("done", "partial")))
                                      .order_by(AivisRun.created_at.desc()).limit(1))).first()


async def _run(ctx: Context, rid: uuid.UUID | None) -> AivisRun | None:
    if rid is None:
        return await _latest_done(ctx)
    r = await ctx.session.scalar(select(AivisRun).where(AivisRun.id == rid))
    if r is None:
        raise _bad("Run not found.", 404)
    return r


def _next_run(cfg: dict, org, now: datetime, this_week_done: bool) -> str | None:
    if not cfg["enabled"]:
        return None
    tz = svc.org_tz(org)
    local = now.astimezone(tz)
    h, m = _hhmm(cfg["run_time"])
    monday = (local - timedelta(days=local.weekday())).replace(hour=h, minute=m, second=0, microsecond=0)
    at = monday + timedelta(days=int(cfg["weekday"]))
    if this_week_done:
        at += timedelta(days=7)
    elif at < local:
        at = local  # due: the scheduler picks it up within 15 minutes
    return at.astimezone(UTC).isoformat()


# ------------------------------------------------------------------ overview
@router.get("/overview")
async def overview(ctx: Context = Depends(require(VIEW))):
    s = ctx.session
    row = await svc.get_settings_row(s, ctx.org_id)
    cfg = svc.effective_config(row)
    latest = await _latest_done(ctx)
    active = (await s.scalars(select(AivisRun).where(AivisRun.status.in_(("queued", "running")))
                              .order_by(AivisRun.created_at.desc()).limit(1))).first()
    last_any = (await s.scalars(select(AivisRun).order_by(AivisRun.created_at.desc()).limit(1))).first()
    runs = (await s.scalars(select(AivisRun).where(AivisRun.status.in_(("done", "partial")))
                            .order_by(AivisRun.created_at.desc()).limit(26))).all()
    trend = [{"run_id": r.id, "at": r.created_at, "visibility": (r.metrics or {}).get("visibility"),
              "share_of_voice": (r.metrics or {}).get("share_of_voice"), "avg_position": (r.metrics or {}).get("avg_position"),
              "mentioned": (r.metrics or {}).get("mentioned"), "answers": (r.metrics or {}).get("answers")}
             for r in reversed(runs)]
    prev = runs[1] if len(runs) > 1 else None
    engines = await engines_svc.resolve(s, cfg["engines"])
    prompts = await s.scalar(select(func.count()).select_from(AivisPrompt)) or 0
    enabled = await s.scalar(select(func.count()).select_from(AivisPrompt).where(AivisPrompt.enabled.is_(True))) or 0
    local = datetime.now(UTC).astimezone(svc.org_tz(ctx.org))
    this_week = bool(await s.scalar(select(AivisRun.id).where(AivisRun.run_key == week_key(local))))
    return {
        "settings": {k: cfg[k] for k in ("enabled", "weekday", "run_time")},
        "weekday_label": svc.WEEKDAYS[int(cfg["weekday"]) % 7], "timezone": ctx.org.timezone if ctx.org else "Asia/Kolkata",
        "next_run_at": _next_run(cfg, ctx.org, datetime.now(UTC), this_week),
        "latest": run_out(latest, full=True) if latest else None,
        "previous": {"visibility": (prev.metrics or {}).get("visibility"), "share_of_voice": (prev.metrics or {}).get("share_of_voice"),
                     "avg_position": (prev.metrics or {}).get("avg_position")} if prev else None,
        "active_run": run_out(active, full=True) if active else None,
        "last_run": run_out(last_any) if last_any else None,
        "trend": trend,
        "engines": [{"key": e.key, "label": e.label, "web": e.web, "cost": e.cost} for e in engines],
        "engines_auto": not cfg["engines"],
        "has_ai": bool(await engines_svc.text_providers(s)),
        "paid_hints": await engines_svc.paid_hints(s),
        "prompts": {"total": prompts, "enabled": enabled, "max": svc.MAX_PROMPTS},
    }


# ------------------------------------------------------------------ runs
@router.post("/runs", status_code=202)
async def run_now(ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    if await s.scalar(select(AivisRun.id).where(AivisRun.status.in_(("queued", "running")))):
        raise _bad("A visibility check is already running. Wait for it to finish.", 409)
    try:
        run = await svc.start_run(s, ctx.org_id, trigger="manual", created_by=ctx.user.id)
    except ValueError as e:
        raise _bad(str(e))
    audit.record(s, action="aivis.run", organization_id=ctx.org_id, actor=ctx.user, entity_type="aivis_run",
                 entity_id=run.id, summary="Started an AI visibility check")
    await s.commit()
    return {"run": run_out(run), "job_id": run.job_id}


@router.get("/runs")
async def list_runs(limit: int = Query(30, ge=1, le=100), ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(AivisRun).order_by(AivisRun.created_at.desc()).limit(limit))).all()
    return [run_out(r) for r in rows]


@router.get("/runs/{rid}")
async def get_run(rid: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    return run_out(await _run(ctx, rid), full=True)


@router.post("/runs/{rid}/recommendations", status_code=202)
async def refresh_recommendations(rid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    run = await _run(ctx, rid)
    if run.status not in ("done", "partial"):
        raise _bad("Recommendations need a finished run.")
    job = await enqueue(ctx.session, "aivis.recommend", ctx.org_id, {"run_id": str(run.id)},
                        dedupe_key=f"aivis.recommend:{run.id}", priority=4, max_attempts=1, created_by=ctx.user.id)
    await ctx.session.commit()
    return {"job_id": job.id}


# ------------------------------------------------------------------ prompts
class PromptIn(BaseModel):
    text: str = Field(min_length=3, max_length=300)
    topic: str | None = Field(None, max_length=60)
    enabled: bool = True
    source: str = "manual"
    reason: str | None = Field(None, max_length=300)

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        v = svc.clean_prompt(v)
        if len(v) < 3:
            raise ValueError("Write the question in a few words.")
        return v

    @field_validator("source")
    @classmethod
    def _src(cls, v: str) -> str:
        return v if v in ("manual", "ai") else "manual"


class PromptPatch(BaseModel):
    text: str | None = Field(None, min_length=3, max_length=300)
    topic: str | None = Field(None, max_length=60)
    enabled: bool | None = None


class PromptsBulk(BaseModel):
    prompts: list[PromptIn] = Field(min_length=1, max_length=50)


@router.get("/prompts")
async def list_prompts(run_id: uuid.UUID | None = None, ctx: Context = Depends(require(VIEW))):
    s = ctx.session
    prompts = (await s.scalars(select(AivisPrompt).order_by(AivisPrompt.topic, AivisPrompt.created_at))).all()
    run = await _run(ctx, run_id)
    results: dict[str, list[dict]] = defaultdict(list)
    if run is not None:
        for a in await svc.answers_of(s, run.id):
            results[str(a.prompt_id)].append({"answer_id": a.id, "engine_key": a.engine_key, "engine_label": a.engine_label,
                                              "status": a.status, "mentioned": a.mentioned, "position": a.position,
                                              "sentiment": a.sentiment, "competitors": a.competitors or []})
    return {
        "run_id": run.id if run else None,
        "engines": [{"key": e.get("key"), "label": e.get("label"), "web": e.get("web"), "cost": e.get("cost", "free")}
                    for e in (run.engines if run else [])],
        "max": svc.MAX_PROMPTS,
        "prompts": [{**prompt_out(p), "results": results.get(str(p.id), [])} for p in prompts],
    }


async def _count(ctx: Context) -> int:
    return await ctx.session.scalar(select(func.count()).select_from(AivisPrompt)) or 0


async def _existing_keys(ctx: Context) -> set[str]:
    return {svc.prompt_key(t) for t in (await ctx.session.scalars(select(AivisPrompt.text))).all()}


@router.post("/prompts", status_code=201)
async def create_prompt(body: PromptIn, ctx: Context = Depends(require(MANAGE))):
    if await _count(ctx) >= svc.MAX_PROMPTS:
        raise _bad(f"You can track up to {svc.MAX_PROMPTS} prompts. Delete one to add another.")
    if svc.prompt_key(body.text) in await _existing_keys(ctx):
        raise _bad("You're already tracking this prompt.", 409)
    p = AivisPrompt(organization_id=ctx.org_id, text=body.text, topic=svc.clean_topic(body.topic), source=body.source,
                    enabled=body.enabled, data={"reason": body.reason} if body.reason else {})
    ctx.session.add(p)
    await ctx.session.flush()
    audit.record(ctx.session, action="aivis.prompt.create", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="aivis_prompt", entity_id=p.id, summary=body.text[:200])
    await ctx.session.commit()
    return prompt_out(p)


@router.post("/prompts/bulk", status_code=201)
async def create_prompts(body: PromptsBulk, ctx: Context = Depends(require(MANAGE))):
    have = await _existing_keys(ctx)
    room = svc.MAX_PROMPTS - await _count(ctx)
    added, skipped = [], 0
    for item in body.prompts:
        k = svc.prompt_key(item.text)
        if k in have or len(added) >= room:
            skipped += 1
            continue
        have.add(k)
        p = AivisPrompt(organization_id=ctx.org_id, text=item.text, topic=svc.clean_topic(item.topic), source=item.source,
                        enabled=item.enabled, data={"reason": item.reason} if item.reason else {})
        ctx.session.add(p)
        added.append(p)
    await ctx.session.flush()
    if added:
        audit.record(ctx.session, action="aivis.prompt.create", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="aivis_prompt", summary=f"Added {len(added)} prompts")
    await ctx.session.commit()
    return {"added": [prompt_out(p) for p in added], "skipped": skipped}


async def _prompt(ctx: Context, pid: uuid.UUID) -> AivisPrompt:
    p = await ctx.session.scalar(select(AivisPrompt).where(AivisPrompt.id == pid))
    if p is None:
        raise _bad("Prompt not found.", 404)
    return p


@router.patch("/prompts/{pid}")
async def update_prompt(pid: uuid.UUID, body: PromptPatch, ctx: Context = Depends(require(MANAGE))):
    p = await _prompt(ctx, pid)
    if body.text is not None:
        text = svc.clean_prompt(body.text)
        if svc.prompt_key(text) != svc.prompt_key(p.text) and svc.prompt_key(text) in await _existing_keys(ctx):
            raise _bad("You're already tracking this prompt.", 409)
        p.text = text
    if body.topic is not None:
        p.topic = svc.clean_topic(body.topic)
    if body.enabled is not None:
        p.enabled = body.enabled
    await ctx.session.commit()
    return prompt_out(p)


@router.delete("/prompts/{pid}", status_code=204)
async def delete_prompt(pid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    p = await _prompt(ctx, pid)
    audit.record(ctx.session, action="aivis.prompt.delete", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="aivis_prompt", entity_id=p.id, summary=p.text[:200])
    await ctx.session.delete(p)
    await ctx.session.commit()


class SuggestIn(BaseModel):
    count: int = Field(15, ge=3, le=25)


@router.post("/prompts/suggest", status_code=202)
async def suggest(body: SuggestIn | None = None, ctx: Context = Depends(require(MANAGE))):
    if not await engines_svc.text_providers(ctx.session):
        raise _bad(svc.NO_AI)
    job = await enqueue(ctx.session, "aivis.suggest", ctx.org_id, {"count": (body or SuggestIn()).count},
                        priority=3, max_attempts=1, created_by=ctx.user.id)
    await ctx.session.commit()
    return {"job_id": job.id}


# ------------------------------------------------------------------ answers
@router.get("/answers")
async def list_answers(run_id: uuid.UUID | None = None, prompt_id: uuid.UUID | None = None, engine: str | None = None,
                       mentioned: bool | None = None, ctx: Context = Depends(require(VIEW))):
    run = await _run(ctx, run_id)
    if run is None:
        return []
    q = select(AivisAnswer).where(AivisAnswer.run_id == run.id)
    if prompt_id:
        q = q.where(AivisAnswer.prompt_id == prompt_id)
    if engine:
        q = q.where(AivisAnswer.engine_key == engine)
    if mentioned is not None:
        q = q.where(AivisAnswer.mentioned.is_(mentioned))
    rows = (await ctx.session.scalars(q.order_by(AivisAnswer.topic, AivisAnswer.prompt_text, AivisAnswer.engine_label))).all()
    return [answer_out(a) for a in rows]


@router.get("/answers/{aid}")
async def get_answer(aid: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    a = await ctx.session.scalar(select(AivisAnswer).where(AivisAnswer.id == aid))
    if a is None:
        raise _bad("Answer not found.", 404)
    return answer_out(a, full=True)


@router.get("/prompts/{pid}/history")
async def prompt_history(pid: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    """How one prompt did across runs (newest first)."""
    await _prompt(ctx, pid)
    first = func.min(AivisAnswer.created_at)
    rows = (await ctx.session.execute(
        select(AivisAnswer.run_id, first, func.count(), func.count().filter(AivisAnswer.mentioned.is_(True)))
        .where(AivisAnswer.prompt_id == pid, AivisAnswer.status == "ok")
        .group_by(AivisAnswer.run_id).order_by(first.desc()).limit(26))).all()
    return [{"run_id": r[0], "at": r[1], "answers": r[2], "mentioned": int(r[3] or 0)} for r in rows]


# ------------------------------------------------------------------ competitors
@router.get("/competitors")
async def competitors(run_id: uuid.UUID | None = None, ctx: Context = Depends(require(VIEW))):
    run = await _run(ctx, run_id)
    if run is None:
        return {"run_id": None, "brands": [], "engines": [], "head_to_head": []}
    m = run.metrics or {}
    answers = [a for a in await svc.answers_of(ctx.session, run.id) if a.status == "ok"]
    us = next((r for r in m.get("sov", []) if r["is_us"]), None)
    us_key = svc.norm(us["name"]) if us else ""
    engines: dict[str, dict] = {}
    for a in answers:
        e = engines.setdefault(a.engine_key, {"key": a.engine_key, "label": a.engine_label, "answers": 0, "brands": defaultdict(int)})
        e["answers"] += 1
        names = {svc.norm(b): b for b in a.brands or []}
        if a.mentioned and us:
            names[us_key] = us["name"]
        for n in names.values():
            e["brands"][n] += 1
    # Head to head with the brands in the share-of-voice list.
    head = []
    for r in [r for r in m.get("sov", []) if not r["is_us"]][:10]:
        k = svc.norm(r["name"])
        them = both = only_us = ahead = 0
        for a in answers:
            keys = [svc.norm(b) for b in a.brands or []]
            if k in keys:
                them += 1
                if a.mentioned:
                    both += 1
                    if a.position and a.position < keys.index(k) + 1:
                        ahead += 1
            elif a.mentioned:
                only_us += 1
        head.append({"name": r["name"], "tracked": r["tracked"], "them": them, "both": both,
                     "only_them": them - both, "only_us": only_us, "we_rank_higher": ahead})
    return {
        "run_id": run.id, "answers": len(answers), "brands": m.get("sov", []),
        "engines": [{"key": e["key"], "label": e["label"], "answers": e["answers"],
                     "brands": sorted(({"name": n, "answers": c, "visibility": svc._pct(c, e["answers"])} for n, c in e["brands"].items()),
                                      key=lambda x: -x["answers"])[:8]} for e in engines.values()],
        "head_to_head": head,
    }


# ------------------------------------------------------------------ settings
class EngineIn(BaseModel):
    provider_id: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=160)
    web: bool = True


class SettingsIn(BaseModel):
    enabled: bool = True
    weekday: int = Field(0, ge=0, le=6)
    run_time: str = "09:00"
    engines: list[EngineIn] = Field(default_factory=list, max_length=engines_svc.MAX_ENGINES)
    aliases: list[str] = Field(default_factory=list, max_length=svc.MAX_ALIASES)
    own_domains: list[str] = Field(default_factory=list, max_length=10)
    drop_alert_points: float = Field(10, ge=0, le=100)
    llm_analysis: bool = True

    @field_validator("run_time")
    @classmethod
    def _time(cls, v: str) -> str:
        if not HHMM.match(v or ""):
            raise ValueError("Use a time like 09:00.")
        return v

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        out = []
        for a in v:
            a = re.sub(r"\s+", " ", str(a)).strip()[:80]
            if len(a) >= 3 and a.lower() not in {x.lower() for x in out}:
                out.append(a)
        return out

    @field_validator("own_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        out = []
        for d in v:
            d = str(d).strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0].removeprefix("www.")
            if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d) and d not in out:
                out.append(d)
        return out


async def _settings_payload(ctx: Context) -> dict:
    cfg = svc.effective_config(await svc.get_settings_row(ctx.session, ctx.org_id))
    us, comps, own = await svc.brand_set(ctx.session, cfg)
    engines = await engines_svc.resolve(ctx.session, cfg["engines"])
    return {
        **cfg, "engines": [e.to_dict() for e in engines], "engines_auto": not cfg["engines"],
        "available_engines": await engines_svc.available(ctx.session),
        "paid_hints": await engines_svc.paid_hints(ctx.session),
        "brand": {"name": us.name, "terms": list(dict.fromkeys(t for t in us.terms if t)), "domains": sorted(own)},
        "competitors": [c.name for c in comps], "weekdays": svc.WEEKDAYS,
    }


@router.get("/settings")
async def get_settings(ctx: Context = Depends(require(VIEW))):
    return await _settings_payload(ctx)


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: Context = Depends(require(MANAGE))):
    avail = {a["provider_id"]: a for a in await engines_svc.available(ctx.session)}
    engines = []
    for e in body.engines:
        if str(e.provider_id) not in avail:
            raise _bad("One of the chosen engines uses an integration that is off or was removed.")
        engines.append({"provider_id": str(e.provider_id), "model": e.model.strip(), "web": e.web})
    row = await svc.ensure_settings_row(ctx.session, ctx.org_id)
    row.config = {**(row.config or {}), **body.model_dump(exclude={"engines"}), "engines": engines}
    audit.record(ctx.session, action="aivis.settings", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="aivis_settings", entity_id=row.id, summary="Changed AI visibility settings")
    await ctx.session.commit()
    return await _settings_payload(ctx)


# ------------------------------------------------------------------ llms.txt
class LlmsIn(BaseModel):
    text: str = Field(max_length=200_000)
    full_text: str | None = Field(None, max_length=2_000_000)


async def _llms_payload(ctx: Context) -> dict:
    row = await svc.get_settings_row(ctx.session, ctx.org_id)
    data = (row.data if row else None) or {}
    llms = data.get("llms") or {}
    return {
        "text": llms.get("text") or "", "full_text": llms.get("full_text") or "",
        "generated_at": llms.get("generated_at"), "edited_at": llms.get("edited_at"), "pages": llms.get("pages"),
        "issues": llms_txt.validate(llms["text"]) if llms.get("text") else [],
        "site_check": data.get("site_check"), "website": ctx.org.website if ctx.org else None,
        "site_root": llms_txt.site_root(ctx.org.website if ctx.org else None),
        "crawled_pages": len(await llms_txt.website_pages(ctx.session)),
    }


@router.get("/llms")
async def get_llms(ctx: Context = Depends(require(VIEW))):
    return await _llms_payload(ctx)


@router.put("/llms")
async def put_llms(body: LlmsIn, ctx: Context = Depends(require(MANAGE))):
    row = await svc.ensure_settings_row(ctx.session, ctx.org_id)
    data = dict(row.data or {})
    llms = dict(data.get("llms") or {})
    llms["text"] = body.text.replace("\r\n", "\n")
    if body.full_text is not None:
        llms["full_text"] = body.full_text.replace("\r\n", "\n")
    llms["edited_at"] = datetime.now(UTC).isoformat()
    data["llms"] = llms
    row.data = data
    audit.record(ctx.session, action="aivis.llms.edit", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="aivis_settings", entity_id=row.id, summary="Edited llms.txt")
    await ctx.session.commit()
    return await _llms_payload(ctx)


@router.post("/llms/generate", status_code=202)
async def generate_llms(ctx: Context = Depends(require(MANAGE))):
    job = await enqueue(ctx.session, "aivis.llms", ctx.org_id, {"use_ai": True}, dedupe_key=f"aivis.llms:{ctx.org_id}",
                        priority=3, max_attempts=1, created_by=ctx.user.id)
    await ctx.session.commit()
    return {"job_id": job.id}


@router.post("/site-check")
async def site_check(ctx: Context = Depends(require(MANAGE))):
    result = await llms_txt.check_site(ctx.org.website if ctx.org else None)
    row = await svc.ensure_settings_row(ctx.session, ctx.org_id)
    row.data = {**(row.data or {}), "site_check": result}
    await ctx.session.commit()
    return result
