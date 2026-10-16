"""Knowledge base and company facts used to ground generated content.

Contract used by other modules (see docs/ARCHITECTURE.md):
  retrieve(session, query, k=6)        -> [{text, url, title}]  RAG over the org's website pages and uploads
  company_context(session, max_chars)  -> str                    compact confirmed company facts for prompts
  next_opportunity(session, pillar_id) -> Opportunity | None     best unused topic idea; marks it used

Ingestion helpers (used by the intelligence module and API): chunk_text, add_document, embed_pending.
Competitor pages are stored in the same tables (source kind "competitor") but never retrieved for grounding.
"""

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import case, delete, func, literal, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CompanyEntity,
    CompanyProfile,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSource,
    Opportunity,
    Organization,
)
from app.services.llm.router import embed_texts, resolve_chain

log = logging.getLogger(__name__)

CHUNK_SIZE = 900
CHUNK_OVERLAP = 150
GROUNDING_KINDS = ("website", "upload", "note")
_ENGLISH = literal_column("'english'::regconfig")

ENTITY_TYPES = ("product", "studio", "service", "use_case", "client", "industry", "case_study", "team")
ENTITY_LABELS = {
    "product": "Products", "studio": "Studios", "service": "Services", "use_case": "Use cases",
    "industry": "Industries served", "case_study": "Case studies", "client": "Clients (may be named publicly)",
    "team": "Team",
}


# ------------------------------------------------------------------ text utilities
def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().encode("utf-8")).hexdigest()


def normalize_name(name: str) -> str:
    """Key for de-duplicating names and titles: lower-case words without punctuation or legal suffixes."""
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    s = re.sub(r"\b(the|pvt|private|ltd|limited|llp|llc|inc|corp|co)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _split_long(paragraph: str, size: int) -> list[str]:
    """Split one long paragraph on sentence boundaries, then hard-split what is still too long."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > size:
            cut = s.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > size:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return [p for p in out if p]


def _tail(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    t = text[-n:]
    sp = t.find(" ")
    return t[sp + 1:] if 0 <= sp < n // 2 else t


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """~`size`-character chunks split on paragraphs, each starting with the last ~`overlap` characters
    of the previous chunk so facts that straddle a boundary stay retrievable."""
    text = (text or "").replace("\r", "").strip()
    if not text:
        return []
    paragraphs: list[str] = []
    for p in re.split(r"\n\s*\n", text):
        p = re.sub(r"[ \t]+", " ", p).strip()
        if not p:
            continue
        paragraphs.extend(_split_long(p, size - overlap) if len(p) > size - overlap else [p])
    chunks: list[str] = []
    cur = ""
    for p in paragraphs:
        if cur and len(cur) + 2 + len(p) > size:
            chunks.append(cur)
            cur = f"{_tail(cur, overlap)}\n\n{p}" if overlap else p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------------ ingestion
async def add_document(
    session: AsyncSession, source: KnowledgeSource, *, url: str | None, title: str | None, text: str,
    meta: dict | None = None,
) -> tuple[KnowledgeDocument, str]:
    """Insert or update one document and its chunks. Returns (document, 'new' | 'changed' | 'same').
    Documents are de-duplicated by (source, url) — or by content hash when there is no URL."""
    h = content_hash(text)
    q = select(KnowledgeDocument).where(KnowledgeDocument.source_id == source.id)
    q = q.where(KnowledgeDocument.url == url) if url else q.where(KnowledgeDocument.content_hash == h)
    doc = (await session.scalars(q.limit(1))).first()
    if doc is not None and doc.content_hash == h:
        doc.fetched_at = datetime.now(UTC)
        if meta is not None:
            doc.meta = meta
        if title:
            doc.title = title[:2000]
        return doc, "same"
    state = "changed" if doc is not None else "new"
    if doc is None:
        doc = KnowledgeDocument(organization_id=source.organization_id, source_id=source.id, url=url)
        session.add(doc)
    else:
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
    doc.title = (title or url or source.title or "")[:2000]
    doc.text = text
    doc.content_hash = h
    doc.meta = meta or {}
    doc.fetched_at = datetime.now(UTC)
    await session.flush()
    for i, piece in enumerate(chunk_text(text)):
        session.add(KnowledgeChunk(organization_id=source.organization_id, document_id=doc.id, ordinal=i, text=piece))
    await session.flush()
    return doc, state


async def _embed_model_names(session: AsyncSession) -> list[str]:
    try:
        chain = await resolve_chain(session, "embed")
    except Exception:
        return []
    return [f"{cfg.provider}:{model}" for cfg, model in chain]


async def embed_pending(session: AsyncSession, limit: int = 2000) -> int:
    """Embed chunks that have no embedding for the current embedding model. Returns how many were embedded.
    Does nothing when no embedding model is configured."""
    names = await _embed_model_names(session)
    if not names:
        return 0
    rows = (await session.scalars(
        select(KnowledgeChunk)
        .where(or_(KnowledgeChunk.embedding.is_(None), KnowledgeChunk.embedding_model.is_distinct_from(names[0])))
        .order_by(KnowledgeChunk.document_id, KnowledgeChunk.ordinal).limit(limit)
    )).all()
    done = 0
    for i in range(0, len(rows), 64):
        batch = rows[i:i + 64]
        vecs, model = await embed_texts(session, [c.text for c in batch])
        if not vecs or not model or len(vecs) != len(batch):
            break
        for c, v in zip(batch, vecs, strict=True):
            c.embedding, c.embedding_model = v, model
        done += len(batch)
        await session.flush()
    return done


# ------------------------------------------------------------------ retrieval
def _row(text: str, url: str | None, title: str | None) -> dict:
    return {"text": text, "url": url, "title": title}


def _grounding_filter():
    return KnowledgeSource.kind.in_(GROUNDING_KINDS)


async def _vector_search(session: AsyncSession, query: str, k: int) -> list[dict] | None:
    names = await _embed_model_names(session)
    if not names:
        return None
    available = set((await session.scalars(
        select(KnowledgeChunk.embedding_model).where(KnowledgeChunk.embedding_model.in_(names)).distinct()
    )).all())
    if not available:
        return None
    vecs, model = await embed_texts(session, [query])
    if not vecs or model not in available:
        return None
    try:
        async with session.begin_nested():
            rows = (await session.execute(
                select(KnowledgeChunk.text, KnowledgeDocument.url, KnowledgeDocument.title)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
                .where(KnowledgeChunk.embedding_model == model, _grounding_filter())
                .order_by(KnowledgeChunk.embedding.cosine_distance(vecs[0]))
                .limit(k)
            )).all()
    except Exception as e:
        log.warning("vector search failed, using full-text search: %s", e)
        return None
    return [_row(t, u, ti) for t, u, ti in rows]


async def _fulltext_search(session: AsyncSession, query: str, k: int) -> list[dict]:
    tsv = func.to_tsvector(_ENGLISH, KnowledgeChunk.text)
    tsq = func.plainto_tsquery(_ENGLISH, query)
    rows = (await session.execute(
        select(KnowledgeChunk.text, KnowledgeDocument.url, KnowledgeDocument.title)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(tsv.op("@@")(tsq), _grounding_filter())
        .order_by(func.ts_rank(tsv, tsq).desc(), KnowledgeChunk.ordinal)
        .limit(k)
    )).all()
    return [_row(t, u, ti) for t, u, ti in rows]


async def _ilike_search(session: AsyncSession, query: str, k: int) -> list[dict]:
    words = list(dict.fromkeys(re.findall(r"\w{3,}", query.lower())))[:8]
    if not words:
        return []
    conds = [KnowledgeChunk.text.ilike(f"%{w}%") for w in words]
    hits = sum((case((c, 1), else_=0) for c in conds), start=literal(0))  # number of words matched
    rows = (await session.execute(
        select(KnowledgeChunk.text, KnowledgeDocument.url, KnowledgeDocument.title)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(or_(*conds), _grounding_filter())
        .order_by(hits.desc(), KnowledgeChunk.ordinal)
        .limit(k)
    )).all()
    return [_row(t, u, ti) for t, u, ti in rows]


async def retrieve(session: AsyncSession, query: str, k: int = 6) -> list[dict]:
    """Most relevant passages from the org's website pages and uploaded documents.
    Vector search when chunks are embedded with the current embedding model; otherwise PostgreSQL
    full-text search; otherwise a simple ILIKE match."""
    query = (query or "").strip()
    if not query:
        return []
    k = max(1, min(k, 50))
    try:
        rows = await _vector_search(session, query, k)
        if rows:
            return rows
    except Exception as e:
        log.warning("vector retrieval failed: %s", e)
    rows = await _fulltext_search(session, query, k)
    if rows:
        return rows
    return await _ilike_search(session, query, k)


# ------------------------------------------------------------------ company facts for prompts
def _short(s: str | None, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


async def company_context(session: AsyncSession, max_chars: int = 3500) -> str:
    """Compact company facts for LLM prompts: the profile plus confirmed entities (all entities when none
    are confirmed yet). Clients are listed only when they may be named publicly."""
    org_id = session.info.get("org_id")
    org = await session.get(Organization, org_id) if org_id else None
    profile = await session.scalar(select(CompanyProfile).limit(1))
    entities = (await session.scalars(
        select(CompanyEntity).order_by(CompanyEntity.type, CompanyEntity.confirmed.desc(), CompanyEntity.name)
    )).all()
    if any(e.confirmed for e in entities):
        entities = [e for e in entities if e.confirmed]
    entities = [e for e in entities if e.type != "client" or e.public_mention_ok]

    lines: list[str] = []
    if org is not None:
        lines.append(f"Company: {org.name}" + (f" ({org.website})" if org.website else ""))
    if profile is not None:
        if profile.summary:
            lines.append(f"Summary: {_short(profile.summary, 700)}")
        if profile.positioning:
            lines.append(f"Positioning: {_short(profile.positioning, 400)}")
        if profile.industries:
            lines.append("Industries: " + ", ".join(map(str, profile.industries[:15])))
        if profile.audiences:
            lines.append("Audiences: " + ", ".join(map(str, profile.audiences[:12])))
        if profile.geography:
            lines.append(f"Geography: {_short(profile.geography, 200)}")
        if profile.differentiators:
            lines.append("Differentiators:")
            lines.extend(f"- {_short(str(d), 200)}" for d in profile.differentiators[:8])

    by_type: dict[str, list[CompanyEntity]] = {}
    for e in entities:
        by_type.setdefault(e.type, []).append(e)
    out = "\n".join(lines)
    for t in ("product", "studio", "service", "use_case", "industry", "case_study", "client", "team"):
        items = by_type.get(t)
        if not items:
            continue
        section = [f"{ENTITY_LABELS[t]}:"]
        for e in items[:25]:
            if t == "client":
                section.append(f"- {e.name}")
            else:
                desc = _short(e.description, 160)
                section.append(f"- {e.name}" + (f": {desc}" if desc else ""))
        block = "\n".join(section)
        if len(out) + len(block) + 1 > max_chars:
            # Fit as many items as possible, then stop.
            room = max_chars - len(out) - 1
            partial = []
            for line in section:
                if sum(len(x) + 1 for x in partial) + len(line) + 1 > room:
                    break
                partial.append(line)
            if len(partial) > 1:
                out += "\n" + "\n".join(partial)
            break
        out += ("\n" if out else "") + block
    return out[:max_chars]


# ------------------------------------------------------------------ opportunities
async def next_opportunity(session: AsyncSession, pillar_id: uuid.UUID | None) -> Opportunity | None:
    """Highest-scoring new idea for the pillar, else the best idea without a pillar. Marks it used."""
    base = select(Opportunity).where(Opportunity.status == "new")
    order = (Opportunity.score.desc(), Opportunity.created_at)
    opp = None
    if pillar_id is not None:
        opp = (await session.scalars(
            base.where(Opportunity.pillar_id == pillar_id).order_by(*order).limit(1).with_for_update(skip_locked=True)
        )).first()
    if opp is None:
        opp = (await session.scalars(
            base.where(Opportunity.pillar_id.is_(None)).order_by(*order).limit(1).with_for_update(skip_locked=True)
        )).first()
    if opp is None:
        return None
    opp.status = "used"
    opp.used_count = (opp.used_count or 0) + 1
    await session.flush()
    return opp


__all__ = [
    "ENTITY_LABELS",
    "ENTITY_TYPES",
    "add_document",
    "chunk_text",
    "company_context",
    "content_hash",
    "embed_pending",
    "next_opportunity",
    "normalize_name",
    "retrieve",
]
