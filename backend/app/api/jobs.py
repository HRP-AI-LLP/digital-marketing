import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.deps import Context, get_context, require

# Permission needed to see or retry each family of background task.
AREA_PERMS = {"post.": "content.view", "media.": "media.manage", "intel.": "intel.view", "seo.": "seo.view",
              "account.": "integration.view", "keywords.": "keywords.view"}
RETRY_PERMS = {"post.": "content.publish", "media.": "media.manage", "intel.": "intel.manage", "seo.": "seo.manage",
               "account.": "integration.manage", "keywords.": "keywords.manage"}


def _allowed(ctx: Context, kind: str, table: dict) -> bool:
    for prefix, perm in table.items():
        if kind.startswith(prefix):
            return perm in ctx.permissions
    return "integration.manage" in ctx.permissions
from app.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


def job_out(j: Job) -> dict:
    return {"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "error": j.error,
            "result": j.result, "attempts": j.attempts, "payload": j.payload, "created_at": j.created_at,
            "finished_at": j.finished_at, "run_after": j.run_after}


@router.get("")
async def list_jobs(kind: str | None = None, status_: str | None = None, limit: int = 50,
                    ctx: Context = Depends(require("content.view"))):
    q = select(Job).where(Job.organization_id == ctx.org_id)
    if kind:
        q = q.where(Job.kind.startswith(kind))
    if status_:
        q = q.where(Job.status == status_)
    rows = (await ctx.session.scalars(q.order_by(Job.created_at.desc()).limit(min(limit, 200)))).all()
    return [job_out(j) for j in rows if _allowed(ctx, j.kind, AREA_PERMS)]


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, ctx: Context = Depends(get_context)):
    j = await ctx.session.scalar(select(Job).where(Job.id == job_id))
    if j is None or not (_allowed(ctx, j.kind, AREA_PERMS) or j.created_by == ctx.user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found.")
    return job_out(j)


@router.post("/{job_id}/retry")
async def retry_job(job_id: uuid.UUID, ctx: Context = Depends(get_context)):
    j = await ctx.session.scalar(select(Job).where(Job.id == job_id))
    if j is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found.")
    if not _allowed(ctx, j.kind, RETRY_PERMS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission to retry this task.")
    if j.status not in ("failed", "cancelled"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only failed tasks can be retried.")
    j.status, j.run_after, j.attempts, j.error = "queued", datetime.now(UTC), 0, None
    await ctx.session.commit()
    return job_out(j)
