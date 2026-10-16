"""Company Intelligence API: company profile, knowledge sources, competitors, presence audits and topic ideas.

Long-running actions return {"job_id"}; the UI polls /api/v1/jobs/{id}.
"""

import re
import uuid
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.jobs import job_out
from app.core import storage
from app.core.deps import Context, require
from app.models import (
    CompanyEntity,
    CompanyProfile,
    Competitor,
    ContentPillar,
    Job,
    KnowledgeSource,
    Opportunity,
    PresenceAudit,
    ProviderConfig,
)
from app.services import audit, crawler, docparse, knowledge
from app.services.jobs import enqueue
from app.services.knowledge import normalize_name
from app.services.llm.router import resolve_chain

router = APIRouter(prefix="/intel", tags=["intelligence"])

VIEW = "intel.view"
MANAGE = "intel.manage"
EntityType = Literal["product", "studio", "service", "use_case", "client", "industry", "case_study", "team"]
PROFILE_FIELDS = ("summary", "positioning", "industries", "audiences", "differentiators", "geography", "social_links")


def _now() -> datetime:
    return datetime.now(UTC)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found.")


async def _job(ctx: Context, kind: str, payload: dict | None = None, dedupe: str | None = None) -> Job:
    return await enqueue(ctx.session, kind, ctx.org_id, payload or {}, dedupe_key=dedupe, created_by=ctx.user.id)


async def _public_url(url: str) -> str:
    try:
        return await crawler.check_public_url(url)
    except crawler.UnsafeURLError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ------------------------------------------------------------------ serializers
def profile_out(p: CompanyProfile | None) -> dict | None:
    if p is None:
        return None
    return {
        "id": p.id, "summary": p.summary, "positioning": p.positioning, "industries": p.industries or [],
        "audiences": p.audiences or [], "differentiators": p.differentiators or [], "geography": p.geography,
        "social_links": p.social_links or {}, "confirmed": p.confirmed, "confirmed_at": p.confirmed_at,
        "edited_fields": (p.data or {}).get("edited_fields") or [], "extracted_at": (p.data or {}).get("extracted_at"),
        "updated_at": p.updated_at,
    }


def entity_out(e: CompanyEntity) -> dict:
    return {
        "id": e.id, "type": e.type, "name": e.name, "description": e.description, "url": e.url, "source": e.source,
        "confirmed": e.confirmed, "public_mention_ok": e.public_mention_ok, "edited": bool((e.data or {}).get("edited")),
        "created_at": e.created_at, "updated_at": e.updated_at,
    }


def source_out(src: KnowledgeSource) -> dict:
    meta = src.meta or {}
    crawl = meta.get("crawl") or {}
    return {
        "id": src.id, "kind": src.kind, "title": src.title, "url": src.url, "status": src.status,
        "status_message": src.status_message, "page_count": src.page_count, "last_crawled_at": src.last_crawled_at,
        "competitor_id": src.competitor_id, "max_pages": meta.get("max_pages"), "filename": meta.get("filename"),
        "size_bytes": meta.get("size_bytes"), "created_at": src.created_at,
        "crawl": {k: crawl.get(k) for k in ("pages", "sitemap_found", "robots_found", "https", "truncated",
                                             "blocked_by_robots", "finished_at")} | {
            "error_count": len(crawl.get("errors") or []), "broken_link_count": len(crawl.get("broken_links") or []),
        } if crawl else None,
    }


def competitor_out(c: Competitor) -> dict:
    d = c.data or {}
    return {
        "id": c.id, "name": c.name, "website": c.website, "domain": c.domain, "status": c.status,
        "overlap_score": c.overlap_score, "reason": c.reason, "last_crawled_at": c.last_crawled_at,
        "offerings": d.get("offerings") or [], "positioning": d.get("positioning") or "",
        "pricing_signals": d.get("pricing_signals") or [], "content_themes": d.get("content_themes") or [],
        "blog_topics": d.get("blog_topics") or [], "keywords": d.get("keywords") or [],
        "title_keywords": d.get("title_keywords") or [], "audiences": d.get("audiences") or [],
        "industries": d.get("industries") or [], "page_count": d.get("page_count"), "changes": (d.get("changes") or [])[:5],
        "origin": d.get("origin") or "manual", "created_at": c.created_at,
    }


def audit_out(a: PresenceAudit, full: bool = True) -> dict:
    out = {"id": a.id, "score": a.score, "created_at": a.created_at,
           "website": (a.data or {}).get("website"), "pages_analyzed": (a.data or {}).get("pages_analyzed")}
    sections = (a.data or {}).get("sections") or {}
    out["section_scores"] = {k: (v or {}).get("score") for k, v in sections.items()}
    if full:
        out["sections"] = sections
        out["recommendations"] = (a.data or {}).get("recommendations") or []
    return out


def opp_out(o: Opportunity) -> dict:
    d = o.data or {}
    return {
        "id": o.id, "title": o.title, "angle": o.angle, "pillar_id": o.pillar_id, "source": o.source,
        "score": round(o.score or 0, 3), "status": o.status, "used_count": o.used_count,
        "keywords": d.get("keywords") or [], "formats": d.get("formats") or [], "created_at": o.created_at,
    }


# ------------------------------------------------------------------ overview
@router.get("/overview")
async def overview(ctx: Context = Depends(require(VIEW))):
    s = ctx.session
    profile = await s.scalar(select(CompanyProfile).limit(1))
    website = (await s.scalars(select(KnowledgeSource).where(KnowledgeSource.kind == "website")
                               .order_by(KnowledgeSource.created_at).limit(1))).first()
    pages = await s.scalar(select(func.coalesce(func.sum(KnowledgeSource.page_count), 0))
                           .where(KnowledgeSource.kind.in_(("website", "upload")))) or 0
    uploads = await s.scalar(select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.kind == "upload")) or 0
    ent_rows = (await s.execute(select(CompanyEntity.type, CompanyEntity.confirmed, func.count())
                                .group_by(CompanyEntity.type, CompanyEntity.confirmed))).all()
    entities: dict[str, int] = {}
    confirmed_entities = 0
    for t, conf, n in ent_rows:
        entities[t] = entities.get(t, 0) + n
        confirmed_entities += n if conf else 0
    comp_rows = (await s.execute(select(Competitor.status, func.count()).group_by(Competitor.status))).all()
    opp_rows = (await s.execute(select(Opportunity.status, func.count()).group_by(Opportunity.status))).all()
    latest = await s.scalar(select(PresenceAudit).order_by(PresenceAudit.created_at.desc()).limit(1))
    ideas = (await s.scalars(select(Opportunity).where(Opportunity.status == "new")
                             .order_by(Opportunity.score.desc(), Opportunity.created_at.desc()).limit(5))).all()
    jobs = (await s.scalars(select(Job).where(Job.kind.like("intel.%"), Job.status.in_(("queued", "running")))
                            .order_by(Job.created_at.desc()).limit(10))).all()
    last_run = (await s.scalars(select(Job).where(Job.kind == "intel.run_all").order_by(Job.created_at.desc()).limit(1))).first()
    ai_ready = bool(await resolve_chain(s, "extract"))
    search_cfg = await s.scalar(select(func.count()).select_from(ProviderConfig)
                                .where(ProviderConfig.kind == "search", ProviderConfig.enabled.is_(True))) or 0
    return {
        "organization_website": ctx.org.website if ctx.org else None,
        "website_source": source_out(website) if website else None,
        "profile": profile_out(profile),
        "counts": {
            "pages": int(pages), "uploads": uploads, "entities": entities, "entities_total": sum(entities.values()),
            "entities_confirmed": confirmed_entities, "competitors": dict(comp_rows), "ideas": dict(opp_rows),
        },
        "latest_audit": audit_out(latest, full=False) | {
            "top_recommendations": ((latest.data or {}).get("recommendations") or [])[:3]} if latest else None,
        "latest_ideas": [opp_out(o) for o in ideas],
        "running_jobs": [job_out(j) for j in jobs],
        "last_run": job_out(last_run) if last_run else None,
        "ai_ready": ai_ready,
        "search_provider": "configured" if search_cfg else "duckduckgo",
    }


class RunAllIn(BaseModel):
    max_pages: int | None = Field(None, ge=1, le=500)


@router.post("/run-all", status_code=202)
async def run_all(body: RunAllIn | None = None, ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    has_site = await s.scalar(select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.kind == "website"))
    if not has_site:
        if not (ctx.org and ctx.org.website):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Add your website in Organization settings or on the Sources tab first.")
        await _public_url(ctx.org.website)
    payload = {"max_pages": body.max_pages} if body and body.max_pages else {}
    job = await _job(ctx, "intel.run_all", payload, dedupe=f"intel.run_all:{ctx.org_id}")
    audit.record(s, action="intel.run_all", organization_id=ctx.org_id, actor=ctx.user,
                 summary="Started a full company analysis", ip=ctx.ip)
    await s.commit()
    return {"job_id": job.id}


@router.get("/search")
async def search_knowledge(q: str, k: int = 6, ctx: Context = Depends(require(VIEW))):
    return {"results": await knowledge.retrieve(ctx.session, q, k=max(1, min(k, 20)))}


# ------------------------------------------------------------------ profile
class ProfilePatch(BaseModel):
    summary: str | None = Field(None, max_length=5000)
    positioning: str | None = Field(None, max_length=2000)
    industries: list[str] | None = Field(None, max_length=50)
    audiences: list[str] | None = Field(None, max_length=50)
    differentiators: list[str] | None = Field(None, max_length=50)
    geography: str | None = Field(None, max_length=500)
    social_links: dict[str, str] | None = None


class ConfirmIn(BaseModel):
    confirm_entities: bool = False


def _clean_list(values: list[str]) -> list[str]:
    out: dict[str, str] = {}
    for v in values:
        v = re.sub(r"\s+", " ", v or "").strip()[:300]
        if v and normalize_name(v) not in out:
            out[normalize_name(v)] = v
    return list(out.values())


@router.get("/profile")
async def get_profile(ctx: Context = Depends(require(VIEW))):
    return {"profile": profile_out(await ctx.session.scalar(select(CompanyProfile).limit(1)))}


@router.patch("/profile")
async def patch_profile(body: ProfilePatch, ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    p = await s.scalar(select(CompanyProfile).limit(1))
    if p is None:
        p = CompanyProfile(organization_id=ctx.org_id, industries=[], audiences=[], differentiators=[], social_links={}, data={})
        s.add(p)
    data = body.model_dump(exclude_unset=True)
    edited = set((p.data or {}).get("edited_fields") or [])
    for k, v in data.items():
        if k in ("industries", "audiences", "differentiators"):
            v = _clean_list(v or [])
        elif k == "social_links":
            v = {str(pk)[:30]: str(pv)[:500] for pk, pv in (v or {}).items() if pv and str(pv).startswith(("http://", "https://"))}
        elif isinstance(v, str):
            v = v.strip() or None
        setattr(p, k, v)
        edited.add(k)
    p.data = {**(p.data or {}), "edited_fields": sorted(edited)}
    audit.record(s, action="intel.profile.updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="company_profile", entity_id=p.id, summary="Edited the company profile", ip=ctx.ip)
    await s.commit()
    return {"profile": profile_out(p)}


@router.post("/profile/confirm")
async def confirm_profile(body: ConfirmIn | None = None, ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    p = await s.scalar(select(CompanyProfile).limit(1))
    if p is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There is no profile yet. Analyze your website first.")
    p.confirmed, p.confirmed_at = True, _now()
    n = 0
    if body and body.confirm_entities:
        for e in (await s.scalars(select(CompanyEntity).where(CompanyEntity.confirmed.is_(False)))).all():
            e.confirmed = True
            n += 1
    audit.record(s, action="intel.profile.confirmed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="company_profile", entity_id=p.id,
                 summary="Confirmed the company profile" + (f" and {n} facts" if n else ""), ip=ctx.ip)
    await s.commit()
    return {"profile": profile_out(p), "entities_confirmed": n}


# ------------------------------------------------------------------ entities
class EntityIn(BaseModel):
    type: EntityType
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(None, max_length=2000)
    url: str | None = Field(None, max_length=1000)
    confirmed: bool = True
    public_mention_ok: bool = False


class EntityPatch(BaseModel):
    type: EntityType | None = None
    name: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=2000)
    url: str | None = Field(None, max_length=1000)
    confirmed: bool | None = None
    public_mention_ok: bool | None = None


def _clean_url(url: str | None) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Links must start with http:// or https://.")
    return url


async def _get_entity(ctx: Context, eid: uuid.UUID) -> CompanyEntity:
    e = await ctx.session.scalar(select(CompanyEntity).where(CompanyEntity.id == eid))
    if e is None:
        raise _not_found("Item")
    return e


async def _entity_conflict(ctx: Context, etype: str, name: str, exclude: uuid.UUID | None = None) -> None:
    key = normalize_name(name)
    for e in (await ctx.session.scalars(select(CompanyEntity).where(CompanyEntity.type == etype))).all():
        if e.id != exclude and normalize_name(e.name) == key:
            raise HTTPException(status.HTTP_409_CONFLICT, f"“{e.name}” is already in the list.")


@router.get("/entities")
async def list_entities(type: EntityType | None = None, ctx: Context = Depends(require(VIEW))):
    q = select(CompanyEntity).order_by(CompanyEntity.type, CompanyEntity.name)
    if type:
        q = q.where(CompanyEntity.type == type)
    return [entity_out(e) for e in (await ctx.session.scalars(q)).all()]


@router.post("/entities", status_code=201)
async def create_entity(body: EntityIn, ctx: Context = Depends(require(MANAGE))):
    name = body.name.strip()
    await _entity_conflict(ctx, body.type, name)
    e = CompanyEntity(
        organization_id=ctx.org_id, type=body.type, name=name, description=(body.description or "").strip() or None,
        url=_clean_url(body.url), source="manual", confirmed=body.confirmed,
        public_mention_ok=body.public_mention_ok if body.type == "client" else False, data={"edited": True},
    )
    ctx.session.add(e)
    audit.record(ctx.session, action="intel.entity.added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="company_entity", entity_id=e.id, summary=f"Added {body.type.replace('_', ' ')} {name}", ip=ctx.ip)
    await ctx.session.commit()
    return entity_out(e)


@router.patch("/entities/{eid}")
async def patch_entity(eid: uuid.UUID, body: EntityPatch, ctx: Context = Depends(require(MANAGE))):
    e = await _get_entity(ctx, eid)
    data = body.model_dump(exclude_unset=True)
    if "name" in data or "type" in data:
        await _entity_conflict(ctx, data.get("type") or e.type, (data.get("name") or e.name).strip(), exclude=e.id)
    for k, v in data.items():
        if k == "url":
            v = _clean_url(v)
        elif k in ("name", "description") and isinstance(v, str):
            v = v.strip() or (None if k == "description" else e.name)
        elif v is None and k in ("type", "confirmed", "public_mention_ok"):
            continue
        setattr(e, k, v)
    if e.type != "client":
        e.public_mention_ok = False
    e.data = {**(e.data or {}), "edited": True, "edited_at": _now().isoformat()}
    audit.record(ctx.session, action="intel.entity.updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="company_entity", entity_id=e.id, summary=f"Updated {e.name}", ip=ctx.ip)
    await ctx.session.commit()
    return entity_out(e)


@router.delete("/entities/{eid}", status_code=204)
async def delete_entity(eid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    e = await _get_entity(ctx, eid)
    audit.record(ctx.session, action="intel.entity.removed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="company_entity", entity_id=e.id, summary=f"Removed {e.name}", ip=ctx.ip)
    await ctx.session.delete(e)
    await ctx.session.commit()


# ------------------------------------------------------------------ sources
class WebsiteIn(BaseModel):
    url: str = Field(min_length=4, max_length=500)
    max_pages: int = Field(crawler.DEFAULT_MAX_PAGES, ge=1, le=500)


async def _get_source(ctx: Context, sid: uuid.UUID) -> KnowledgeSource:
    src = await ctx.session.scalar(select(KnowledgeSource).where(KnowledgeSource.id == sid))
    if src is None:
        raise _not_found("Source")
    return src


@router.get("/sources")
async def list_sources(kind: str | None = None, ctx: Context = Depends(require(VIEW))):
    q = select(KnowledgeSource).order_by(KnowledgeSource.kind.desc(), KnowledgeSource.created_at.desc())
    q = q.where(KnowledgeSource.kind == kind) if kind else q.where(KnowledgeSource.kind != "competitor")
    return [source_out(src) for src in (await ctx.session.scalars(q)).all()]


@router.post("/sources/website", status_code=202)
async def crawl_website(body: WebsiteIn, ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    url = await _public_url(body.url)
    dom = crawler.domain_of(url)
    src = None
    for existing in (await s.scalars(select(KnowledgeSource).where(KnowledgeSource.kind == "website"))).all():
        if existing.url and crawler.domain_of(existing.url) == dom:
            src = existing
            break
    if src is None:
        src = KnowledgeSource(organization_id=ctx.org_id, kind="website", title=urlsplit_host(url), url=url, status="pending")
        s.add(src)
        await s.flush()
    if src.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "This website is being crawled right now.")
    src.url = url
    src.status, src.status_message = "pending", "Waiting to start…"
    src.meta = {**(src.meta or {}), "max_pages": body.max_pages}
    job = await _job(ctx, "intel.crawl", {"source_id": str(src.id), "chain": True}, dedupe=f"intel.crawl:{src.id}")
    audit.record(s, action="intel.crawl", organization_id=ctx.org_id, actor=ctx.user, entity_type="knowledge_source",
                 entity_id=src.id, summary=f"Started crawling {url}", ip=ctx.ip)
    await s.commit()
    return {"job_id": job.id, "source": source_out(src)}


def urlsplit_host(url: str) -> str:
    return (urlsplit(url).hostname or url).removeprefix("www.")


@router.post("/sources/{sid}/recrawl", status_code=202)
async def recrawl(sid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    src = await _get_source(ctx, sid)
    if src.kind not in ("website", "competitor") or not src.url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only websites can be re-crawled. Upload a new version of the file instead.")
    if src.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "This website is being crawled right now.")
    await _public_url(src.url)
    if src.kind == "competitor" and src.competitor_id:
        job = await _job(ctx, "intel.competitor_crawl", {"competitor_id": str(src.competitor_id)},
                         dedupe=f"intel.competitor_crawl:{src.competitor_id}")
    else:
        job = await _job(ctx, "intel.crawl", {"source_id": str(src.id), "chain": True}, dedupe=f"intel.crawl:{src.id}")
    src.status, src.status_message = "pending", "Waiting to start…"
    await ctx.session.commit()
    return {"job_id": job.id}


def _multipart_file(content_type: str, body: bytes) -> tuple[str, bytes]:
    """First file part of a multipart/form-data body: (filename, bytes)."""
    m = re.search(r'boundary="?([^";]+)"?', content_type, re.I)
    if not m:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The upload is malformed. Choose the file again.")
    delimiter = b"--" + m.group(1).strip().encode("latin-1")
    for part in body.split(delimiter)[1:]:
        if part.startswith(b"--"):
            break
        part = part[2:] if part.startswith(b"\r\n") else part
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = head.decode("utf-8", errors="replace")
        fm = re.search(r"filename\*=(?:UTF-8'')?([^;\r\n]+)", headers, re.I) or re.search(r'filename="([^"]*)"', headers, re.I) \
            or re.search(r"filename=([^;\r\n]+)", headers, re.I)
        if fm and fm.group(1).strip():
            return unquote(fm.group(1).strip().strip('"')), data[:-2] if data.endswith(b"\r\n") else data
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a file to upload.")


@router.post("/sources/upload", status_code=201)
async def upload_source(request: Request, ctx: Context = Depends(require(MANAGE))):
    """Accepts multipart/form-data (field "file") or a raw body with ?filename=."""
    limit = docparse.MAX_UPLOAD_BYTES + 64 * 1024
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file is larger than 25 MB. Split it and upload the parts.")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file is larger than 25 MB. Split it and upload the parts.")
    ct = request.headers.get("content-type", "")
    if ct.lower().startswith("multipart/form-data"):
        filename, data = _multipart_file(ct, body)
    else:
        filename, data = request.query_params.get("filename") or "", body
    filename = PurePath(filename.replace("\\", "/")).name[:200] or "document"
    try:
        text, doc_title = docparse.extract_text(data, filename)
    except docparse.DocParseError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    s = ctx.session
    ext = PurePath(filename).suffix.lstrip(".").lower()
    if ext not in {"pdf", "docx", "pptx", "txt", "md", "markdown", "html", "htm", "csv"}:
        ext = "bin"  # never let a user-supplied name shape the stored file name (Windows ADS, reserved chars)
    rel, sha = storage.save_bytes(ctx.org_id, "knowledge", data, ext)
    src = KnowledgeSource(
        organization_id=ctx.org_id, kind="upload", title=(doc_title or PurePath(filename).stem)[:300], status="ready",
        page_count=1, last_crawled_at=_now(),
        meta={"filename": filename, "path": rel, "sha256": sha, "size_bytes": len(data), "mime": storage.guess_mime(filename)},
    )
    s.add(src)
    await s.flush()
    await knowledge.add_document(s, src, url=None, title=src.title, text=text,
                                 meta={"filename": filename, "chars": len(text)})
    chunks = len(knowledge.chunk_text(text))
    src.status_message = f"{len(text):,} characters, {chunks} passages"
    audit.record(s, action="intel.upload", organization_id=ctx.org_id, actor=ctx.user, entity_type="knowledge_source",
                 entity_id=src.id, summary=f"Uploaded {filename}", ip=ctx.ip)
    await s.commit()
    out = source_out(src)  # before the optional embedding step: a rollback expires loaded rows
    try:  # embeddings are optional; full-text search works without them
        if await knowledge.embed_pending(s, limit=500):
            await s.commit()
    except Exception:
        await s.rollback()
    return out


@router.delete("/sources/{sid}", status_code=204)
async def delete_source(sid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    src = await _get_source(ctx, sid)
    path = (src.meta or {}).get("path")
    audit.record(ctx.session, action="intel.source.removed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="knowledge_source", entity_id=src.id, summary=f"Removed {src.title}", ip=ctx.ip)
    await ctx.session.delete(src)
    await ctx.session.commit()
    if path:
        try:
            storage.abs_path(path).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


# ------------------------------------------------------------------ competitors
class CompetitorIn(BaseModel):
    url: str = Field(min_length=4, max_length=500)
    name: str | None = Field(None, max_length=300)


class CompetitorPatch(BaseModel):
    status: Literal["suggested", "confirmed", "rejected"] | None = None
    name: str | None = Field(None, min_length=1, max_length=300)


async def _get_competitor(ctx: Context, cid: uuid.UUID) -> Competitor:
    c = await ctx.session.scalar(select(Competitor).where(Competitor.id == cid))
    if c is None:
        raise _not_found("Competitor")
    return c


@router.get("/competitors")
async def list_competitors(status_: Literal["suggested", "confirmed", "rejected"] | None = Query(None, alias="status"),
                           ctx: Context = Depends(require(VIEW))):
    q = select(Competitor).order_by(Competitor.overlap_score.desc(), Competitor.name)
    if status_:
        q = q.where(Competitor.status == status_)
    return [competitor_out(c) for c in (await ctx.session.scalars(q)).all()]


@router.post("/competitors", status_code=201)
async def add_competitor(body: CompetitorIn, ctx: Context = Depends(require(MANAGE))):
    s = ctx.session
    url = await _public_url(body.url)
    dom = crawler.domain_of(url)
    own = {crawler.domain_of(src.url) for src in (await s.scalars(select(KnowledgeSource).where(KnowledgeSource.kind == "website"))).all() if src.url}
    if ctx.org and ctx.org.website:
        own.add(crawler.domain_of(crawler.normalize_url(ctx.org.website) or ""))
    if dom in own:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's your own website.")
    existing = await s.scalar(select(Competitor).where(Competitor.domain == dom))
    if existing is not None:
        hint = " It was rejected earlier; confirm it from the rejected list." if existing.status == "rejected" else ""
        raise HTTPException(status.HTTP_409_CONFLICT, f"{existing.name} is already in the list.{hint}")
    origin = crawler.normalize_url(f"{url.split('://')[0]}://{url.split('://')[1].split('/')[0]}/") or url
    c = Competitor(organization_id=ctx.org_id, name=(body.name or "").strip() or urlsplit_host(url), website=origin,
                   domain=dom, status="confirmed", overlap_score=0, data={"origin": "manual"})
    s.add(c)
    await s.flush()
    job = await _job(ctx, "intel.competitor_crawl", {"competitor_id": str(c.id)}, dedupe=f"intel.competitor_crawl:{c.id}")
    audit.record(s, action="intel.competitor.added", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="competitor", entity_id=c.id, summary=f"Added competitor {c.name}", ip=ctx.ip)
    await s.commit()
    return {"competitor": competitor_out(c), "job_id": job.id}


@router.patch("/competitors/{cid}")
async def patch_competitor(cid: uuid.UUID, body: CompetitorPatch, ctx: Context = Depends(require(MANAGE))):
    c = await _get_competitor(ctx, cid)
    job = None
    if body.name:
        c.name = body.name.strip()
    if body.status and body.status != c.status:
        was = c.status
        c.status = body.status
        if body.status == "confirmed" and was != "confirmed":
            job = await _job(ctx, "intel.competitor_crawl", {"competitor_id": str(c.id)}, dedupe=f"intel.competitor_crawl:{c.id}")
        audit.record(ctx.session, action=f"intel.competitor.{body.status}", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="competitor", entity_id=c.id, summary=f"Marked {c.name} as {body.status}", ip=ctx.ip)
    await ctx.session.commit()
    return {"competitor": competitor_out(c), "job_id": job.id if job else None}


@router.delete("/competitors/{cid}", status_code=204)
async def delete_competitor(cid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    c = await _get_competitor(ctx, cid)
    for src in (await ctx.session.scalars(select(KnowledgeSource).where(KnowledgeSource.competitor_id == c.id))).all():
        await ctx.session.delete(src)
    audit.record(ctx.session, action="intel.competitor.removed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="competitor", entity_id=c.id, summary=f"Removed competitor {c.name}", ip=ctx.ip)
    await ctx.session.delete(c)
    await ctx.session.commit()


@router.post("/competitors/discover", status_code=202)
async def discover_competitors(ctx: Context = Depends(require(MANAGE))):
    if await ctx.session.scalar(select(CompanyProfile).limit(1)) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Analyze your website first so competitors can be compared with your offerings.")
    job = await _job(ctx, "intel.competitors", {}, dedupe=f"intel.competitors:{ctx.org_id}")
    await ctx.session.commit()
    return {"job_id": job.id}


@router.post("/competitors/{cid}/crawl", status_code=202)
async def crawl_competitor(cid: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    c = await _get_competitor(ctx, cid)
    if c.status == "rejected":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirm this competitor before crawling it.")
    await _public_url(c.website)
    job = await _job(ctx, "intel.competitor_crawl", {"competitor_id": str(c.id)}, dedupe=f"intel.competitor_crawl:{c.id}")
    await ctx.session.commit()
    return {"job_id": job.id}


# ------------------------------------------------------------------ audits
@router.get("/audits")
async def list_audits(limit: int = 20, ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(PresenceAudit).order_by(PresenceAudit.created_at.desc())
                                      .limit(max(1, min(limit, 100))))).all()
    return [audit_out(a, full=False) for a in rows]


@router.get("/audits/latest")
async def latest_audit(ctx: Context = Depends(require(VIEW))):
    a = await ctx.session.scalar(select(PresenceAudit).order_by(PresenceAudit.created_at.desc()).limit(1))
    return {"audit": audit_out(a) if a else None}


@router.post("/audits/run", status_code=202)
async def run_audit(ctx: Context = Depends(require(MANAGE))):
    ready = await ctx.session.scalar(select(func.count()).select_from(KnowledgeSource)
                                     .where(KnowledgeSource.kind == "website", KnowledgeSource.status == "ready"))
    if not ready:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Crawl your website first on the Sources tab.")
    job = await _job(ctx, "intel.audit", {}, dedupe=f"intel.audit:{ctx.org_id}")
    await ctx.session.commit()
    return {"job_id": job.id}


# ------------------------------------------------------------------ opportunities (topic ideas)
class OpportunityIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    angle: str | None = Field(None, max_length=1000)
    pillar_id: uuid.UUID | None = None
    score: float = Field(0.6, ge=0, le=1)


class OpportunityPatch(BaseModel):
    status: Literal["new", "used", "dismissed"] | None = None
    title: str | None = Field(None, min_length=3, max_length=300)
    angle: str | None = Field(None, max_length=1000)
    pillar_id: uuid.UUID | None = None
    score: float | None = Field(None, ge=0, le=1)


async def _check_pillar(ctx: Context, pillar_id: uuid.UUID | None) -> None:
    if pillar_id and not await ctx.session.scalar(select(ContentPillar.id).where(ContentPillar.id == pillar_id)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That content pillar doesn't exist.")


@router.get("/pillars")
async def list_pillars(ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(ContentPillar).order_by(ContentPillar.name))).all()
    return [{"id": p.id, "name": p.name, "color": p.color, "active": p.active} for p in rows]


@router.get("/opportunities")
async def list_opportunities(
    status_: Literal["new", "used", "dismissed"] | None = Query(None, alias="status"), source: str | None = None,
    pillar_id: uuid.UUID | None = None, q: str | None = None, limit: int = 300, ctx: Context = Depends(require(VIEW)),
):
    query = select(Opportunity).order_by(Opportunity.score.desc(), Opportunity.created_at.desc())
    if status_:
        query = query.where(Opportunity.status == status_)
    if source:
        query = query.where(Opportunity.source == source)
    if pillar_id:
        query = query.where(Opportunity.pillar_id == pillar_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(Opportunity.title.ilike(like), Opportunity.angle.ilike(like)))
    rows = (await ctx.session.scalars(query.limit(max(1, min(limit, 500))))).all()
    return [opp_out(o) for o in rows]


@router.post("/opportunities", status_code=201)
async def create_opportunity(body: OpportunityIn, ctx: Context = Depends(require(MANAGE))):
    await _check_pillar(ctx, body.pillar_id)
    key = normalize_name(body.title)
    for t in (await ctx.session.scalars(select(Opportunity.title))).all():
        if normalize_name(t) == key:
            raise HTTPException(status.HTTP_409_CONFLICT, "That idea is already in the list.")
    o = Opportunity(organization_id=ctx.org_id, title=body.title.strip(), angle=(body.angle or "").strip() or None,
                    pillar_id=body.pillar_id, source="manual", score=body.score, status="new",
                    data={"created_by": str(ctx.user.id)})
    ctx.session.add(o)
    await ctx.session.commit()
    return opp_out(o)


@router.patch("/opportunities/{oid}")
async def patch_opportunity(oid: uuid.UUID, body: OpportunityPatch, ctx: Context = Depends(require(MANAGE))):
    o = await ctx.session.scalar(select(Opportunity).where(Opportunity.id == oid))
    if o is None:
        raise _not_found("Idea")
    data = body.model_dump(exclude_unset=True)
    if "pillar_id" in data:
        await _check_pillar(ctx, data["pillar_id"])
    for k, v in data.items():
        if k in ("status", "title", "score") and v is None:
            continue
        setattr(o, k, v.strip() if isinstance(v, str) else v)
    await ctx.session.commit()
    return opp_out(o)


@router.post("/opportunities/generate", status_code=202)
async def generate_opportunities(ctx: Context = Depends(require(MANAGE))):
    if await ctx.session.scalar(select(CompanyProfile).limit(1)) is None and not await ctx.session.scalar(
            select(func.count()).select_from(CompanyEntity)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Analyze your website first so ideas can be based on your company.")
    job = await _job(ctx, "intel.ideas", {}, dedupe=f"intel.ideas:{ctx.org_id}")
    await ctx.session.commit()
    return {"job_id": job.id}

