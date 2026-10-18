"""Website publishing jobs (seo.site_publish) and the WordPress / webhook connection testers."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.integrations import tester
from app.models import Organization, ProviderConfig, SeoArticle, SitePublication
from app.services import audit, site_publish
from app.services.jobs import JobContext, PermanentError, handler
from app.services.site_publish import SiteError

log = logging.getLogger(__name__)

GENERIC = ("Something went wrong while talking to the website. Try again; if it keeps failing, test the connection "
           "in Settings → Integrations.")


@tester("wordpress")
async def _test_wordpress(cfg: ProviderConfig) -> str:
    try:
        return await site_publish.test_wordpress(cfg)
    except SiteError as e:
        raise RuntimeError(str(e)) from None


@tester("webhook")
async def _test_webhook(cfg: ProviderConfig) -> str:
    try:
        return await site_publish.test_webhook(cfg)
    except SiteError as e:
        raise RuntimeError(str(e)) from None


def _uuid(v) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise PermanentError("Missing id") from None


@handler("seo.site_publish")
async def seo_site_publish(ctx: JobContext) -> dict:
    """payload: {article_id, provider_id, action: publish|unpublish, options: {...}}. Never retried automatically:
    a retry after a half-finished call could create a second post."""
    s = ctx.session
    article = await s.scalar(select(SeoArticle).where(SeoArticle.id == _uuid(ctx.payload.get("article_id"))))
    cfg = await s.scalar(select(ProviderConfig).where(ProviderConfig.id == _uuid(ctx.payload.get("provider_id"))))
    if article is None:
        raise PermanentError("The article no longer exists.")
    if cfg is None or site_publish.target_of(cfg) is None:
        raise PermanentError("The website connection was removed. Add it again in Settings → Integrations.")
    pub = await s.scalar(select(SitePublication).where(SitePublication.article_id == article.id,
                                                       SitePublication.provider_id == cfg.id))
    if pub is None:
        pub = SitePublication(organization_id=article.organization_id, article_id=article.id, provider_id=cfg.id,
                              target=site_publish.target_of(cfg), status="none", data={})
        s.add(pub)
        await s.flush()
    article_id, provider_id, org_id = article.id, cfg.id, article.organization_id
    target = site_publish.target_of(cfg)
    action = ctx.payload.get("action") or "publish"
    options = dict(ctx.payload.get("options") or {})
    where = site_publish.TARGET_LABEL.get(pub.target, "the website")
    try:
        if action == "unpublish":
            result = await site_publish.unpublish(s, article, cfg, pub, progress=ctx.progress)
        else:
            result = await site_publish.publish(s, article, cfg, pub, options, progress=ctx.progress)
    except SiteError as e:
        msg = str(e)
    except Exception:  # noqa: BLE001
        log.exception("website publish failed for article %s", article_id)
        msg = GENERIC
    else:
        msg = None
    if msg:
        await s.rollback()
        pub = await s.scalar(select(SitePublication).where(SitePublication.article_id == article_id,
                                                           SitePublication.provider_id == provider_id))
        if pub is None:
            pub = SitePublication(organization_id=org_id, article_id=article_id, provider_id=provider_id,
                                  target=target, status="none", data={})
            s.add(pub)
        pub.error = msg[:2000]
        if pub.status in ("none", "publishing"):
            pub.status = "error"
        await s.commit()
        raise PermanentError(msg)

    # after a live publish: optional IndexNow ping and a check of the live page
    if action == "publish" and pub.status == "published" and pub.remote_url:
        key = site_publish.fields(cfg).get("indexnow_key")
        if options.get("indexnow") and key:
            await ctx.progress("Notifying search engines")
            pub.data = {**(pub.data or {}), "indexnow": await site_publish.ping_indexnow(pub.remote_url, key)}
        if options.get("check", True):
            await ctx.progress("Checking the live page")
            check = await site_publish.check_page(pub.remote_url, article.meta_title or article.title,
                                                  site_publish.explicit_canonical(article) or pub.remote_url)
            pub.data = {**(pub.data or {}), "check": check}
    pub.updated_at = datetime.now(UTC)
    audit.record(s, action=f"seo.site_{'unpublished' if action == 'unpublish' else 'published'}",
                 organization_id=article.organization_id, entity_type="seo_article", entity_id=article.id,
                 summary=(f"Moved “{article.title[:80]}” back to draft on {where}" if action == "unpublish"
                          else f"Sent “{article.title[:80]}” to {where} ({pub.status})"),
                 details={"url": pub.remote_url, "provider_id": str(cfg.id)})
    await s.commit()
    if action == "publish" and pub.status == "published":
        try:
            from app.services.notify import notify_org

            org = await s.get(Organization, article.organization_id)
            await notify_org(article.organization_id, "seo_ready", f"Article live: {article.title[:120]}",
                             f"Published on {org.website if org and org.website else where}: {pub.remote_url}",
                             link=f"/seo/{article.id}", permission="seo.view")
        except Exception:  # noqa: BLE001
            log.exception("publish notification failed")
    warnings = len((pub.data or {}).get("warnings") or [])
    label = {"published": "Published", "scheduled": "Scheduled", "draft": "Saved as a draft",
             "unpublished": "Removed from the website"}.get(pub.status, "Done")
    return {**result, "message": f"{label}." + (f" {warnings} thing(s) to check." if warnings else "")}
