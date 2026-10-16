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

    try:
        from app.services.notify import notify_org

        await notify_org(ctx.org_id, "seo_ready", f"Article ready: {article.title}",
                         f"SEO score {article.seo_score}/100 · {article.word_count} words. Review and export it.",
                         link=f"/seo/{article.id}", permission="seo.view")
    except Exception:  # noqa: BLE001
        log.exception("seo_ready notification failed")
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
