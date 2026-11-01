"""File storage: a local folder, optionally backed by an S3-compatible bucket (Cloudflare R2 in the cloud).

Paths stored in the database are relative to the storage root, so the folder can move.
Layout: storage/orgs/<org_id>/<area>/<yyyy-mm>/<file>

Locally (no bucket configured) the folder is the storage. In the cloud the bucket is the storage and the local
folder is a cache on the container's disk: save_bytes() writes both, abs_path() downloads a file (or a folder)
from the bucket the first time it is needed, delete() removes both. Object keys equal the relative paths.
"""

import hashlib
import logging
import mimetypes
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings

log = logging.getLogger("storage")
_client = None
_client_lock = threading.Lock()


def bucket() -> str:
    return (get_settings().s3_bucket or "").strip()


def remote() -> bool:
    return bool(bucket())


def _s3():
    """boto3 client for the bucket (thread-safe, created once)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import boto3
                from botocore.config import Config

                s = get_settings()
                _client = boto3.client(
                    "s3", endpoint_url=s.s3_endpoint or None, region_name=s.s3_region or "auto",
                    aws_access_key_id=s.s3_access_key_id or None, aws_secret_access_key=s.s3_secret_access_key or None,
                    config=Config(retries={"max_attempts": 4, "mode": "standard"}, signature_version="s3v4"))
    return _client


def _upload(p: Path, key: str) -> None:
    _s3().upload_file(str(p), bucket(), key, ExtraArgs={"ContentType": guess_mime(key)})


def _download(key: str, p: Path) -> bool:
    from botocore.exceptions import ClientError

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".part-{uuid.uuid4().hex[:6]}")
    try:
        _s3().download_file(bucket(), key, str(tmp))
    except ClientError as e:
        tmp.unlink(missing_ok=True)
        if str(e.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    tmp.replace(p)
    return True


def _download_prefix(prefix: str, folder: Path) -> bool:
    """Fetch every object under prefix/ into folder (an exported article folder, the music library)."""
    found = False
    pages = _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket(), Prefix=prefix.rstrip("/") + "/")
    for page in pages:
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            found = True
            dest = root() / key
            if not dest.exists():
                _download(key, dest)
    return found


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
    if remote() and not p.exists() and p != root().resolve():
        key = p.relative_to(root().resolve()).as_posix()
        try:
            if not _download(key, p):
                _download_prefix(key, p)  # a folder (SEO export)
        except Exception:  # noqa: BLE001 - the caller sees a missing file
            log.warning("couldn't fetch %s from the bucket", key, exc_info=True)
    return p


def local_dir(name: str) -> Path:
    """A top-level folder (music library, …), filled from the bucket once per container."""
    p = root() / name
    if remote() and not (p / ".synced").exists():
        try:
            _download_prefix(name, p)
            p.mkdir(parents=True, exist_ok=True)
            (p / ".synced").write_text(str(time.time()))
        except Exception:  # noqa: BLE001
            log.warning("couldn't fetch %s/ from the bucket", name, exc_info=True)
    return p


def delete(rel: str | None) -> None:
    """Remove a stored file (and its bucket copy). Never raises."""
    if not rel:
        return
    try:
        abs_local = (root() / rel).resolve()
        if root().resolve() in abs_local.parents:
            abs_local.unlink(missing_ok=True)
        if remote():
            _s3().delete_object(Bucket=bucket(), Key=Path(rel).as_posix())
    except Exception:  # noqa: BLE001
        log.warning("couldn't delete %s", rel, exc_info=True)


def upload_tree(folder: Path) -> None:
    """Copy a folder written in place (an SEO export) to the bucket."""
    if not remote():
        return
    for f in folder.rglob("*"):
        if f.is_file():
            _upload(f, rel_path(f))


def prune_cache(max_bytes: int = 400 * 1024 * 1024, keep: tuple[str, ...] = ("music",)) -> int:
    """Cloud: the container's disk lives in memory, so keep the local cache small (oldest files go first)."""
    if not remote():
        return 0
    files = [f for f in (root() / "orgs").rglob("*") if f.is_file()] if (root() / "orgs").exists() else []
    files += [f for f in (root() / "seo").rglob("*") if f.is_file()] if (root() / "seo").exists() else []
    total = sum(f.stat().st_size for f in files)
    removed = 0
    for f in sorted(files, key=lambda x: x.stat().st_atime):
        if total <= max_bytes:
            break
        size = f.stat().st_size
        f.unlink(missing_ok=True)
        total -= size
        removed += 1
    return removed


def rel_path(p: Path) -> str:
    return p.resolve().relative_to(root().resolve()).as_posix()


def save_bytes(org_id, area: str, data: bytes, ext: str) -> tuple[str, str]:
    """Write bytes; returns (relative path, sha256)."""
    sha = hashlib.sha256(data).hexdigest()
    ext = "".join(ch for ch in ext.lower() if ch.isalnum())[:8] or "bin"
    p = org_dir(org_id, area) / f"{sha[:16]}-{uuid.uuid4().hex[:6]}.{ext}"
    p.write_bytes(data)
    rel = rel_path(p)
    if remote():
        _upload(p, rel)  # the bucket is the real storage in the cloud; fail loudly if it can't be written
    return rel, sha


def guess_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
