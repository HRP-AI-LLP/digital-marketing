"""Personal API tokens for automation tools: hrp_<prefix>_<secret>.

Only the SHA-256 hash is stored; the full token is shown once when it's created. A request carrying a token
acts as the person who created it, in the token's organization, limited to the token's scopes (and to what
that person may still do). Tokens can never reach sign-in, users, roles, platform or token management.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import platform_session

TOKEN_PREFIX = "hrp_"
API_PREFIX = "/api/v1"
RATE_LIMIT = 120  # requests per token per minute
RATE_WINDOW = 60.0
LAST_USED_EVERY = timedelta(minutes=1)

# Paths (after /api/v1) an API token may never use.
FORBIDDEN_PREFIXES = ("/auth", "/users", "/roles", "/permissions", "/platform", "/connections/tokens",
                      "/connections/scopes")
# Permissions that are pointless or too powerful for a token (their endpoints are refused anyway).
NOT_GRANTABLE = {"user.manage", "role.manage", "apitoken.create"}


def generate() -> tuple[str, str, str]:
    """(full token, prefix, sha256 hash)."""
    prefix = secrets.token_hex(4)
    token = f"{TOKEN_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"
    return token, prefix, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_prefix(token: str) -> str | None:
    if not token.startswith(TOKEN_PREFIX):
        return None
    parts = token.split("_", 2)
    if len(parts) != 3 or len(parts[1]) != 8 or not parts[2]:
        return None
    return parts[1]


def forbidden_path(path: str) -> bool:
    p = path[len(API_PREFIX):] if path.startswith(API_PREFIX) else path
    return any(p == f or p.startswith(f + "/") for f in FORBIDDEN_PREFIXES)


# ------------------------------------------------------------------ rate limit (in memory, per token)
_hits: dict[str, deque] = {}


def rate_limited(prefix: str, now: float | None = None) -> float | None:
    """Record a request; return seconds to wait when the token is over its limit, else None."""
    now = time.monotonic() if now is None else now
    q = _hits.setdefault(prefix, deque())
    while q and now - q[0] >= RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return max(1.0, RATE_WINDOW - (now - q[0]))
    q.append(now)
    if len(_hits) > 5000:  # forget idle tokens
        for k in [k for k, v in _hits.items() if not v or now - v[-1] >= RATE_WINDOW][:2500]:
            _hits.pop(k, None)
    return None


def reset_rate_limits() -> None:
    _hits.clear()


# ------------------------------------------------------------------ lookup
async def lookup(token: str, ip: str | None = None):
    """The ApiToken row for a valid, unrevoked, unexpired token (looked up across organizations), else None."""
    from app.models import ApiToken

    prefix = parse_prefix(token)
    if prefix is None:
        return None
    async with platform_session() as s:
        row = await s.scalar(select(ApiToken).where(ApiToken.prefix == prefix))
        if row is None or not secrets.compare_digest(row.token_hash, hash_token(token)):
            return None
        now = datetime.now(UTC)
        if row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= now):
            return None
        if row.last_used_at is None or now - row.last_used_at >= LAST_USED_EVERY or row.last_used_ip != ip:
            row.last_used_at, row.last_used_ip = now, (ip or None)
            await s.commit()
        s.expunge(row)
        return row
