"""Local file storage under storage/ (generated media, uploads, SEO exports).

Paths stored in the database are relative to the storage root, so the folder can move.
Layout: storage/orgs/<org_id>/<area>/<yyyy-mm>/<file>
"""

import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings


def root() -> Path:
    p = Path(get_settings().storage_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def org_dir(org_id: uuid.UUID | str, area: str) -> Path:
    p = root() / "orgs" / str(org_id) / area / datetime.now(UTC).strftime("%Y-%m")
    p.mkdir(parents=True, exist_ok=True)
    return p


def abs_path(rel: str) -> Path:
    p = (root() / rel).resolve()
    if root().resolve() not in p.parents and p != root().resolve():
        raise ValueError("Path escapes storage root")
    return p


def rel_path(p: Path) -> str:
    return p.resolve().relative_to(root().resolve()).as_posix()


def save_bytes(org_id, area: str, data: bytes, ext: str) -> tuple[str, str]:
    """Write bytes; returns (relative path, sha256)."""
    sha = hashlib.sha256(data).hexdigest()
    ext = "".join(ch for ch in ext.lower() if ch.isalnum())[:8] or "bin"
    p = org_dir(org_id, area) / f"{sha[:16]}-{uuid.uuid4().hex[:6]}.{ext}"
    p.write_bytes(data)
    return rel_path(p), sha


def guess_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
