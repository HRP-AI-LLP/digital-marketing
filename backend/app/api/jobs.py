import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
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


# What each task does, in words people use (unknown kinds fall back to the technical name)
LABELS = {
    "post.generate": "Write a post (text and media)", "post.publish": "Publish a post", "post.metrics": "Refresh post results",
    "media.generate": "Create an image or video", "account.refresh": "Refresh a connected account",
    "inbox.sync": "Fetch new comments and messages", "inbox.listen": "Listen for mentions",
    "intel.crawl": "Read the company website", "intel.competitor_crawl": "Read a competitor's website",
    "intel.competitors": "Find competitors", "intel.extract": "Build the company profile", "intel.ideas": "Suggest topics",
    "intel.analytics": "Analyse company data", "intel.audit": "Website audit (company profile)", "intel.run_all": "Company intelligence",
    "seo.write": "Write an SEO article", "seo.keywords": "Find keywords for an article", "seo.export": "Export an article",
    "seo.site_publish": "Publish an article to the website", "keywords.research": "Keyword research",
    "keywords.cluster": "Group keywords", "keywords.gap": "Keyword gap analysis", "keywords.rank_check": "Check keyword rankings",
    "research.run": "Daily research", "siteaudit.run": "Site audit", "presence.audit": "Social presence check",
    "aivis.run": "AI visibility check", "aivis.llms": "Write llms.txt", "aivis.recommend": "AI visibility advice",
    "aivis.suggest": "AI visibility suggestions", "analytics.collect": "Collect analytics", "analytics.report": "Send a report",
    "analytics.advisor": "Analytics advice", "listening.run": "Social listening", "listening.digest": "Listening summary",
    "listening.summary": "Listening summary", "brand.suggest": "Suggest brand settings", "canva.import": "Import a Canva design",
    "webhook.deliver": "Send a webhook", "security.signin_notice": "Sign-in notice email",
}


def job_label(kind: str) -> str:
    return LABELS.get(kind) or kind.replace(".", ": ").replace("_", " ").capitalize()


def job_link(j: Job) -> str | None:
    """Where to look at (or fix) what the task was about."""
    p = j.payload or {}
    if p.get("post_id"):
        return f"/posts/{p['post_id']}"
    if p.get("article_id") and j.kind.startswith("seo."):
        return f"/seo/{p['article_id']}"
    if j.kind.startswith("account.") or j.kind.startswith("inbox."):
        return "/settings/accounts" if j.kind.startswith("account.") else "/inbox"
    if j.kind.startswith("intel."):
        return "/intelligence"
    if j.kind.startswith("siteaudit."):
        return "/site-audit"
    return None


def job_out(j: Job) -> dict:
    return {"id": j.id, "kind": j.kind, "label": job_label(j.kind), "link": job_link(j), "status": j.status,
            "progress": j.progress, "error": j.error, "result": j.result, "attempts": j.attempts, "payload": j.payload,
            "created_at": j.created_at, "finished_at": j.finished_at, "run_after": j.run_after}


@router.get("")
async def list_jobs(kind: str | None = None, status_: str | None = None, status: str | None = None,
                    since: datetime | None = None, limit: int = 50, ctx: Context = Depends(require("content.view"))):
    """Background tasks, newest first. status=failed&since=… is what the dashboard warning links to."""
    q = select(Job).where(Job.organization_id == ctx.org_id)
    if kind:
        q = q.where(Job.kind.startswith(kind))
    st = status or status_
    if st:
        q = q.where(Job.status == st)
    if since:
        q = q.where(Job.finished_at >= since)
    order = Job.finished_at.desc().nulls_last() if st == "failed" else Job.created_at.desc()
    rows = (await ctx.session.scalars(q.order_by(order).limit(min(limit, 200)))).all()
    return [job_out(j) for j in rows if _allowed(ctx, j.kind, AREA_PERMS)]


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, ctx: Context = Depends(get_context)):
    j = await ctx.session.scalar(select(Job).where(Job.id == job_id))
    if j is None or not (_allowed(ctx, j.kind, AREA_PERMS) or j.created_by == ctx.user.id):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Task not found.")
    return job_out(j)


@router.post("/{job_id}/retry")
async def retry_job(job_id: uuid.UUID, ctx: Context = Depends(get_context)):
    j = await ctx.session.scalar(select(Job).where(Job.id == job_id))
    if j is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Task not found.")
    if not _allowed(ctx, j.kind, RETRY_PERMS):
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "You don't have permission to retry this task.")
    if j.status not in ("failed", "cancelled"):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "Only failed tasks can be retried.")
    j.status, j.run_after, j.attempts, j.error = "queued", datetime.now(UTC), 0, None
    await ctx.session.commit()
    return job_out(j)
