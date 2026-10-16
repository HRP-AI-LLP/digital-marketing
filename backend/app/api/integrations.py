"""Integrations: configure AI providers and other external services, route tasks, see usage."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, literal_column, select

from app.core.crypto import decrypt_json, encrypt_json, mask
from app.core.deps import Context, require
from app.models import LlmUsage, ProviderConfig, TaskRoute
from app.services import audit
from app.services.llm import client as llm
from app.services.llm import discovery, health
from app.services.llm.catalog import BY_ID, CATALOG, MEDIA_TASKS, ROUTE_TASKS, TASKS
from app.services.llm.router import AllProvidersFailed, NoProviderError, complete, conn_from, month_spend

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Other modules register connection testers per provider style: async fn(cfg) -> message
TESTERS: dict[str, Callable[[ProviderConfig], Awaitable[str]]] = {}


def tester(*styles: str):
    def deco(fn):
        for s in styles:
            TESTERS[s] = fn
        return fn
    return deco


# ------------------------------------------------------------------ schemas
class ProviderIn(BaseModel):
    provider: str
    name: str | None = Field(None, max_length=120)
    base_url: str | None = Field(None, max_length=300)
    secrets: dict[str, str | None] = {}
    settings: dict = {}
    enabled: bool = True
    monthly_budget_usd: float | None = Field(None, ge=0)


class ProviderPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    base_url: str | None = Field(None, max_length=300)
    secrets: dict[str, str | None] | None = None
    settings: dict | None = None
    enabled: bool | None = None
    monthly_budget_usd: float | None = Field(None, ge=0)


class RouteStep(BaseModel):
    provider_id: uuid.UUID
    model: str


class RoutesIn(BaseModel):
    routes: dict[str, list[RouteStep]]


class DiscoverIn(BaseModel):
    provider: str
    base_url: str | None = None
    secrets: dict[str, str | None] = {}
    provider_id: uuid.UUID | None = None  # editing: use the saved key when the form leaves it empty


class PlaygroundIn(BaseModel):
    task: str = "write"
    prompt: str = Field(min_length=1, max_length=8000)
    system: str | None = None


def _secret_fields(provider: str) -> list[str]:
    spec = BY_ID.get(provider)
    names = ["api_key"] if (spec and spec.key) else []
    if spec:
        names += [f for f, _, secret in spec.fields if secret]
    return names


def _out(cfg: ProviderConfig, spend: float | None = None) -> dict:
    secrets = decrypt_json(cfg.secrets_enc)
    spec = BY_ID.get(cfg.provider)
    return {
        "id": cfg.id, "kind": cfg.kind, "provider": cfg.provider, "name": cfg.name,
        "label": spec.label if spec else cfg.provider,
        "base_url": cfg.base_url or (spec.base_url if spec else ""),
        "secrets": {k: mask(v) for k, v in secrets.items() if k in _secret_fields(cfg.provider) and v},
        "fields": {k: v for k, v in secrets.items() if k not in _secret_fields(cfg.provider)},
        "settings": cfg.settings or {}, "enabled": cfg.enabled,
        "monthly_budget_usd": float(cfg.monthly_budget_usd) if cfg.monthly_budget_usd is not None else None,
        "month_spend_usd": spend, "last_test_ok": cfg.last_test_ok, "last_test_at": cfg.last_test_at,
        "last_test_message": cfg.last_test_message, "created_at": cfg.created_at,
    }


async def _get(ctx: Context, pid: uuid.UUID) -> ProviderConfig:
    cfg = await ctx.session.scalar(select(ProviderConfig).where(ProviderConfig.id == pid))
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found.")
    return cfg


# ------------------------------------------------------------------ catalog & CRUD
@router.get("/catalog")
async def catalog(ctx: Context = Depends(require("integration.view"))):
    return {
        "providers": [
            {"id": p.id, "kind": p.kind, "label": p.label, "style": p.style, "base_url": p.base_url, "key": p.key,
             "free": p.free, "docs": p.docs, "examples": list(p.examples), "local": p.local,
             "fields": [{"name": n, "label": lbl, "secret": s} for n, lbl, s in p.fields]}
            for p in CATALOG
        ],
        "tasks": TASKS,
        "media_tasks": MEDIA_TASKS,
        "route_tasks": ROUTE_TASKS,
    }


@router.get("/providers")
async def list_providers(kind: str | None = None, ctx: Context = Depends(require("integration.view"))):
    q = select(ProviderConfig).order_by(ProviderConfig.kind, ProviderConfig.created_at)
    if kind:
        q = q.where(ProviderConfig.kind == kind)
    rows = (await ctx.session.scalars(q)).all()
    return [_out(c, await month_spend(ctx.session, c.id) if c.kind in discovery.AI_KINDS else None) for c in rows]


@router.post("/providers", status_code=201)
async def add_provider(body: ProviderIn, ctx: Context = Depends(require("integration.manage"))):
    spec = BY_ID.get(body.provider)
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown provider: {body.provider}")
    if spec.key and not (body.secrets.get("api_key") or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{spec.label} needs an API key.")
    secrets = {k: v.strip() for k, v in body.secrets.items() if v and v.strip()}
    cfg = ProviderConfig(
        organization_id=ctx.org_id, kind=spec.kind, provider=spec.id, name=body.name or spec.label,
        base_url=(body.base_url or "").strip() or None, secrets_enc=encrypt_json(secrets),
        settings=body.settings or {}, enabled=body.enabled, monthly_budget_usd=body.monthly_budget_usd,
    )
    ctx.session.add(cfg)
    audit.record(ctx.session, action="integration.added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="integration", entity_id=cfg.id, summary=f"Added {spec.label}", ip=ctx.ip)
    await ctx.session.commit()
    if cfg.kind in discovery.AI_KINDS:
        await discovery.sync_provider(ctx.session, cfg, notify_missing=False)
        await ctx.session.commit()
    return _out(cfg)


@router.patch("/providers/{pid}")
async def update_provider(pid: uuid.UUID, body: ProviderPatch, ctx: Context = Depends(require("integration.manage"))):
    cfg = await _get(ctx, pid)
    data = body.model_dump(exclude_unset=True)
    if "secrets" in data and data["secrets"] is not None:
        current = decrypt_json(cfg.secrets_enc)
        for k, v in data["secrets"].items():
            if v is None:
                current.pop(k, None)
            elif v.strip() and not v.startswith("••"):
                current[k] = v.strip()
        cfg.secrets_enc = encrypt_json(current)
    if "settings" in data and data["settings"] is not None:
        cfg.settings = {**(cfg.settings or {}), **data["settings"]}
    for k in ("name", "enabled", "monthly_budget_usd"):
        if k in data:
            setattr(cfg, k, data[k])
    old_base = cfg.base_url
    if "base_url" in data:
        cfg.base_url = (data["base_url"] or "").strip() or None
    access_changed = cfg.base_url != old_base or any(
        v and v.strip() and not v.startswith("••") for v in (data.get("secrets") or {}).values())
    if access_changed or data.get("enabled") is True:
        await health.clear_provider(ctx.session, cfg.id)  # new key or address: give every model a fresh start
    audit.record(ctx.session, action="integration.updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="integration", entity_id=cfg.id, summary=f"Updated {cfg.name}", ip=ctx.ip)
    await ctx.session.commit()
    if cfg.kind in discovery.AI_KINDS and (access_changed or not (cfg.settings or {}).get("models_synced_at")):
        await discovery.sync_provider(ctx.session, cfg, notify_missing=False)
        await ctx.session.commit()
    return _out(cfg, await month_spend(ctx.session, cfg.id))


@router.delete("/providers/{pid}", status_code=204)
async def delete_provider(pid: uuid.UUID, ctx: Context = Depends(require("integration.manage"))):
    cfg = await _get(ctx, pid)
    audit.record(ctx.session, action="integration.removed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="integration", entity_id=cfg.id, summary=f"Removed {cfg.name}", ip=ctx.ip)
    await ctx.session.delete(cfg)
    await ctx.session.commit()


def _models_out(cfg: ProviderConfig, error: str | None = None) -> dict:
    st = cfg.settings or {}
    return {
        "models": st.get("models") or [], "types": st.get("model_types") or {}, "source": st.get("models_source"),
        "synced_at": st.get("models_synced_at"), "error": error or st.get("models_error"),
        "missing": st.get("missing_models") or [],
    }


@router.get("/providers/{pid}/models")
async def provider_models(pid: uuid.UUID, refresh: bool = False, ctx: Context = Depends(require("integration.view"))):
    """The provider's model list. Served from the last refresh; refreshed live when asked or older than 6 hours."""
    cfg = await _get(ctx, pid)
    st = cfg.settings or {}
    stale = True
    if st.get("models_synced_at"):
        try:
            stale = datetime.now(UTC) - datetime.fromisoformat(st["models_synced_at"]) > timedelta(hours=6)
        except ValueError:
            stale = True
    if cfg.kind in discovery.AI_KINDS and (refresh or stale or not st.get("models")):
        if refresh and "integration.manage" not in ctx.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't refresh integrations.")
        r = await discovery.sync_provider(ctx.session, cfg, notify_missing=refresh)
        await ctx.session.commit()
        return _models_out(cfg, None if r.get("ok") else r.get("error"))
    return _models_out(cfg)


@router.post("/discover")
async def discover_models(body: DiscoverIn, ctx: Context = Depends(require("integration.manage"))):
    """List models for the Add/Edit form before saving (uses the saved key when the form leaves it empty)."""
    secrets = {k: v for k, v in body.secrets.items() if v and v.strip() and not v.startswith("••")}
    settings: dict = {}
    if body.provider_id:
        cfg = await _get(ctx, body.provider_id)
        secrets = {**decrypt_json(cfg.secrets_enc), **secrets}
        settings = cfg.settings or {}
    try:
        d = await discovery.discover_unsaved(body.provider, body.base_url, secrets, settings)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Couldn't list models: {e}")
    return {"models": d.models, "types": d.types, "source": d.source}


@router.post("/models/refresh")
async def refresh_all_models(ctx: Context = Depends(require("integration.manage"))):
    cfgs = (await ctx.session.scalars(select(ProviderConfig).where(
        ProviderConfig.kind.in_(discovery.AI_KINDS), ProviderConfig.enabled.is_(True)))).all()
    out = []
    for cfg in cfgs:
        r = await discovery.sync_provider(ctx.session, cfg)
        out.append({"id": cfg.id, "name": cfg.name, "ok": r.get("ok"), "count": len(r.get("models") or []),
                    "error": r.get("error"), "missing": r.get("missing", [])})
    await ctx.session.commit()
    return {"providers": out}


@router.get("/model-status")
async def model_status(ctx: Context = Depends(require("integration.view"))):
    return {"statuses": await discovery.statuses_for_ui(ctx.session)}


@router.post("/model-status/reset", status_code=204)
async def reset_model_status(body: RouteStep, ctx: Context = Depends(require("integration.manage"))):
    """'Try it again now': clear a model's rest period."""
    from app.models import ModelStatus

    row = await ctx.session.scalar(select(ModelStatus).where(
        ModelStatus.provider_id == body.provider_id, ModelStatus.model == body.model))
    if row:
        row.state, row.cooldown_until, row.reason, row.fail_count = "ok", None, None, 0
        await ctx.session.commit()


async def _test_llm(cfg: ProviderConfig) -> str:
    c = conn_from(cfg)
    models = await llm.list_models(c)
    st = cfg.settings or {}
    model, emb = st.get("default_model"), st.get("embed_model")
    parts = [f"Connected. {len(models)} models available."]
    if model:
        try:
            r = await llm.chat(c, model, [{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=10, temperature=0)
        except Exception as e:  # noqa: BLE001
            await health.record_failure(cfg, model, e)
            raise
        await health.record_success(cfg, model)
        parts.append(f"{model} replied: {r.text.strip()[:40]}.")
    else:
        parts.append("Choose a default model to use it.")
    if emb:
        try:
            vecs, _ = await llm.embed(c, emb, ["ready"])
        except Exception as e:  # noqa: BLE001
            await health.record_failure(cfg, emb, e)
            raise llm.ProviderError(f"{' '.join(parts)} Embedding model {emb} failed: {e}") from e
        await health.record_success(cfg, emb)
        parts.append(f"Embeddings OK ({len(vecs[0]) if vecs else 0} dimensions).")
    return " ".join(parts)


TESTERS["openai"] = _test_llm
TESTERS["anthropic"] = _test_llm


@router.post("/providers/{pid}/test")
async def test_provider(pid: uuid.UUID, ctx: Context = Depends(require("integration.manage"))):
    cfg = await _get(ctx, pid)
    spec = BY_ID.get(cfg.provider)
    fn = TESTERS.get(cfg.provider) or TESTERS.get(spec.style if spec else "")
    if fn is None:
        ok, msg = True, "Saved. There's no live test for this integration; it's checked when first used."
    else:
        try:
            msg, ok = await fn(cfg), True
        except Exception as e:  # noqa: BLE001
            msg, ok = f"{e}", False
    cfg.last_test_ok, cfg.last_test_at, cfg.last_test_message = ok, datetime.now(UTC), msg[:1000]
    if cfg.kind in discovery.AI_KINDS:
        if ok:
            await health.clear_provider(ctx.session, cfg.id)
        await discovery.sync_provider(ctx.session, cfg, notify_missing=False)
    await ctx.session.commit()
    return {"ok": ok, "message": msg}


# ------------------------------------------------------------------ routing
@router.get("/routes")
async def get_routes(ctx: Context = Depends(require("integration.view"))):
    rows = (await ctx.session.scalars(select(TaskRoute))).all()
    return {"tasks": TASKS, "route_tasks": ROUTE_TASKS, "routes": {r.task: r.chain for r in rows}}


@router.put("/routes")
async def set_routes(body: RoutesIn, ctx: Context = Depends(require("integration.manage"))):
    existing = {r.task: r for r in (await ctx.session.scalars(select(TaskRoute))).all()}
    kinds = {c.id: c.kind for c in (await ctx.session.scalars(select(ProviderConfig))).all()}
    for task, steps in body.routes.items():
        if task not in ROUTE_TASKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown task: {task}")
        for s in steps:
            if s.provider_id not in kinds:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "A route points to an integration that doesn't exist.")
            if kinds[s.provider_id] != ROUTE_TASKS[task]["kind"]:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    f"'{ROUTE_TASKS[task]['label']}' can only use {ROUTE_TASKS[task]['kind']} integrations.")
            if not s.model.strip():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Choose a model for every step of '{ROUTE_TASKS[task]['label']}'.")
        chain = [{"provider_id": str(s.provider_id), "model": s.model} for s in steps]
        if task in existing:
            existing[task].chain = chain
        else:
            ctx.session.add(TaskRoute(organization_id=ctx.org_id, task=task, chain=chain))
    audit.record(ctx.session, action="integration.routes", organization_id=ctx.org_id, actor=ctx.user,
                 summary="Updated AI task routing", ip=ctx.ip)
    await ctx.session.commit()
    return await get_routes(ctx)


# ------------------------------------------------------------------ usage & playground
@router.get("/usage")
async def usage(days: int = 30, ctx: Context = Depends(require("integration.view"))):
    """Usage per model. days=0 means since the beginning."""
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 3650))) if days else datetime(2000, 1, 1, tzinfo=UTC)
    period = LlmUsage.created_at >= since
    fails = func.sum(case((LlmUsage.ok.is_(False), 1), else_=0))
    rows = (await ctx.session.execute(
        select(
            LlmUsage.provider, LlmUsage.model, LlmUsage.task,
            func.count(), func.sum(LlmUsage.input_tokens), func.sum(LlmUsage.output_tokens),
            func.sum(LlmUsage.cost_usd), fails,
        ).where(period).group_by(LlmUsage.provider, LlmUsage.model, LlmUsage.task)
    )).all()
    per_model = (await ctx.session.execute(
        select(
            LlmUsage.provider_id, LlmUsage.provider, LlmUsage.model, LlmUsage.kind,
            func.count(), fails, func.sum(LlmUsage.input_tokens), func.sum(LlmUsage.output_tokens),
            func.sum(case((LlmUsage.ok.is_(True), LlmUsage.units), else_=0)), func.sum(LlmUsage.cost_usd),
            func.max(LlmUsage.created_at), func.array_agg(func.distinct(LlmUsage.task)),
        ).where(period).group_by(LlmUsage.provider_id, LlmUsage.provider, LlmUsage.model, LlmUsage.kind)
        .order_by(func.max(LlmUsage.created_at).desc())
    )).all()
    names = {c.id: c.name for c in (await ctx.session.scalars(select(ProviderConfig))).all()}
    day = func.date_trunc(literal_column("'day'"), LlmUsage.created_at)
    daily = (await ctx.session.execute(
        select(day, func.count(), func.sum(LlmUsage.cost_usd), func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens))
        .where(LlmUsage.created_at >= max(since, datetime.now(UTC) - timedelta(days=90))).group_by(day).order_by(day)
    )).all()
    models = [
        {"provider_id": pid, "integration": names.get(pid) if pid else None, "provider": p, "model": m, "kind": k,
         "calls": n, "failures": int(f or 0), "input_tokens": int(i or 0), "output_tokens": int(o or 0),
         "units": int(u or 0), "cost_usd": float(c or 0), "last_used": last, "tasks": sorted(t for t in (tasks or []) if t)}
        for pid, p, m, k, n, f, i, o, u, c, last, tasks in per_model
    ]
    return {
        "days": days,
        "totals": {
            "calls": sum(x["calls"] for x in models), "failures": sum(x["failures"] for x in models),
            "input_tokens": sum(x["input_tokens"] for x in models), "output_tokens": sum(x["output_tokens"] for x in models),
            "images": sum(x["units"] for x in models if x["kind"] == "image"),
            "audio_seconds": sum(x["units"] for x in models if x["kind"] == "tts"),
            "videos": sum(x["units"] for x in models if x["kind"] == "video"),
            "cost_usd": round(sum(x["cost_usd"] for x in models), 6),
        },
        "models": models,
        "by_model": [
            {"provider": p, "model": m, "task": t, "calls": n, "input_tokens": int(i or 0), "output_tokens": int(o or 0),
             "cost_usd": float(c or 0), "failures": int(f or 0)}
            for p, m, t, n, i, o, c, f in rows
        ],
        "daily": [{"day": d.date().isoformat(), "calls": n, "cost_usd": float(c or 0), "tokens": int(tk or 0)}
                  for d, n, c, tk in daily],
    }


@router.post("/playground")
async def playground(body: PlaygroundIn, ctx: Context = Depends(require("integration.manage"))):
    if body.task not in TASKS or body.task == "embed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a text task.")
    try:
        r = await complete(ctx.session, body.task, body.prompt, system=body.system, max_tokens=800)
    except (NoProviderError, AllProvidersFailed) as e:
        await ctx.session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    await ctx.session.commit()
    return {"text": r.text, "provider": r.provider, "model": r.model}
