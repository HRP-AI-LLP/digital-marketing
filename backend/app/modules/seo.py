"""SEO background jobs: seo.write (article pipeline), seo.export (files), seo.keywords (ideas)."""

import logging
import uuid

from sqlalchemy import select

from app.models import AnalyticsSnapshot, Organization, SeoArticle
from app.services import seo
from app.services.jobs import JobContext, PermanentError, handler
from app.services.llm.router import NoProviderError

log = logging.getLogger(__name__)

NO_MODEL = "Add an AI text model in Settings → Integrations"


async def _article(ctx: JobContext) -> SeoArticle:
    try:
        aid = uuid.UUID(str(ctx.payload.get("article_id")))
    except ValueError:
        raise PermanentError("Missing article id")
    article = await ctx.session.scalar(select(SeoArticle).where(SeoArticle.id == aid))
    if article is None:
        raise PermanentError("The article no longer exists")
    return article


AUTO_ARTICLE_MIN_SCORE = 60  # articles scoring lower stay drafts even with auto-publishing on


async def _auto_publish(ctx: JobContext, article: SeoArticle) -> str | None:
    """Settings → Organization → Auto-approval → "Publish finished articles to the website". Returns a sentence for
    the notification, or None when auto-publishing is off."""
    from app.models import SitePublication
    from app.services import site_publish
    from app.services.jobs import enqueue

    org = await ctx.session.get(Organization, article.organization_id)
    if not ((org.publishing if org else None) or {}).get("auto_publish_articles"):
        return None
    if (article.seo_score or 0) < AUTO_ARTICLE_MIN_SCORE or not article.body_md:
        return (f"It stays a draft because its SEO score is below {AUTO_ARTICLE_MIN_SCORE}; "
                "improve it, then publish it from the article page.")
    conns = [c for c in await site_publish.connections(ctx.session) if c.enabled]
    if not conns:
        return "Auto-publishing is on, but no website is connected (Settings → Integrations → Website), so it stays a draft."
    cfg = conns[0]
    pub = await ctx.session.scalar(select(SitePublication).where(SitePublication.article_id == article.id,
                                                                 SitePublication.provider_id == cfg.id))
    if pub is None:
        pub = SitePublication(organization_id=article.organization_id, article_id=article.id, provider_id=cfg.id,
                              target=site_publish.target_of(cfg), status="none", data={})
        ctx.session.add(pub)
        await ctx.session.flush()
    options = {"status": "publish", "publish_at": None, "categories": [], "tags": [], "author": None,
               "indexnow": False, "check": True}
    job = await enqueue(ctx.session, "seo.site_publish", article.organization_id,
                        {"article_id": str(article.id), "provider_id": str(cfg.id), "action": "publish",
                         "options": options}, max_attempts=1, priority=3)
    pub.job_id, pub.error = job.id, None
    await ctx.session.commit()
    return f"Auto-publishing is on, so it's being published to {cfg.name} now."


@handler("seo.write")
async def seo_write(ctx: JobContext) -> dict:
    article = await _article(ctx)
    article.status, article.error = "writing", None
    await ctx.session.commit()
    await ctx.progress("Starting")
    try:
        await seo.write_article(ctx.session, article, progress=ctx.progress)
    except NoProviderError:
        await ctx.session.rollback()
        article = await _article(ctx)
        article.status, article.error = "error", NO_MODEL
        await ctx.session.commit()
        raise PermanentError(NO_MODEL)
    except Exception as e:
        await ctx.session.rollback()
        article = await _article(ctx)
        article.status, article.error = "error", f"{type(e).__name__}: {e}"[:1000]
        await ctx.session.commit()
        raise
    article.status = "draft"
    await ctx.session.commit()

    if not article.cover_media_id:
        await ctx.progress("Creating a cover image")
        try:
            asset_id = await seo.request_cover(article)
        except Exception:  # noqa: BLE001
            log.exception("cover image for article %s failed", article.id)
            asset_id = None
        if asset_id:
            article.cover_media_id = asset_id
            seo.recompute(article, await ctx.session.get(Organization, article.organization_id))
            await ctx.session.commit()

    auto = await _auto_publish(ctx, article)
    try:
        from app.services.notify import notify_org

        await notify_org(ctx.org_id, "seo_ready", f"Article ready: {article.title}",
                         f"SEO score {article.seo_score}/100 · {article.word_count} words. "
                         + (auto or "Review and export it."),
                         link=f"/seo/{article.id}", permission="seo.view")
    except Exception:  # noqa: BLE001
        log.exception("seo_ready notification failed")
    from app.services import webhooks

    await webhooks.emit(ctx.org_id, "article.ready", lambda: {
        "id": article.id, "title": article.title, "slug": article.slug, "keyword": article.keyword,
        "status": article.status, "seo_score": article.seo_score, "word_count": article.word_count,
        "app_url": webhooks.app_link(f"/seo/{article.id}")})
    return {"article_id": str(article.id), "seo_score": article.seo_score, "word_count": article.word_count,
            "message": f"Article written: {article.word_count} words, SEO score {article.seo_score}."}


@handler("seo.export")
async def seo_export(ctx: JobContext) -> dict:
    article = await _article(ctx)
    await ctx.progress("Writing MDX, HTML and meta files")
    try:
        folder = await seo.export_article(ctx.session, article)
    except ValueError as e:
        raise PermanentError(str(e))
    return {"article_id": str(article.id), "export_path": article.export_path, "files": sorted(p.name for p in folder.iterdir()),
            "message": "Exported."}


@handler("seo.keywords")
async def seo_keywords(ctx: JobContext) -> dict:
    seed = (ctx.payload.get("seed") or "").strip() or None
    await ctx.progress("Looking at your site, competitors and Search Console")
    try:
        ideas = await seo.keyword_ideas(ctx.session, seed)
    except NoProviderError:
        raise PermanentError(NO_MODEL)
    ctx.session.add(AnalyticsSnapshot(organization_id=ctx.org_id, source="seo_ideas", key=(seed or "")[:200],
                                      data={"seed": seed, "ideas": ideas}))
    return {"ideas": ideas, "message": f"{len(ideas)} keyword ideas."}
