"""Platform → Backups: list, download, back up now, and the backup settings. Platform admins only."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db import platform_session
from app.core.deps import Context, require_platform_admin
from app.models import BackupRun, User
from app.services import audit, backups

router = APIRouter(prefix="/platform/backups", tags=["backups"])


def _out(r: BackupRun, names: dict) -> dict:
    return {"id": str(r.id), "kind": r.kind, "status": r.status, "method": r.method, "file_name": r.file_name,
            "size_bytes": r.size_bytes, "media_file": r.media_file, "media_size_bytes": r.media_size_bytes,
            "copied_to": r.copied_to, "warning": r.warning, "error": r.error,
            "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "removed": r.removed_at is not None, "triggered_by": names.get(r.triggered_by)}


@router.get("")
async def list_backups(limit: int = 60, ctx: Context = Depends(require_platform_admin)):
    await backups.running()  # marks runs left over by a stopped app
    async with platform_session() as s:
        rows = (await s.scalars(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(min(limit, 200)))).all()
        ids = {r.triggered_by for r in rows if r.triggered_by}
        names = {u.id: u.full_name or u.email for u in (await s.scalars(select(User).where(User.id.in_(ids)))).all()} if ids else {}
    return {"runs": [_out(r, names) for r in rows], "settings": await backups.get_config(), "disk": backups.disk_usage()}


@router.post("/run", status_code=202)
async def back_up_now(tasks: BackgroundTasks, ctx: Context = Depends(require_platform_admin)):
    try:
        run = await backups.start_run("manual", ctx.user.id)
    except backups.BackupError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if backups._cloud():
        run = await backups.execute(run.id)  # Cloud Run only computes during a request: finish it here (< 1 min)
    else:
        tasks.add_task(backups.execute, run.id)
    async with platform_session() as s:
        audit.record(s, action="platform.backup_started", organization_id=None, actor=ctx.user,
                     summary="Started a database backup", ip=ctx.ip)
        await s.commit()
    return _out(run, {ctx.user.id: ctx.user.full_name})


@router.get("/{run_id}/download")
async def download(run_id: UUID, media: bool = False, ctx: Context = Depends(require_platform_admin)):
    async with platform_session() as s:
        run = await s.get(BackupRun, run_id)
        name = (run.media_file if media else run.file_name) if run else None
        if not name:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Backup not found.")
        audit.record(s, action="platform.backup_downloaded", organization_id=None, actor=ctx.user,
                     summary=f"Downloaded backup {name}", ip=ctx.ip)
        await s.commit()
    try:
        path = backups.resolve_file(name)
    except backups.BackupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return FileResponse(path, filename=name, media_type="application/zip" if media else "application/octet-stream")


class BackupSettingsIn(BaseModel):
    enabled: bool | None = None
    time: str | None = Field(None, max_length=5)
    keep_daily: int | None = Field(None, ge=1, le=90)
    keep_weekly: int | None = Field(None, ge=0, le=52)
    copy_to: str | None = Field(None, max_length=400)
    include_media: bool | None = None


@router.patch("/settings")
async def update_settings(body: BackupSettingsIn, ctx: Context = Depends(require_platform_admin)):
    changes = body.model_dump(exclude_none=True)
    try:
        if "time" in changes:
            h, m = backups.parse_time(changes["time"])
            changes["time"] = f"{h:02d}:{m:02d}"
        if "copy_to" in changes:
            changes["copy_to"] = backups.check_copy_folder(changes["copy_to"])
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    cfg = await backups.save_config(changes)
    async with platform_session() as s:
        audit.record(s, action="platform.backup_settings", organization_id=None, actor=ctx.user, ip=ctx.ip,
                     summary="Backup settings: " + (", ".join(f"{k.replace('_', ' ')} {v}" for k, v in changes.items())
                                                    or "no changes"))
        await s.commit()
    return cfg
