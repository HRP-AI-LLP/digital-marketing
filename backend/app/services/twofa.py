"""Two-step sign-in with an authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password...).

- TOTP per RFC 6238 (HMAC-SHA1, 6 digits, 30-second steps) using only the standard library.
- The secret is stored encrypted (app.core.crypto); recovery codes are stored as keyed hashes and work once.
- After a correct password, /auth/login returns a short-lived signed challenge instead of tokens; the code is
  checked at /auth/login/2fa. Wrong codes count per user: 5 in a row lock the second step for 10 minutes.
- Replays are refused: a code's time step must be newer than the last accepted one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode
from uuid import UUID

import jwt

from app.core.config import get_settings
from app.core.crypto import decrypt_json, encrypt_json
from app.models import Organization, User

ISSUER = "HRP Marketing Studio"
STEP = 30
DIGITS = 6
WINDOW = 1  # accept the previous and next step too (phone clocks drift)
MAX_FAILURES = 5
LOCK_MINUTES = 10
CHALLENGE_MINUTES = 5
RECOVERY_COUNT = 10
_RC_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


# ---- RFC 4226 / 6238 ---------------------------------------------------------------------------------
def hotp(key: bytes, counter: int, digits: int = DIGITS, algorithm: str = "sha1") -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), getattr(hashlib, algorithm)).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10 ** digits).zfill(digits)


def totp(key: bytes, at: float | None = None, *, step: int = STEP, digits: int = DIGITS, algorithm: str = "sha1") -> str:
    return hotp(key, int((time.time() if at is None else at) // step), digits, algorithm)


def new_secret() -> str:
    """A random 160-bit secret, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def secret_bytes(b32: str) -> bytes:
    s = b32.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def match_step(b32: str, code: str, *, at: float | None = None, last_step: int | None = None) -> int | None:
    """The time step `code` belongs to (within ±WINDOW), or None. Steps at or before last_step are refused."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    key = secret_bytes(b32)
    now_step = int((time.time() if at is None else at) // STEP)
    found = None
    for st in range(now_step - WINDOW, now_step + WINDOW + 1):
        if hmac.compare_digest(hotp(key, st), code):
            found = st
    if found is None or (last_step is not None and found <= last_step):
        return None
    return found


def otpauth_uri(b32: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}", safe="@:")
    return f"otpauth://totp/{label}?" + urlencode({"secret": b32, "issuer": ISSUER}, quote_via=quote)


# ---- secrets on the user row ---------------------------------------------------------------------------
def set_secret(user: User, b32: str) -> None:
    user.totp_secret_enc = encrypt_json({"secret": b32})


def get_secret(user: User) -> str | None:
    return decrypt_json(user.totp_secret_enc).get("secret") if user.totp_secret_enc else None


def _rc_hash(code: str) -> str:
    norm = "".join(ch for ch in code.lower() if ch.isalnum())
    return hmac.new(get_settings().secret_key.encode(), b"rc:" + norm.encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    codes = ["-".join("".join(secrets.choice(_RC_ALPHABET) for _ in range(5)) for _ in range(2))
             for _ in range(RECOVERY_COUNT)]
    user.recovery_codes = [_rc_hash(c) for c in codes]
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    h = _rc_hash(code or "")
    left = list(user.recovery_codes or [])
    for stored in left:
        if hmac.compare_digest(stored, h):
            left.remove(stored)
            user.recovery_codes = left  # reassign so SQLAlchemy sees the change
            return True
    return False


def clear(user: User) -> None:
    user.totp_enabled = False
    user.totp_secret_enc = None
    user.totp_enabled_at = None
    user.recovery_codes = []
    user.totp_last_step = None
    user.totp_failed_count = 0
    user.totp_locked_until = None


# ---- lockout -------------------------------------------------------------------------------------------
def locked_minutes(user: User) -> int:
    """Minutes left on a lockout (0 when not locked)."""
    if user.totp_locked_until and user.totp_locked_until > datetime.now(UTC):
        return max(1, int((user.totp_locked_until - datetime.now(UTC)).total_seconds() // 60) + 1)
    return 0


def record_failure(user: User) -> None:
    user.totp_failed_count = (user.totp_failed_count or 0) + 1
    if user.totp_failed_count >= MAX_FAILURES:
        user.totp_locked_until = datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)
        user.totp_failed_count = 0


def record_success(user: User) -> None:
    user.totp_failed_count = 0
    user.totp_locked_until = None


def check(user: User, code: str | None = None, recovery_code: str | None = None) -> str | None:
    """Verify an authenticator code (or a recovery code) for an enabled user. Returns 'totp' / 'recovery' on
    success, None on failure (and counts the failure). Callers check locked_minutes() first."""
    secret = get_secret(user)
    if code and secret:
        st = match_step(secret, code, last_step=user.totp_last_step)
        if st is not None:
            user.totp_last_step = st
            record_success(user)
            return "totp"
    elif recovery_code and use_recovery_code(user, recovery_code):
        record_success(user)
        return "recovery"
    record_failure(user)
    return None


# ---- login challenge -----------------------------------------------------------------------------------
def challenge_token(user: User, org_id: UUID | None, device: str | None) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user.id), "org": str(org_id) if org_id else None, "dev": device, "tv": user.token_version,
               "iat": now, "exp": now + timedelta(minutes=CHALLENGE_MINUTES), "typ": "2fa"}
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def read_challenge(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == "2fa" else None


# ---- organization policy -------------------------------------------------------------------------------
def security_settings(org: Organization | None) -> dict:
    cfg = dict(((org.publishing if org is not None else None) or {}).get("security") or {})
    return {"require_2fa": bool(cfg.get("require_2fa", False))}


def setup_required(user: User, org: Organization | None) -> bool:
    return bool(org is not None and security_settings(org)["require_2fa"] and not user.totp_enabled)
