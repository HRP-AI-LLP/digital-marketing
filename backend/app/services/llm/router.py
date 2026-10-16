"""Task routing for text models with fallbacks, monthly budgets and usage logging.

A task's chain comes from TaskRoute (Admin → Integrations → Task routing). Without a route, the
first enabled LLM provider with a default model is used (local providers last, so a cloud free
tier is preferred when configured; set a route to change this).
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.models import LlmUsage, ProviderConfig, TaskRoute
from app.services.llm import client as llm
from app.services.llm import health
from app.services.llm.catalog import BY_ID


class NoProviderError(Exception):
    pass


class AllProvidersFailed(Exception):
    pass


@dataclass
class LlmResult:
    text: str
    provider: str
    provider_id: uuid.UUID
    model: str


def conn_from(cfg: ProviderConfig) -> llm.Conn:
    secrets = decrypt_json(cfg.secrets_enc)
    spec = BY_ID.get(cfg.provider)
    base = cfg.base_url or (spec.base_url if spec else "")
    return llm.Conn(provider=cfg.provider, base_url=base, api_key=secrets.get("api_key"), extra={**(cfg.settings or {}), **secrets})


def _price(cfg: ProviderConfig, model: str) -> tuple[float, float]:
    prices = (cfg.settings or {}).get("prices", {})
    p = prices.get(model) or prices.get("*") or [0, 0]
    try:
        return float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0


async def month_spend(session: AsyncSession, provider_id: uuid.UUID) -> float:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(
            LlmUsage.provider_id == provider_id, LlmUsage.created_at >= start
        )
    )
    return float(total or 0)


async def over_budget(session: AsyncSession, cfg: ProviderConfig) -> bool:
    if not cfg.monthly_budget_usd:
        return False
    return await month_spend(session, cfg.id) >= float(cfg.monthly_budget_usd)


async def resolve_chain(session: AsyncSession, task: str, kind: str = "llm") -> list[tuple[ProviderConfig, str]]:
    route = await session.scalar(select(TaskRoute).where(TaskRoute.task == task))
    cfgs = {c.id: c for c in (await session.scalars(select(ProviderConfig).where(ProviderConfig.kind == kind))).all()}
    chain: list[tuple[ProviderConfig, str]] = []
    if route and route.chain:
        for step in route.chain:
            try:
                cfg = cfgs.get(uuid.UUID(str(step.get("provider_id"))))
            except ValueError:
                cfg = None
            if cfg and cfg.enabled and step.get("model"):
                chain.append((cfg, step["model"]))
        if chain:
            return chain
    key = "embed_model" if task == "embed" else "default_model"
    enabled = [c for c in cfgs.values() if c.enabled and (c.settings or {}).get(key)]
    enabled.sort(key=lambda c: (BY_ID.get(c.provider).local if BY_ID.get(c.provider) else False, c.created_at))
    return [(c, c.settings[key]) for c in enabled]


def _log(session: AsyncSession, cfg: ProviderConfig, model: str, task: str, kind: str, *, tin=0, tout=0, units=0,
         ok=True, error=None, latency=0) -> None:
    pin, pout = _price(cfg, model)
    session.add(LlmUsage(
        organization_id=cfg.organization_id, provider_id=cfg.id, provider=cfg.provider, model=model, task=task,
        kind=kind, input_tokens=tin, output_tokens=tout, units=units,
        cost_usd=round((tin * pin + tout * pout) / 1_000_000, 6), ok=ok, error=error, latency_ms=latency,
    ))


async def complete(
    session: AsyncSession, task: str, prompt: str | None = None, *, messages: list[dict] | None = None,
    system: str | None = None, json_mode: bool = False, temperature: float = 0.7, max_tokens: int = 1500,
) -> LlmResult:
    chain = await resolve_chain(session, task)
    if not chain:
        raise NoProviderError(
            "No AI text provider is set up. Add one in Settings → Integrations (local Ollama or a free Gemini/Groq key works)."
        )
    msgs = messages or [{"role": "user", "content": prompt or ""}]
    chain, _ = await health.order_chain(session, chain)
    errors = []
    for cfg, model in chain:
        if await over_budget(session, cfg):
            errors.append(f"{cfg.name}: monthly budget reached")
            continue
        try:
            r = await llm.chat(conn_from(cfg), model, msgs, system=system, json_mode=json_mode,
                               temperature=temperature, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001 - try the next model in the chain
            _log(session, cfg, model, task, "chat", ok=False, error=health.describe(e)[:1000])
            v = await health.record_failure(cfg, model, e)
            errors.append(f"{cfg.name} ({model}): {_short(e, v)}")
            continue
        _log(session, cfg, model, task, "chat", tin=r.input_tokens, tout=r.output_tokens, latency=r.latency_ms)
        await session.flush()
        await health.record_success(cfg, model)
        return LlmResult(r.text, cfg.provider, cfg.id, model)
    await session.flush()
    raise AllProvidersFailed("All AI providers failed. " + " | ".join(errors))


def _short(e: Exception, v: "health.Verdict") -> str:
    text = health.describe(e)
    if v.state != "none":
        return f"{v.reason}. {text[:200]}"
    return text[:300]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(text: str):
    text = text.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("Model did not return valid JSON")


async def complete_json(session: AsyncSession, task: str, prompt: str, *, system: str | None = None,
                        temperature: float = 0.5, max_tokens: int = 2500):
    r = await complete(session, task, prompt, system=system, json_mode=True, temperature=temperature, max_tokens=max_tokens)
    try:
        return parse_json(r.text)
    except ValueError:
        r = await complete(session, task, prompt + "\n\nYour previous answer was not valid JSON. Return ONLY valid JSON.",
                           system=system, json_mode=True, temperature=0.2, max_tokens=max_tokens)
        return parse_json(r.text)


async def embed_texts(session: AsyncSession, texts: list[str]) -> tuple[list[list[float]] | None, str | None]:
    """Embeddings via the 'embed' route. Returns (None, None) when no embedding model is set up."""
    chain, _ = await health.order_chain(session, await resolve_chain(session, "embed"))
    for cfg, model in chain:
        if await over_budget(session, cfg):
            continue
        try:
            vecs: list[list[float]] = []
            for i in range(0, len(texts), 32):
                batch, tokens = await llm.embed(conn_from(cfg), model, [t[:8000] for t in texts[i:i + 32]])
                vecs.extend(batch)
                _log(session, cfg, model, "embed", "embed", tin=tokens or sum(len(t) // 4 for t in texts[i:i + 32]))
        except Exception as e:  # noqa: BLE001
            _log(session, cfg, model, "embed", "embed", ok=False, error=health.describe(e)[:1000])
            await health.record_failure(cfg, model, e)
            continue
        await health.record_success(cfg, model)
        return vecs, f"{cfg.provider}:{model}"
    return None, None
