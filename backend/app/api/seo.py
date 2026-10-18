"""SEO articles: create (background writing), edit, score, rewrite sections, export, download, share as posts."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import AnalyticsSnapshot, MediaAsset, Organization, SeoArticle
from app.services import audit, seo
from app.services.jobs import enqueue
from app.services.llm.router import AllProvidersFailed, NoProviderError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/seo", tags=["seo"])

NO_MODEL = "Add an AI text model in Settings → Integrations to write articles."


# ------------------------------------------------------------------ schemas
class ArticleIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=200)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=15)
    audience: str | None = Field(None, max_length=500)
    word_target: int = Field(1500, ge=500, le=5000)
    tone: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=2000)
    search_intent: str | None = Field(None, pattern="^(informational|commercial|transactional|navigational)$")


class FaqItem(BaseModel):
    q: str = Field(max_length=500)
    a: str = Field(max_length=3000)


class ArticlePatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    keyword: str | None = Field(None, min_length=2, max_length=200)
    secondary_keywords: list[str] | None = None
    audience: str | None = Field(None, max_length=500)
    body_md: str | None = Field(None, max_length=200_000)
    meta_title: str | None = Field(None, max_length=200)
    meta_description: str | None = Field(None, max_length=400)
    faq: list[FaqItem] | None = None
    slug: str | None = Field(None, max_length=200)
    canonical_url: str | None = Field(None, max_length=500)
    status: str | None = Field(None, pattern="^(draft|ready)$")


class RewriteIn(BaseModel):
    heading: str = Field(min_length=1, max_length=300)
    instruction: str = Field("", max_length=1000)


class RepurposeIn(BaseModel):
    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


class KeywordsIn(BaseModel):
    seed: str | None = Field(None, max_length=200)
    background: bool = True


# ------------------------------------------------------------------ helpers
def _cover_out(asset: MediaAsset | None) -> dict | None:
    if asset is None:
        return None
    try:
        from app.services.media import asset_out

        return asset_out(asset)
    except Exception:  # noqa: BLE001 - media module may not be ready
        return {"id": str(asset.id), "kind": asset.kind, "alt_text": asset.alt_text, "url": None}


def article_out(a: SeoArticle, org: Organization | None, cover: MediaAsset | None = None, full: bool = True) -> dict:
    meta = seo.article_meta(a)
    out = {
        "id": a.id, "title": a.title, "slug": a.slug, "keyword": a.keyword, "secondary_keywords": a.secondary_keywords or [],
        "audience": a.audience, "status": a.status, "word_count": a.word_count, "seo_score": a.seo_score,
        "meta_title": a.meta_title, "meta_description": a.meta_description, "error": a.error,
        "export_path": a.export_path, "exported": bool(a.export_path), "cover_media_id": a.cover_media_id,
        "created_at": a.created_at, "updated_at": a.updated_at,
        "search_intent": meta.get("search_intent"), "word_target": meta.get("word_target"),
        "canonical_url": seo.canonical_url(a, org),
    }
    if full:
        out.update({
            "body_md": a.body_md or "", "faq": a.faq or [], "outline": seo.outline_sections(a), "seo_checks": a.seo_checks or [],
            "schema_json": a.schema_json or {}, "settings": {k: v for k, v in meta.items() if k != "kind"},
            "cover": _cover_out(cover), "headings": [{"level": lvl, "text": t} for lvl, t in seo.headings(a.body_md or "")],
        })
    return out


async def _get(ctx: Context, aid: uuid.UUID) -> SeoArticle:
    a = await ctx.session.scalar(select(SeoArticle).where(SeoArticle.id == aid))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found.")
    return a


async def _full(ctx: Context, a: SeoArticle) -> dict:
    cover = None
    if a.cover_media_id:
        cover = await ctx.session.scalar(select(MediaAsset).where(MediaAsset.id == a.cover_media_id))
    return article_out(a, ctx.org, cover)


# ------------------------------------------------------------------ articles
@router.get("/articles")
async def list_articles(ctx: Context = Depends(require("seo.view"))):
    rows = (await ctx.session.scalars(select(SeoArticle).order_by(SeoArticle.updated_at.desc()).limit(500))).all()
    return [article_out(a, ctx.org, full=False) for a in rows]


@router.post("/articles", status_code=201)
async def create_article(body: ArticleIn, ctx: Context = Depends(require("seo.manage"))):
    keyword = " ".join(body.keyword.split())
    a = SeoArticle(
        organization_id=ctx.org_id, title=keyword[:1].upper() + keyword[1:], keyword=keyword,
        slug=await seo.unique_slug(ctx.session, seo.slugify(keyword)),
        secondary_keywords=[" ".join(k.split()) for k in body.secondary_keywords if k.strip()][:15],
        audience=(body.audience or "").strip() or None, status="queued", outline=[],
    )
    seo.set_article_meta(a, word_target=body.word_target, tone=(body.tone or "").strip() or None,
                         notes=(body.notes or "").strip() or None, search_intent=body.search_intent)
    ctx.session.add(a)
    await ctx.session.flush()
    job = await enqueue(ctx.session, "seo.write", ctx.org_id, {"article_id": str(a.id)}, created_by=ctx.user.id,
                        dedupe_key=f"seo.write:{a.id}", max_attempts=2)
    audit.record(ctx.session, action="seo.article_created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="seo_article", entity_id=a.id, summary=f"Started article for “{keyword}”", ip=ctx.ip)
    await ctx.session.commit()
    return {"article": article_out(a, ctx.org), "job_id": job.id}


@router.get("/articles/{aid}")
async def get_article(aid: uuid.UUID, ctx: Context = Depends(require("seo.view"))):
    return await _full(ctx, await _get(ctx, aid))


@router.patch("/articles/{aid}")
async def update_article(aid: uuid.UUID, body: ArticlePatch, ctx: Context = Depends(require("seo.manage"))):
    a = await _get(ctx, aid)
    if a.status in ("queued", "writing"):
        raise HTTPException(status.HTTP_409_CONFLICT, "The article is still being written. Try again when it's done.")
    data = body.model_dump(exclude_unset=True)
    for f in ("title", "keyword", "audience", "body_md", "meta_title", "meta_description"):
        if f in data and data[f] is not None:
            setattr(a, f, data[f].strip() if f != "body_md" else data[f])
    if "secondary_keywords" in data and data["secondary_keywords"] is not None:
        a.secondary_keywords = [k.strip() for k in data["secondary_keywords"] if k.strip()][:15]
    if "faq" in data and data["faq"] is not None:
        a.faq = [{"q": f["q"].strip(), "a": f["a"].strip()} for f in data["faq"] if f["q"].strip()]
    if data.get("slug"):
        new = seo.slugify(data["slug"])
        if new != a.slug:
            old_slug = a.slug
            a.slug = await seo.unique_slug(ctx.session, new, exclude=a.id)
            from app.services import site_publish

            await site_publish.record_slug_change(ctx.session, a, old_slug, a.slug)  # redirects for live copies
    if "canonical_url" in data:
        seo.set_article_meta(a, canonical_url=(data["canonical_url"] or "").strip() or None)
    if data.get("status"):
        a.status = data["status"]
    elif a.status in ("exported", "error"):
        a.status = "draft"  # edited after export: needs a new export
    seo.recompute(a, ctx.org)
    await ctx.session.commit()
    return await _full(ctx, a)


@router.post("/articles/{aid}/score")
async def score_article(aid: uuid.UUID, ctx: Context = Depends(require("seo.view"))):
    a = await _get(ctx, aid)
    if "seo.manage" in ctx.permissions:
        seo.recompute(a, ctx.org)
        await ctx.session.commit()
        return {"seo_score": a.seo_score, "seo_checks": a.seo_checks, "word_count": a.word_count}
    score, checks = seo.seo_checks(a, ctx.org.website if ctx.org else None)
    return {"seo_score": score, "seo_checks": checks, "word_count": seo.word_count(a.body_md or "")}


@router.post("/articles/{aid}/rewrite-section")
async def rewrite_section(aid: uuid.UUID, body: RewriteIn, ctx: Context = Depends(require("seo.manage"))):
    a = await _get(ctx, aid)
    if not a.body_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The article has no text yet.")
    try:
        await seo.rewrite_section(ctx.session, a, body.heading, body.instruction)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except NoProviderError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, NO_MODEL)
    except AllProvidersFailed as e:
        await ctx.session.commit()  # keep the usage log
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:500])
    if a.status == "exported":
        a.status = "draft"
    await ctx.session.commit()
    return await _full(ctx, a)


@router.post("/articles/{aid}/export")
async def export_article(aid: uuid.UUID, ctx: Context = Depends(require("seo.manage"))):
    a = await _get(ctx, aid)
    if not a.body_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The article has no text yet.")
    job = await enqueue(ctx.session, "seo.export", ctx.org_id, {"article_id": str(a.id)}, created_by=ctx.user.id,
                        dedupe_key=f"seo.export:{a.id}")
    await ctx.session.commit()
    return {"job_id": job.id}


@router.get("/articles/{aid}/download")
async def download(aid: uuid.UUID, format: str = "mdx", as_json: bool = False, ctx: Context = Depends(require("seo.view"))):
    if format not in ("mdx", "html", "json"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose mdx, html or json.")
    a = await _get(ctx, aid)
    path = seo.export_file(a, format)
    if path is None:
        if "seo.manage" not in ctx.permissions:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This article hasn't been exported yet.")
        try:
            await seo.export_article(ctx.session, a)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
        await ctx.session.commit()
        path = seo.export_file(a, format)
        if path is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Export failed. Try Export again.")
    media = {"mdx": "text/markdown", "html": "text/html", "json": "application/json"}[format]
    name = f"{a.slug}.{format if format != 'json' else 'meta.json'}"
    if as_json:  # for clients that send a bearer token and build the file themselves
        return {"filename": name, "media_type": media, "content": path.read_text(encoding="utf-8")}
    return FileResponse(path, media_type=media, filename=name)


@router.post("/articles/{aid}/repurpose")
async def repurpose(aid: uuid.UUID, body: RepurposeIn, ctx: Context = Depends(require("seo.manage"))):
    a = await _get(ctx, aid)
    try:
        posts = await seo.repurpose(ctx.session, a, body.account_ids, ctx.user)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except NoProviderError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, NO_MODEL)
    except AllProvidersFailed as e:
        await ctx.session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:500])
    audit.record(ctx.session, action="seo.article_shared", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="seo_article", entity_id=a.id, summary=f"Created {len(posts)} post(s) from “{a.title[:80]}”",
                 ip=ctx.ip)
    await ctx.session.commit()
    try:
        from app.services.notify import notify_org

        await notify_org(ctx.org_id, "post_review", f"{len(posts)} post(s) waiting for approval",
                         f"Posts sharing the article “{a.title[:120]}”. Review and schedule them.",
                         link="/approvals", permission="content.approve")
    except Exception:  # noqa: BLE001
        log.exception("post_review notification failed")
    return {"count": len(posts), "post_ids": [p.id for p in posts],
            "posts": [{"id": p.id, "platform": p.platform, "post_type": p.post_type, "status": p.status,
                       "link_url": p.link_url} for p in posts]}


@router.delete("/articles/{aid}", status_code=204)
async def delete_article(aid: uuid.UUID, ctx: Context = Depends(require("seo.manage"))):
    a = await _get(ctx, aid)
    seo.remove_export(a)
    audit.record(ctx.session, action="seo.article_deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="seo_article", entity_id=a.id, summary=f"Deleted “{a.title[:80]}”", ip=ctx.ip)
    await ctx.session.delete(a)
    await ctx.session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ keyword ideas
@router.post("/keywords")
async def keyword_ideas(body: KeywordsIn, ctx: Context = Depends(require("seo.manage"))):
    seed = (body.seed or "").strip() or None
    if body.background:
        job = await enqueue(ctx.session, "seo.keywords", ctx.org_id, {"seed": seed}, created_by=ctx.user.id)
        await ctx.session.commit()
        return {"job_id": job.id}
    try:
        ideas = await seo.keyword_ideas(ctx.session, seed)
    except NoProviderError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, NO_MODEL)
    except AllProvidersFailed as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:500])
    ctx.session.add(AnalyticsSnapshot(organization_id=ctx.org_id, source="seo_ideas", key=(seed or "")[:200],
                                      data={"seed": seed, "ideas": ideas}))
    await ctx.session.commit()
    return {"ideas": ideas}


@router.get("/keywords/latest")
async def latest_ideas(ctx: Context = Depends(require("seo.view"))):
    s = await ctx.session.scalar(
        select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "seo_ideas").order_by(AnalyticsSnapshot.created_at.desc())
    )
    return {"seed": (s.data or {}).get("seed") if s else None, "ideas": (s.data or {}).get("ideas", []) if s else [],
            "created_at": s.created_at if s else None}
