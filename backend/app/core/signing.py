"""Short-lived signed URLs for files (so <img>/<video> tags work without auth headers)."""

import hashlib
import hmac
import time
import uuid

from app.core.config import get_settings


def _sig(asset_id: str, org_id: str, exp: int) -> str:
    msg = f"{asset_id}:{org_id}:{exp}".encode()
    return hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def media_url(asset_id: uuid.UUID | str, org_id: uuid.UUID | str, ttl: int = 6 * 3600) -> str:
    exp = int(time.time()) + ttl
    exp -= exp % 600  # stable for 10 minutes so browsers can cache
    return f"/api/v1/media/file/{asset_id}?o={org_id}&e={exp}&s={_sig(str(asset_id), str(org_id), exp)}"


def verify(asset_id: str, org_id: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(_sig(asset_id, org_id, exp), sig)
