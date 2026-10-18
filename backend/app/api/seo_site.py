"""Publish SEO articles to the company website: connections, publish / update / unpublish (background job),
live-page checks, IndexNow, redirects and the "Download for website" bundle."""

import base64
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import ProviderConfig, SeoArticle, SitePublication, SiteRedirect
from app.services import audit, site_publish
from app.services.jobs import enqueue
from app.services.site_publish import SiteError

router = APIRouter(prefix="/seo", tags=["seo-website"])

NO_CONNECTION = "Connect your website first in Settings → Integrations → Website."


class PublishIn(BaseModel):
    provider_id: uuid.UUID
    status: str = Field("publish", pattern="^(draft|publish|future)$")
    publish_at: datetime | None = None
    categories: list[int] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=20)
    author: int | None = None
    indexnow: bool = False
    check: bool = True


class ProviderRef(BaseModel):
    provider_id: uuid.UUID


class RedirectPatch(BaseModel):
    status: str = Field(pattern="^(done|manual|pending)$")


# ------------------------------------------------------------------ helpers
async def _article(ctx: Context, aid: uuid.UUID) -> SeoArticle:
    a = await ctx.session.scalar(select(SeoArticle).where(SeoArticle.id == aid))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found.")
    return a


async def _connection(ctx: Context, pid: uuid.UUID) -> ProviderConfig:
    cfg = await ctx.session.scalar(select(ProviderConfig).where(ProviderConfig.id == pid))
    if cfg is None or site_publish.target_of(cfg) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_CONNECTION)
    return cfg


async def _pub(ctx: Context, a: SeoArticle, cfg: ProviderConfig) -> SitePublication | None:
    return await ctx.session.scalar(select(SitePublication).where(SitePublication.article_id == a.id,
                                                                  SitePublication.provider_id == cfg.id))


async def _gsc_site(ctx: Context) -> str | None:
    try:
        from app.services.analytics import google_status

        return (await google_status(ctx.session)).get("gsc_site")
    except Exception:  # noqa: BLE001 - Search Console isn't required
        return None


async def _running_job(ctx: Context, pub: SitePublication | None) -> dict | None:
    if not pub or not pub.job_id:
        return None
    from app.models import Job

    j = await ctx.session.get(Job, pub.job_id)
    if j and j.status in ("queued", "running"):
        return {"id": j.id, "status": j.status, "progress": j.progress}
    return None


async def website_state(ctx: Context, a: SeoArticle) -> dict:
    cfgs = await site_publish.connections(ctx.session)
    pubs = {p.provider_id: p for p in (await ctx.session.scalars(
        select(SitePublication).where(SitePublication.article_id == a.id))).all()}
    gsc = await _gsc_site(ctx)
    items = []
    for cfg in cfgs:
        pub = pubs.get(cfg.id)
        items.append({
            "connection": site_publish.connection_out(cfg),
            "publication": site_publish.publication_out(pub, a, cfg, gsc) if pub else None,
            "job": await _running_job(ctx, pub),
        })
    redirects = (await ctx.session.scalars(select(SiteRedirect).where(SiteRedirect.article_id == a.id)
                                           .order_by(SiteRedirect.created_at.desc()))).all()
    return {"items": items, "redirects": [site_publish.redirect_out(r) for r in redirects],
            "can_publish": "seo.publish" in ctx.permissions}


# ------------------------------------------------------------------ connections
@router.get("/website/connections")
async def list_connections(ctx: Context = Depends(require("seo.view"))):
    return [site_publish.connection_out(c) for c in await site_publish.connections(ctx.session)]


@router.get("/website/connections/{pid}/options")
async def connection_options(pid: uuid.UUID, ctx: Context = Depends(require("seo.publish"))):
    """WordPress categories, tags, authors and detected plugins for the publish form."""
    cfg = await _connection(ctx, pid)
    if site_publish.target_of(cfg) != "wordpress":
        return {"site": site_publish.site_of(cfg), "plugins": {}, "categories": [], "tags": [], "authors": [], "errors": []}
    try:
        out = await site_publish.wp_options(cfg)
    except SiteError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    await ctx.session.commit()  # remembers detected plugins
    return out


@router.post("/website/connections/{pid}/indexnow-key")
async def indexnow_key(pid: uuid.UUID, ctx: Context = Depends(require("seo.publish"))):
    cfg = await _connection(ctx, pid)
    key = site_publish.ensure_indexnow_key(cfg)
    await ctx.session.commit()
    return site_publish.indexnow_instructions(cfg, key)


# ------------------------------------------------------------------ article on the website
@router.get("/articles/{aid}/website")
async def article_website(aid: uuid.UUID, ctx: Context = Depends(require("seo.view"))):
    return await website_state(ctx, await _article(ctx, aid))


@router.post("/articles/{aid}/website/publish")
async def publish_article(aid: uuid.UUID, body: PublishIn, ctx: Context = Depends(require("seo.publish"))):
    a = await _article(ctx, aid)
    cfg = await _connection(ctx, body.provider_id)
    if a.status in ("queued", "writing"):
        raise HTTPException(status.HTTP_409_CONFLICT, "The article is still being written.")
    if not a.body_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Write the article before publishing it.")
    if not cfg.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This website connection is turned off in Settings → Integrations.")
    if body.status == "future":
        if body.publish_at is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose the date and time the article should go live.")
        when = body.publish_at if body.publish_at.tzinfo else body.publish_at.replace(tzinfo=UTC)
        if when <= datetime.now(UTC):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The publish time must be in the future.")
    pub = await _pub(ctx, a, cfg)
    if await _running_job(ctx, pub):
        raise HTTPException(status.HTTP_409_CONFLICT, "This article is already being sent to the website.")
    if pub is None:
        pub = SitePublication(organization_id=ctx.org_id, article_id=a.id, provider_id=cfg.id,
                              target=site_publish.target_of(cfg), status="none", data={})
        ctx.session.add(pub)
        await ctx.session.flush()
    options = body.model_dump(exclude={"provider_id"}, mode="json")
    job = await enqueue(ctx.session, "seo.site_publish", ctx.org_id,
                        {"article_id": str(a.id), "provider_id": str(cfg.id), "action": "publish", "options": options},
                        created_by=ctx.user.id, max_attempts=1, priority=3)
    pub.job_id, pub.error = job.id, None
    audit.record(ctx.session, action="seo.site_publish_requested", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="seo_article", entity_id=a.id, ip=ctx.ip,
                 summary=f"Asked to {'update' if pub.remote_id else 'publish'} “{a.title[:80]}” on {cfg.name} ({body.status})")
    await ctx.session.commit()
    return {"job_id": job.id}


@router.post("/articles/{aid}/website/unpublish")
async def unpublish_article(aid: uuid.UUID, body: ProviderRef, ctx: Context = Depends(require("seo.publish"))):
    a = await _article(ctx, aid)
    cfg = await _connection(ctx, body.provider_id)
    pub = await _pub(ctx, a, cfg)
    if pub is None or site_publish.effective_status(pub) not in ("published", "scheduled"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This article isn't live on that website.")
    if await _running_job(ctx, pub):
        raise HTTPException(status.HTTP_409_CONFLICT, "This article is already being sent to the website.")
    job = await enqueue(ctx.session, "seo.site_publish", ctx.org_id,
                        {"article_id": str(a.id), "provider_id": str(cfg.id), "action": "unpublish"},
                        created_by=ctx.user.id, max_attempts=1, priority=3)
    pub.job_id = job.id
    await ctx.session.commit()
    return {"job_id": job.id}


@router.post("/articles/{aid}/website/check")
async def check_live_page(aid: uuid.UUID, body: ProviderRef, ctx: Context = Depends(require("seo.publish"))):
    """Fetch the live page: is it reachable (200) with the expected title and canonical link?"""
    a = await _article(ctx, aid)
    cfg = await _connection(ctx, body.provider_id)
    pub = await _pub(ctx, a, cfg)
    if pub is None or not pub.remote_url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Publish the article first.")
    check = await site_publish.check_page(pub.remote_url, a.meta_title or a.title,
                                          site_publish.explicit_canonical(a) or pub.remote_url)
    pub.data = {**(pub.data or {}), "check": check}
    await ctx.session.commit()
    return check


@router.post("/articles/{aid}/website/indexnow")
async def notify_indexnow(aid: uuid.UUID, body: ProviderRef, ctx: Context = Depends(require("seo.publish"))):
    a = await _article(ctx, aid)
    cfg = await _connection(ctx, body.provider_id)
    pub = await _pub(ctx, a, cfg)
    if pub is None or not pub.remote_url or site_publish.effective_status(pub) != "published":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Publish the article first.")
    key = site_publish.fields(cfg).get("indexnow_key")
    if not key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Set up an IndexNow key first (see “Set up IndexNow”).")
    result = await site_publish.ping_indexnow(pub.remote_url, key)
    pub.data = {**(pub.data or {}), "indexnow": result}
    await ctx.session.commit()
    return result


@router.get("/articles/{aid}/bundle")
async def download_bundle(aid: uuid.UUID, as_json: bool = False, ctx: Context = Depends(require("seo.view"))):
    """'Download for website': index.html with every head tag and the schema, the cover image, MDX and meta.json."""
    if not ({"seo.manage", "seo.publish"} & ctx.permissions):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You need the 'seo.publish' permission for this.")
    a = await _article(ctx, aid)
    if not a.body_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The article has no text yet.")
    try:
        name, data = await site_publish.build_bundle(ctx.session, a)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    await ctx.session.commit()
    if as_json:
        return {"filename": name, "media_type": "application/zip", "content_b64": base64.b64encode(data).decode()}
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ redirects
@router.get("/redirects")
async def list_redirects(format: str | None = None, article_id: uuid.UUID | None = None,
                         ctx: Context = Depends(require("seo.view"))):
    q = select(SiteRedirect).order_by(SiteRedirect.created_at.desc())
    if article_id:
        q = q.where(SiteRedirect.article_id == article_id)
    rows = list((await ctx.session.scalars(q)).all())
    if not format:
        return [site_publish.redirect_out(r) for r in rows]
    if format not in site_publish.REDIRECT_FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose htaccess, nginx, netlify, vercel or csv.")
    name, media, text = site_publish.export_redirects(rows, format)
    return {"filename": name, "media_type": media, "content": text}


@router.post("/redirects/push")
async def push_redirects(body: ProviderRef, ctx: Context = Depends(require("seo.publish"))):
    cfg = await _connection(ctx, body.provider_id)
    if site_publish.target_of(cfg) != "wordpress":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Redirects can only be pushed to WordPress. Copy them for your site.")
    try:
        out = await site_publish.push_redirects(ctx.session, cfg, include_manual=True)
    except SiteError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    await ctx.session.commit()
    return out


@router.patch("/redirects/{rid}")
async def update_redirect(rid: uuid.UUID, body: RedirectPatch, ctx: Context = Depends(require("seo.publish"))):
    r = await ctx.session.scalar(select(SiteRedirect).where(SiteRedirect.id == rid))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Redirect not found.")
    r.status = body.status
    await ctx.session.commit()
    return site_publish.redirect_out(r)


@router.delete("/redirects/{rid}", status_code=204)
async def delete_redirect(rid: uuid.UUID, ctx: Context = Depends(require("seo.publish"))):
    r = await ctx.session.scalar(select(SiteRedirect).where(SiteRedirect.id == rid))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Redirect not found.")
    await ctx.session.delete(r)
    await ctx.session.commit()
    return Response(status_code=204)
