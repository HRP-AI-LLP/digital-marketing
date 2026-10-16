"""Encryption for secrets at rest (API keys, OAuth tokens).

Uses ENCRYPTION_KEY from .env when present (a Fernet key), otherwise derives one from
SECRET_KEY. Keep .env safe: anyone with it can decrypt stored credentials.
"""

import base64
import hashlib
import json
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    if s.encryption_key:
        return Fernet(s.encryption_key.encode())
    digest = hashlib.sha256(("dm-secrets:" + s.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(data: dict | None) -> str | None:
    if not data:
        return None
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str | None) -> dict:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return {}


def mask(value: str | None) -> str | None:
    """Show only the last 4 characters of a secret."""
    if not value:
        return None
    return "•" * 6 + value[-4:] if len(value) > 6 else "•" * len(value)
