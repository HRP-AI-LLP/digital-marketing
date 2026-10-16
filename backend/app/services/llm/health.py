"""Model health: automatic fallback when a model hits a limit, has a key/billing problem, or was removed.

Every AI call (text, embeddings, images, voice, video) reports its outcome here:

- success clears any earlier problem;
- a failure is classified (rate limit, daily quota, billing, bad key, model removed, provider down) and the model
  rests until the limit resets, so the next calls skip straight to the next model in the task's chain;
- problems a person must fix (billing, key, removed model) send one notification when they start.

Status rows are written in their own short transaction so they survive when the caller rolls back.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, set_tenant
from app.models import ModelStatus, ProviderConfig
from app.services.llm.catalog import BY_ID

log = logging.getLogger(__name__)

ATTENTION = {"auth", "billing", "unavailable"}
LABELS = {
    "ok": "Ready",
    "cooldown": "Resting",
    "auth": "Key problem",
    "billing": "Billing / credits",
    "unavailable": "Model not available",
}


@dataclass
class Verdict:
    state: str           # cooldown | auth | billing | unavailable | none
    seconds: int         # how long to rest; 0 = no rest
    reason: str          # short, human readable


_RETRY_PATTERNS = [
    re.compile(r"retry[ -]?after[\"':\s]*(\d+(?:\.\d+)?)\s*(s|sec|seconds|m|min|minutes|h|hours)?", re.I),
    re.compile(r"retryDelay[\"':\s]*\"?(\d+(?:\.\d+)?)(s|m|h)", re.I),
    re.compile(r"try again in\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|seconds|m|min|minutes|h|hours)", re.I),
    re.compile(r"(?:reset|resets) in\s*(\d+(?:\.\d+)?)\s*(s|sec|seconds|m|min|minutes|h|hours)", re.I),
]


def _seconds(value: str, unit: str | None) -> int:
    v = float(value)
    u = (unit or "s").lower()
    if u == "ms":
        v /= 1000
    elif u.startswith("m"):
        v *= 60
    elif u.startswith("h"):
        v *= 3600
    return int(v)


def _retry_hint(msg: str) -> int | None:
    for rx in _RETRY_PATTERNS:
        m = rx.search(msg)
        if m:
            return _seconds(m.group(1), m.group(2))
    return None


def _until_daily_reset(now: datetime) -> int:
    """Most free tiers reset daily quotas at midnight US Pacific time (about 08:00 UTC, 13:30 IST)."""
    reset = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return max(1800, int((reset - now).total_seconds()) + 1)


def describe(exc: BaseException) -> str:
    """A readable message even for exceptions whose str() is empty (httpx timeouts are)."""
    text = str(exc).strip()
    name = type(exc).__name__
    if not text:
        return {"ReadTimeout": "ReadTimeout: the model took too long to answer",
                "ConnectTimeout": "ConnectTimeout: couldn't connect in time",
                "WriteTimeout": "WriteTimeout: sending the request took too long",
                "PoolTimeout": "PoolTimeout: too many requests at once"}.get(name, name)
    return text


def classify(exc: BaseException, *, local: bool = False, now: datetime | None = None) -> Verdict:
    now = now or datetime.now(UTC)
    msg = describe(exc)
    low = msg.lower()
    status = getattr(exc, "status", None)
    if status is None:
        m = re.match(r"\s*(?:[\w .()-]+:\s*)?(\d{3})\b", msg)
        status = int(m.group(1)) if m else None

    if status == 402 or re.search(r"credit|billing|insufficient[_ ]quota|payment|prepay|exceeded your current quota|balance", low):
        return Verdict("billing", 24 * 3600, "Out of credits or billing not set up")
    if status in (401, 403) or re.search(r"invalid[_ ]api[_ ]key|api key not valid|unauthori[sz]ed|permission denied|incorrect api key", low):
        return Verdict("auth", 24 * 3600, "API key rejected")
    if status == 404 or re.search(
        r"model[^.]{0,60}(not found|does not exist|not exist|decommissioned|deprecated|no longer|not supported|unsupported|unknown)"
        r"|no such model|model_not_found|is not a valid model|invalid model", low):
        return Verdict("unavailable", 7 * 24 * 3600, "Model removed or not available to this key")
    if status == 429 or re.search(r"rate.?limit|too many requests|quota|resource_exhausted|limit reached|exhausted", low):
        hint = _retry_hint(msg)
        if re.search(r"per ?day|daily|perday|rpd|tpd|requests per day|tokens per day", low):
            return Verdict("cooldown", max(hint or 0, _until_daily_reset(now)), "Daily limit reached")
        if re.search(r"per ?month|monthly", low):
            return Verdict("cooldown", 24 * 3600, "Monthly limit reached")
        return Verdict("cooldown", min(max(hint or 90, 30), 6 * 3600), "Rate limit reached")
    if re.search(r"readtimeout|writetimeout|pooltimeout|timed out waiting|took too long", low):
        return Verdict("cooldown", 60, "Took too long to answer" + (" (try a smaller model)" if local else ""))
    if re.search(r"connection attempts failed|connect call failed|connection refused|connecterror|name or service not known"
                 r"|getaddrinfo|timed out|timeout|connecttimeout", low):
        return Verdict("cooldown", 120 if local else 60, "Not reachable" + (" (is it running?)" if local else ""))
    if status is not None and status >= 500:
        return Verdict("cooldown", 60, "Provider error")
    return Verdict("none", 0, "")


# ----------------------------------------------------------------------------- reading
async def statuses(session: AsyncSession, provider_ids: list[uuid.UUID] | None = None) -> dict[tuple[uuid.UUID, str], ModelStatus]:
    q = select(ModelStatus)
    if provider_ids is not None:
        if not provider_ids:
            return {}
        q = q.where(ModelStatus.provider_id.in_(provider_ids))
    return {(r.provider_id, r.model): r for r in (await session.scalars(q)).all()}


def resting(row: ModelStatus | None, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return bool(row and row.state != "ok" and row.cooldown_until and row.cooldown_until > now)


async def order_chain(session: AsyncSession, chain: list[tuple]) -> tuple[list[tuple], list[str]]:
    """Ready models first (in preference order), then resting ones as a last resort.

    Returns (ordered_chain, notes) where notes explain which models were moved to the back.
    """
    if not chain:
        return chain, []
    rows = await statuses(session, list({c[0].id for c in chain}))
    now = datetime.now(UTC)
    ready, later, notes = [], [], []
    for step in chain:
        row = rows.get((step[0].id, step[1]))
        if resting(row, now):
            later.append(step)
            notes.append(f"{step[0].name} ({step[1]}): {row.reason or LABELS.get(row.state, row.state)}, "
                         f"resting until {row.cooldown_until.astimezone(UTC):%H:%M} UTC")
        else:
            ready.append(step)
    return ready + later, notes


# ----------------------------------------------------------------------------- writing
async def _upsert(org_id: uuid.UUID, provider_id: uuid.UUID, model: str, values: dict) -> str | None:
    """Write one status row in its own transaction. Returns the previous state."""
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        prev = await s.scalar(select(ModelStatus.state).where(
            ModelStatus.provider_id == provider_id, ModelStatus.model == model))
        stmt = insert(ModelStatus).values(id=uuid.uuid4(), organization_id=org_id, provider_id=provider_id,
                                          model=model[:200], updated_at=datetime.now(UTC), **values)
        upd = {k: stmt.excluded[k] for k in values} | {"updated_at": stmt.excluded.updated_at}
        await s.execute(stmt.on_conflict_do_update(
            index_elements=[ModelStatus.organization_id, ModelStatus.provider_id, ModelStatus.model], set_=upd))
        await s.commit()
        return prev


async def record_success(cfg: ProviderConfig, model: str) -> None:
    if not model:
        return
    try:
        async with SessionLocal() as s:
            set_tenant(s, cfg.organization_id)
            row = await s.scalar(select(ModelStatus).where(ModelStatus.provider_id == cfg.id, ModelStatus.model == model))
            now = datetime.now(UTC)
            if row is None:
                s.add(ModelStatus(organization_id=cfg.organization_id, provider_id=cfg.id, model=model[:200], state="ok",
                                  last_ok_at=now, fail_count=0))
            elif row.state != "ok" or row.fail_count or not row.last_ok_at or (now - row.last_ok_at) > timedelta(minutes=10):
                row.state, row.cooldown_until, row.reason, row.fail_count, row.last_ok_at = "ok", None, None, 0, now
            else:
                return
            await s.commit()
    except Exception:  # noqa: BLE001 - health tracking must never break the AI call
        log.exception("model health success write failed")


async def record_failure(cfg: ProviderConfig, model: str, exc: BaseException) -> Verdict:
    spec = BY_ID.get(cfg.provider)
    v = classify(exc, local=bool(spec and spec.local))
    if not model:
        return v
    now = datetime.now(UTC)
    try:
        async with SessionLocal() as s:
            set_tenant(s, cfg.organization_id)
            row = await s.scalar(select(ModelStatus).where(ModelStatus.provider_id == cfg.id, ModelStatus.model == model))
            prev = row.state if row else None
            if row is None:
                row = ModelStatus(organization_id=cfg.organization_id, provider_id=cfg.id, model=model[:200], fail_count=0)
                s.add(row)
            row.fail_count = (row.fail_count or 0) + 1
            row.last_fail_at, row.last_error = now, describe(exc)[:1000]
            if v.state != "none":
                row.state, row.reason = v.state, v.reason
                row.cooldown_until = now + timedelta(seconds=v.seconds)
            elif row.fail_count >= 3:  # repeated unexplained failures: short rest
                row.state, row.reason = "cooldown", "Failing repeatedly"
                row.cooldown_until = now + timedelta(minutes=5)
            await s.commit()
    except Exception:  # noqa: BLE001
        log.exception("model health failure write failed")
        return v
    if v.state in ATTENTION and prev != v.state:
        await _notify(cfg, model, v, describe(exc))
    return v


async def _notify(cfg: ProviderConfig, model: str, v: Verdict, error: str) -> None:
    try:
        from app.services.notify import notify_org

        what = {
            "billing": "has run out of credits or needs billing set up",
            "auth": "rejected the API key",
            "unavailable": f"no longer offers the model {model}",
        }[v.state]
        await notify_org(
            cfg.organization_id, "ai_issue", f"{cfg.name} {what}",
            f"Tasks that use it now fall back to the next model in Task routing. Details: {error[:300]}",
            link="/settings/integrations", permission="integration.manage",
        )
    except Exception:  # noqa: BLE001
        log.exception("model health notification failed")


async def clear_provider(session: AsyncSession, provider_id: uuid.UUID) -> None:
    """After the key or address changes, give every model on the provider a fresh start."""
    for row in (await session.scalars(select(ModelStatus).where(ModelStatus.provider_id == provider_id))).all():
        row.state, row.cooldown_until, row.reason, row.fail_count = "ok", None, None, 0


async def mark_unavailable(cfg: ProviderConfig, model: str, reason: str) -> None:
    """Used by the model-list refresh when a model you configured is no longer offered."""
    now = datetime.now(UTC)
    prev = await _upsert(cfg.organization_id, cfg.id, model, {
        "state": "unavailable", "reason": reason[:300], "cooldown_until": now + timedelta(days=1), "last_fail_at": now,
    })
    if prev != "unavailable":
        await _notify(cfg, model, Verdict("unavailable", 0, reason), reason)
