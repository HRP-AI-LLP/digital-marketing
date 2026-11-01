"""Several API keys (accounts) for one provider, with automatic switching.

A provider connection keeps its main key in secrets["api_key"] and any number of backup keys in
secrets["extra_keys"] (other accounts of the same provider, free or paid). Every AI call goes through `with_keys`:
it uses the first key that isn't resting, and when a key runs out of credits, hits its daily or rate limit, or is
rejected, that key rests (same rules as model health) and the call is retried at once with the next key. Only
when every key has failed does the error reach the model-level fallback (the next model or provider in the chain).

Key states are kept as model-health rows named "key:<fingerprint>" so they share the resting logic and survive
restarts; the fingerprint is a short hash, never the key itself.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant
from app.core.crypto import decrypt_json
from app.models import ModelStatus, ProviderConfig

log = logging.getLogger(__name__)
T = TypeVar("T")

SWITCH_ON = {"billing", "auth", "cooldown"}  # a different account helps with these; "unavailable" (model gone) doesn't
_override: ContextVar[dict] = ContextVar("api_key_override", default={})


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def status_name(key: str) -> str:
    return f"key:{fingerprint(key)}"


def all_keys(cfg: ProviderConfig) -> list[str]:
    s = decrypt_json(cfg.secrets_enc) or {}
    keys = [s.get("api_key") or ""] + list(s.get("extra_keys") or [])
    out: list[str] = []
    for k in keys:
        k = (k or "").strip()
        if k and k not in out:
            out.append(k)
    return out


def secrets_of(cfg: ProviderConfig) -> dict:
    """The connection's secrets, with the key currently in use (see `with_keys`) as api_key."""
    s = dict(decrypt_json(cfg.secrets_enc) or {})
    k = _override.get().get(cfg.id)
    if k:
        s["api_key"] = k
    s.pop("extra_keys", None)
    return s


@contextmanager
def using(cfg: ProviderConfig, key: str):
    token = _override.set({**_override.get(), cfg.id: key})
    try:
        yield
    finally:
        _override.reset(token)


async def _rows(cfg: ProviderConfig) -> dict[str, ModelStatus]:
    async with SessionLocal() as s:
        set_tenant(s, cfg.organization_id)
        rows = (await s.scalars(select(ModelStatus).where(ModelStatus.provider_id == cfg.id,
                                                          ModelStatus.model.like("key:%")))).all()
        return {r.model: r for r in rows}


async def ordered_keys(cfg: ProviderConfig) -> list[str]:
    """Ready keys first (in the order they were added), resting ones last as a final try."""
    keys = all_keys(cfg)
    if len(keys) <= 1:
        return keys
    from app.services.llm.health import resting

    rows = await _rows(cfg)
    now = datetime.now(UTC)
    ready = [k for k in keys if not resting(rows.get(status_name(k)), now)]
    return ready + [k for k in keys if k not in ready]


async def key_states(cfg: ProviderConfig) -> list[dict]:
    """For the Integrations page: one entry per key (masked), with its state."""
    from app.services.llm.health import LABELS, resting

    rows = await _rows(cfg)
    now = datetime.now(UTC)
    out = []
    for i, k in enumerate(all_keys(cfg)):
        r = rows.get(status_name(k))
        rest = resting(r, now)
        out.append({
            "index": i, "label": "Main key" if i == 0 else f"Backup key {i}", "masked": f"…{k[-4:]}" if len(k) > 8 else "…",
            "fingerprint": fingerprint(k), "state": r.state if r and rest else "ok",
            "state_label": (r.reason or LABELS.get(r.state, r.state)) if r and rest else "Ready",
            "until": r.cooldown_until.isoformat() if r and rest and r.cooldown_until else None,
            "last_ok_at": r.last_ok_at.isoformat() if r and r.last_ok_at else None,
        })
    return out


async def _mark(cfg: ProviderConfig, key: str, state: str, reason: str, seconds: int, error: str | None) -> str | None:
    name = status_name(key)
    now = datetime.now(UTC)
    try:
        async with SessionLocal() as s:
            set_tenant(s, cfg.organization_id)
            row = await s.scalar(select(ModelStatus).where(ModelStatus.provider_id == cfg.id, ModelStatus.model == name))
            prev = row.state if row else None
            if row is None:
                row = ModelStatus(organization_id=cfg.organization_id, provider_id=cfg.id, model=name, fail_count=0)
                s.add(row)
            if state == "ok":
                if prev in (None, "ok") and row.last_ok_at and (now - row.last_ok_at) < timedelta(minutes=10):
                    return prev
                row.state, row.reason, row.cooldown_until, row.fail_count, row.last_ok_at = "ok", None, None, 0, now
            else:
                row.state, row.reason = state, reason
                row.cooldown_until = now + timedelta(seconds=seconds)
                row.fail_count = (row.fail_count or 0) + 1
                row.last_fail_at, row.last_error = now, (error or "")[:1000]
            await s.commit()
            return prev
    except Exception:  # noqa: BLE001 - key tracking must never break the AI call
        log.exception("API key state write failed")
        return None


async def _notify_switch(cfg: ProviderConfig, idx: int, total: int, reason: str, nxt: int | None) -> None:
    try:
        from app.services.notify import notify_org

        which = "main key" if idx == 0 else f"backup key {idx}"
        then = (f"Switched to {'the main key' if nxt == 0 else f'backup key {nxt}'} automatically."
                if nxt is not None else "No other key is ready, so tasks fall back to the next model in Task routing.")
        await notify_org(cfg.organization_id, "ai_issue", f"{cfg.name}: {which} {reason.lower()}",
                         f"{then} ({total} key{'s' if total != 1 else ''} on this connection.)",
                         link="/settings/integrations", permission="integration.manage")
    except Exception:  # noqa: BLE001
        log.exception("key switch notification failed")


async def with_keys(cfg: ProviderConfig, call: Callable[[], Awaitable[T]]) -> T:
    """Run `call` with this connection's keys in turn until one works. `call` must read the key through
    secrets_of(cfg) / conn_from(cfg) inside the call (the current key is set for its duration)."""
    keys = await ordered_keys(cfg)
    if len(keys) <= 1:
        return await call()
    from app.services.llm.catalog import BY_ID
    from app.services.llm.health import classify

    spec = BY_ID.get(cfg.provider)
    last: BaseException | None = None
    order = all_keys(cfg)
    for i, key in enumerate(keys):
        with using(cfg, key):
            try:
                result = await call()
            except Exception as e:  # noqa: BLE001 - decide below whether another key can help
                v = classify(e, local=bool(spec and spec.local))
                if v.state not in SWITCH_ON:
                    raise
                prev = await _mark(cfg, key, v.state, v.reason, v.seconds, str(e))
                nxt = order.index(keys[i + 1]) if i + 1 < len(keys) else None
                if v.state in ("billing", "auth") and prev != v.state:
                    await _notify_switch(cfg, order.index(key), len(order), v.reason, nxt)
                last = e
                continue
        await _mark(cfg, key, "ok", "", 0, None)
        return result
    assert last is not None
    raise last
