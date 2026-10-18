"""Web and social listening: topics, mentions feed, insights, settings.

All queries go through ctx.session, which row-level security scopes to the caller's organization.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update

from app.core.deps import Context, require
from app.models import Competitor
from app.models.listening import QUERY_KINDS, ListeningMention, ListeningQuery
from app.services import audit
from app.services.jobs import enqueue
from app.services.listening import assist, engine, insights
from app.services.listening import sources as src
from app.services.listening.text import clean_terms
from app.services.llm.router import AllProvidersFailed, NoProviderError

router = APIRouter(prefix="/listening", tags=["listening"])

VIEW, MANAGE = "listening.view", "listening.manage"
MAX_QUERIES = engine.MAX_QUERIES


def _src_label(s: str) -> str:
    return src.SOURCES.get(s, {}).get("label", s)


# ------------------------------------------------------------------ serializers
def query_out(q: ListeningQuery, counts: dict | None = None) -> dict:
    st = q.state or {}
    return {
        "id": q.id, "name": q.name, "kind": q.kind, "terms": q.terms or [], "context_terms": q.context_terms or [],
        "exclude": q.exclude or [], "languages": q.languages or [], "countries": q.countries or [],
        "sources": q.sources or {}, "competitor_id": q.competitor_id, "enabled": q.enabled,
        "last_run_at": st.get("last_run_at"), "found_last_run": st.get("found_last_run"),
        "source_status": {k: {"ok": v.get("ok"), "message": v.get("message"), "found": v.get("found"),
                              "last_run_at": v.get("last_run_at")} for k, v in (st.get("sources") or {}).items()},
        "mentions_30d": (counts or {}).get(str(q.id), 0), "created_at": q.created_at,
    }


def mention_out(m: ListeningMention, queries: dict[str, ListeningQuery], comps: dict[str, str]) -> dict:
    q = queries.get(str(m.query_id)) if m.query_id else None
    return {
        "id": m.id, "source": m.source, "source_label": _src_label(m.source), "url": m.url, "domain": m.domain,
        "title": m.title, "snippet": m.snippet,
        "author": {"name": m.author_name, "handle": m.author_handle, "url": m.author_url, "followers": m.author_followers},
        "published_at": m.published_at, "found_at": m.created_at, "language": m.language,
        "score": m.score, "comments": m.comments, "reach": m.reach, "engagement": insights.engagement(m),
        "query": {"id": q.id, "name": q.name, "kind": q.kind} if q else None,
        "queries": [{"id": queries[i].id, "name": queries[i].name, "kind": queries[i].kind}
                    for i in (m.query_ids or []) if i in queries],
        "matched_terms": m.matched_terms or [], "relevant": m.relevant, "relevance": m.relevance,
        "sentiment": m.sentiment, "sentiment_source": m.sentiment_source, "tags": m.tags or [],
        "needs_response": m.needs_response, "response_reason": m.response_reason,
        "is_competitor": m.is_competitor,
        "competitor": {"id": m.competitor_id, "name": comps.get(str(m.competitor_id))} if m.competitor_id else None,
        "status": m.status, "idea_id": m.idea_id, "publisher": (m.raw or {}).get("publisher"),
        "link": (m.raw or {}).get("link"),
    }


async def _lookups(ctx: Context) -> tuple[dict[str, ListeningQuery], dict[str, str]]:
    queries = {str(q.id): q for q in (await ctx.session.scalars(select(ListeningQuery))).all()}
    comps = {str(c.id): c.name for c in (await ctx.session.scalars(select(Competitor))).all()}
    return queries, comps


async def _query(ctx: Context, qid: uuid.UUID) -> ListeningQuery:
    q = await ctx.session.get(ListeningQuery, qid)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That topic doesn't exist (it may have been deleted).")
    return q


async def _mention(ctx: Context, mid: uuid.UUID) -> ListeningMention:
    m = await ctx.session.get(ListeningMention, mid)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That mention doesn't exist (it may have been removed).")
    return m


# ------------------------------------------------------------------ status & settings
async def _status(ctx: Context) -> dict:
    s = await engine.get_settings(ctx.session, ctx.org_id)
    c = await src.build_ctx(ctx.session, s, datetime.now(UTC))
    avail = src.availability(c)
    on = src.enabled_sources(s)
    st = s.state or {}
    n_queries = await ctx.session.scalar(select(func.count()).select_from(ListeningQuery)) or 0
    new = await ctx.session.scalar(select(func.count()).select_from(ListeningMention).where(
        ListeningMention.relevant.is_(True), ListeningMention.status == "new")) or 0
    needs = await ctx.session.scalar(select(func.count()).select_from(ListeningMention).where(
        ListeningMention.relevant.is_(True), ListeningMention.status == "new", ListeningMention.needs_response.is_(True))) or 0
    last = st.get("last_run_at")
    nxt = None
    if s.enabled and last and n_queries:
        nxt = (datetime.fromisoformat(last) + timedelta(hours=s.interval_hours or 3)).isoformat()
    return {
        "settings": {"enabled": s.enabled, "interval_hours": s.interval_hours, "digest": s.digest,
                     "spike_alerts": s.spike_alerts, "use_ai": s.use_ai, "mastodon_instance": s.mastodon_instance,
                     "sources": on},
        "sources": [{"id": k, "label": v["label"], "note": v["note"], "enabled": on[k], "available": avail[k][0],
                     "missing": avail[k][1], "min_minutes": v["min_minutes"], "paid": bool(v.get("paid")),
                     "last": (st.get("sources") or {}).get(k)} for k, v in src.SOURCES.items()],
        "last_run_at": last, "next_run_at": nxt, "last_found": st.get("last_found"),
        "queries": n_queries, "max_queries": MAX_QUERIES, "new": new, "needs_response": needs,
        "kinds": list(QUERY_KINDS),
    }


@router.get("/status")
async def get_status(ctx: Context = Depends(require(VIEW))):
    out = await _status(ctx)
    await ctx.session.commit()
    return out


class SettingsIn(BaseModel):
    enabled: bool = True
    interval_hours: int = Field(3, ge=1, le=24)
    digest: Literal["off", "daily", "weekly"] = "weekly"
    spike_alerts: bool = True
    use_ai: bool = True
    mastodon_instance: str | None = Field(None, max_length=200)
    sources: dict[str, bool] = {}

    @field_validator("mastodon_instance")
    @classmethod
    def _inst(cls, v: str | None) -> str | None:
        v = (v or "").strip().rstrip("/")
        if not v:
            return None
        host = v.split("://", 1)[-1]
        if not host or "/" in host or " " in host or "." not in host:
            raise ValueError("Type the server's address, for example mastodon.social.")
        return host.lower()


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: Context = Depends(require(MANAGE))):
    s = await engine.get_settings(ctx.session, ctx.org_id)
    s.enabled, s.interval_hours, s.digest = body.enabled, body.interval_hours, body.digest
    s.spike_alerts, s.use_ai, s.mastodon_instance = body.spike_alerts, body.use_ai, body.mastodon_instance
    s.sources = {k: bool(v) for k, v in body.sources.items() if k in src.SOURCES}
    audit.record(ctx.session, action="listening.settings_updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="listening_settings", entity_id=s.id, summary="Changed listening settings",
                 details={"interval_hours": s.interval_hours, "digest": s.digest, "sources": s.sources})
    await ctx.session.commit()
    return await _status(ctx)


# ------------------------------------------------------------------ topics (queries)
def _codes(v: list[str], n: int, label: str) -> list[str]:
    out = []
    for x in v:
        x = str(x).strip()
        if not x:
            continue
        if not x.isalpha() or len(x) != n:
            raise ValueError(f"Use {n}-letter {label} codes" + (" such as en or hi." if n == 2 and label == "language"
                                                                  else " such as IN or US."))
        out.append(x.lower() if label == "language" else x.upper())
    return list(dict.fromkeys(out))[:5]


class QueryIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: Literal["brand", "competitor", "industry", "custom"] = "custom"
    terms: list[str] = Field(..., min_length=1, max_length=20)
    context_terms: list[str] = Field(default_factory=list, max_length=20)
    exclude: list[str] = Field(default_factory=list, max_length=40)
    languages: list[str] = Field(default_factory=list, max_length=5)
    countries: list[str] = Field(default_factory=list, max_length=3)
    sources: dict[str, bool] = {}
    competitor_id: uuid.UUID | None = None
    enabled: bool = True

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        return _codes(v, 2, "language")

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        return _codes(v, 2, "country")

    @field_validator("terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        out = clean_terms(v, 20)
        if not out:
            raise ValueError("Add at least one word or phrase to listen for.")
        if any(len(t.lstrip("#@")) < 2 for t in out):
            raise ValueError("Each word to listen for needs at least 2 letters.")
        return out


def _apply_query(q: ListeningQuery, body: QueryIn) -> None:
    q.name = body.name.strip()
    q.kind = body.kind
    q.terms = body.terms
    q.context_terms = clean_terms(body.context_terms, 20)
    q.exclude = clean_terms(body.exclude, 40)
    q.languages, q.countries = body.languages, body.countries
    q.sources = {k: bool(v) for k, v in body.sources.items() if k in src.SOURCES and not v}
    q.competitor_id = body.competitor_id if body.kind == "competitor" else None
    q.enabled = body.enabled


async def _check_competitor(ctx: Context, body: QueryIn) -> None:
    if body.competitor_id and body.kind == "competitor" and await ctx.session.get(Competitor, body.competitor_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That competitor doesn't exist any more.")


async def _counts_30d(ctx: Context) -> dict[str, int]:
    since = datetime.now(UTC) - timedelta(days=30)
    rows = (await ctx.session.execute(select(ListeningMention.query_ids).where(
        ListeningMention.relevant.is_(True), ListeningMention.status != "hidden", engine.when_col() >= since))).all()
    out: dict[str, int] = {}
    for (ids,) in rows:
        for i in ids or []:
            out[i] = out.get(i, 0) + 1
    return out


@router.get("/queries")
async def list_queries(ctx: Context = Depends(require(VIEW))):
    rows = (await ctx.session.scalars(select(ListeningQuery).order_by(ListeningQuery.created_at))).all()
    counts = await _counts_30d(ctx)
    order = {k: i for i, k in enumerate(QUERY_KINDS)}
    return [query_out(q, counts) for q in sorted(rows, key=lambda q: (order.get(q.kind, 9), q.created_at))]


@router.post("/queries", status_code=201)
async def create_query(body: QueryIn, ctx: Context = Depends(require(MANAGE))):
    n = await ctx.session.scalar(select(func.count()).select_from(ListeningQuery)) or 0
    if n >= MAX_QUERIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"You can track up to {MAX_QUERIES} topics. Delete one first.")
    await _check_competitor(ctx, body)
    q = ListeningQuery(organization_id=ctx.org_id, state={})
    _apply_query(q, body)
    ctx.session.add(q)
    await ctx.session.flush()
    audit.record(ctx.session, action="listening.query_created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="listening_query", entity_id=q.id, summary=f"Started listening for “{q.name}”",
                 details={"terms": q.terms, "kind": q.kind})
    await ctx.session.commit()
    return query_out(q)


@router.put("/queries/{qid}")
async def update_query(qid: uuid.UUID, body: QueryIn, ctx: Context = Depends(require(MANAGE))):
    q = await _query(ctx, qid)
    await _check_competitor(ctx, body)
    before = (list(q.terms or []), list(q.context_terms or []), list(q.exclude or []))
    _apply_query(q, body)
    if before != (q.terms, q.context_terms, q.exclude):
        q.state = {**(q.state or {}), "sources": {}}  # changed words: search again from a week back
    audit.record(ctx.session, action="listening.query_updated", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="listening_query", entity_id=q.id, summary=f"Changed listening topic “{q.name}”")
    await ctx.session.commit()
    return query_out(q, await _counts_30d(ctx))


@router.delete("/queries/{qid}", status_code=204)
async def delete_query(qid: uuid.UUID, purge: bool = False, ctx: Context = Depends(require(MANAGE))):
    """purge=true also deletes mentions found only by this topic."""
    q = await _query(ctx, qid)
    removed = 0
    if purge:
        rows = (await ctx.session.scalars(select(ListeningMention).where(ListeningMention.query_id == q.id))).all()
        for m in rows:
            others = [i for i in (m.query_ids or []) if i != str(q.id)]
            if others:
                m.query_ids, m.query_id = others, uuid.UUID(others[0])
            else:
                await ctx.session.delete(m)
                removed += 1
    audit.record(ctx.session, action="listening.query_deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="listening_query", entity_id=q.id, summary=f"Stopped listening for “{q.name}”",
                 details={"mentions_removed": removed})
    await ctx.session.delete(q)
    await ctx.session.commit()


@router.get("/suggestions")
async def suggestions(ctx: Context = Depends(require(VIEW))):
    return await assist.starter_queries(ctx.session)


class StarterIn(BaseModel):
    names: list[str] | None = None  # create only these suggestions (default: all that don't exist yet)


@router.post("/queries/starter", status_code=201)
async def create_starter(body: StarterIn, ctx: Context = Depends(require(MANAGE))):
    wanted = {n.lower() for n in body.names} if body.names else None
    n = await ctx.session.scalar(select(func.count()).select_from(ListeningQuery)) or 0
    created = []
    for d in await assist.starter_queries(ctx.session):
        if d["exists"] or (wanted is not None and d["name"].lower() not in wanted) or n >= MAX_QUERIES:
            continue
        q = ListeningQuery(organization_id=ctx.org_id, name=d["name"], kind=d["kind"], terms=d["terms"],
                           context_terms=d["context_terms"], exclude=d["exclude"], languages=d["languages"],
                           countries=d["countries"], sources={}, enabled=True, state={},
                           competitor_id=uuid.UUID(d["competitor_id"]) if d["competitor_id"] else None)
        ctx.session.add(q)
        created.append(q)
        n += 1
    await ctx.session.flush()
    if created:
        audit.record(ctx.session, action="listening.starter_created", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="listening_query", summary=f"Added {len(created)} suggested listening topics",
                     details={"names": [q.name for q in created]})
    await ctx.session.commit()
    return [query_out(q) for q in created]


# ------------------------------------------------------------------ run now
class RunIn(BaseModel):
    query_id: uuid.UUID | None = None


@router.post("/run", status_code=202)
async def run_now(body: RunIn, ctx: Context = Depends(require(MANAGE))):
    n = await ctx.session.scalar(select(func.count()).select_from(ListeningQuery).where(ListeningQuery.enabled.is_(True)))
    if not n:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add a topic to listen for first.")
    payload: dict = {"force": True}
    if body.query_id:
        await _query(ctx, body.query_id)
        payload["query_id"] = str(body.query_id)
    j = await enqueue(ctx.session, "listening.run", ctx.org_id, payload, dedupe_key=f"listening.run:{ctx.org_id}",
                      max_attempts=1, priority=3, created_by=ctx.user.id)
    await ctx.session.commit()
    return {"job_id": j.id}


# ------------------------------------------------------------------ mentions feed
@router.get("/mentions")
async def list_mentions(
    query_id: uuid.UUID | None = None, source: str | None = None,
    sentiment: Literal["positive", "neutral", "negative"] | None = None,
    status_: Literal["new", "reviewed", "hidden", "open", "all"] = Query("open", alias="status"),
    needs_response: bool | None = None, competitor: bool | None = None, kind: str | None = None,
    days: int | None = Query(None, ge=1, le=365), since: datetime | None = None, until: datetime | None = None,
    q: str | None = Query(None, max_length=200), filtered: bool = False,
    sort: Literal["newest", "engagement"] = "newest",
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    ctx: Context = Depends(require(VIEW)),
):
    """status=open (default) shows new + reviewed; filtered=true shows only what the relevance check removed."""
    conds = [ListeningMention.relevant.is_(not filtered)]
    if status_ == "open":
        conds.append(ListeningMention.status != "hidden")
    elif status_ != "all":
        conds.append(ListeningMention.status == status_)
    if source:
        conds.append(ListeningMention.source == source)
    if sentiment:
        conds.append(ListeningMention.sentiment == sentiment)
    if needs_response is not None:
        conds.append(ListeningMention.needs_response.is_(needs_response))
    if competitor is not None:
        conds.append(ListeningMention.is_competitor.is_(competitor))
    when = engine.when_col()
    if days:
        conds.append(when >= datetime.now(UTC) - timedelta(days=days))
    if since:
        conds.append(when >= since)
    if until:
        conds.append(when < until)
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(ListeningMention.title.ilike(like), ListeningMention.snippet.ilike(like),
                         ListeningMention.author_name.ilike(like), ListeningMention.domain.ilike(like)))
    queries, comps = await _lookups(ctx)
    qid = str(query_id) if query_id else None
    kind_ids = {k for k, v in queries.items() if v.kind == kind} if kind else None
    stmt = select(ListeningMention).where(*conds)
    if qid or kind_ids is not None:
        # query_ids is JSON; filter in Python over a bounded window
        rows = (await ctx.session.scalars(stmt.order_by(when.desc()).limit(3000))).all()
        rows = [m for m in rows if (not qid or qid in (m.query_ids or []) or str(m.query_id) == qid)
                and (kind_ids is None or set(m.query_ids or []) & kind_ids)]
        total = len(rows)
        if sort == "engagement":
            rows = sorted(rows, key=insights.engagement, reverse=True)
        page = rows[offset: offset + limit]
    else:
        total = await ctx.session.scalar(select(func.count()).select_from(ListeningMention).where(*conds)) or 0
        if sort == "engagement":
            eng = (func.coalesce(ListeningMention.score, 0) + 2 * func.coalesce(ListeningMention.comments, 0)
                   + func.coalesce(ListeningMention.reach, 0) / 100)
            stmt = stmt.order_by(eng.desc(), when.desc())
        else:
            stmt = stmt.order_by(when.desc(), ListeningMention.created_at.desc())
        page = (await ctx.session.scalars(stmt.offset(offset).limit(limit))).all()
    return {"items": [mention_out(m, queries, comps) for m in page], "total": total}


class MentionPatch(BaseModel):
    status: Literal["new", "reviewed", "hidden"] | None = None
    needs_response: bool | None = None
    sentiment: Literal["positive", "neutral", "negative"] | None = None
    relevant: bool | None = None


@router.patch("/mentions/{mid}")
async def patch_mention(mid: uuid.UUID, body: MentionPatch, ctx: Context = Depends(require(MANAGE))):
    m = await _mention(ctx, mid)
    if body.status is not None:
        m.status = body.status
        m.reviewed_by = ctx.user.id if body.status != "new" else None
    if body.needs_response is not None:
        m.needs_response = body.needs_response
        if not body.needs_response:
            m.response_reason = None
    if body.sentiment is not None:
        m.sentiment, m.sentiment_source = body.sentiment, "manual"
    if body.relevant is not None:
        m.relevant = body.relevant
    await ctx.session.commit()
    queries, comps = await _lookups(ctx)
    return mention_out(m, queries, comps)


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)
    status: Literal["new", "reviewed", "hidden"]


@router.post("/mentions/bulk")
async def bulk_status(body: BulkIn, ctx: Context = Depends(require(MANAGE))):
    r = await ctx.session.execute(update(ListeningMention).where(ListeningMention.id.in_(body.ids)).values(
        status=body.status, reviewed_by=ctx.user.id if body.status != "new" else None))
    await ctx.session.commit()
    return {"updated": r.rowcount}


def _ai_error(e: Exception) -> HTTPException:
    if isinstance(e, NoProviderError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, "Set up an AI text model in Settings → Integrations to draft replies.")
    if isinstance(e, AllProvidersFailed):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI models didn't answer. Try again in a minute.")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI model didn't return usable drafts. Try again.")


@router.post("/mentions/{mid}/draft")
async def draft_reply(mid: uuid.UUID, ctx: Context = Depends(require(VIEW))):
    m = await _mention(ctx, mid)
    q = await ctx.session.get(ListeningQuery, m.query_id) if m.query_id else None
    try:
        drafts = await assist.draft_replies(ctx.session, m, q)
    except Exception as e:  # noqa: BLE001 - friendly message, never a stack trace
        raise _ai_error(e) from e
    await ctx.session.commit()  # token usage
    if not drafts:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The AI model didn't return usable drafts. Try again.")
    return {"drafts": drafts, "limit": assist.REPLY_LIMITS.get(m.source, 1000), "url": m.url,
            "where": _src_label(m.source)}


@router.post("/mentions/{mid}/idea", status_code=201)
async def make_idea(mid: uuid.UUID, ctx: Context = Depends(require(MANAGE, "research.view"))):
    m = await _mention(ctx, mid)
    if m.idea_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already made a post idea from this mention.")
    q = await ctx.session.get(ListeningQuery, m.query_id) if m.query_id else None
    b = await assist.create_idea(ctx.session, m, q)
    audit.record(ctx.session, action="listening.idea_created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="idea_brief", entity_id=b.id, summary=f"Made a post idea from a {_src_label(m.source)} mention")
    if m.status == "new":
        m.status = "reviewed"
        m.reviewed_by = ctx.user.id
    await ctx.session.commit()
    return {"idea_id": b.id, "title": b.title, "link": f"/research?brief={b.id}"}


# ------------------------------------------------------------------ insights
@router.get("/overview")
async def overview(days: int = Query(30, ge=1, le=180), query_id: uuid.UUID | None = None,
                   ctx: Context = Depends(require(VIEW))):
    out = await insights.overview(ctx.session, days, str(query_id) if query_id else None)
    await ctx.session.commit()
    return out


class SummaryIn(BaseModel):
    days: int = Field(7, ge=1, le=90)


@router.post("/summary", status_code=202)
async def summary(body: SummaryIn, ctx: Context = Depends(require(VIEW))):
    j = await enqueue(ctx.session, "listening.summary", ctx.org_id, {"days": body.days},
                      dedupe_key=f"listening.summary:{ctx.org_id}", max_attempts=1, priority=3, created_by=ctx.user.id)
    await ctx.session.commit()
    return {"job_id": j.id}


@router.post("/digest", status_code=202)
async def digest_now(ctx: Context = Depends(require(MANAGE))):
    """Send the digest now (to try it out)."""
    j = await enqueue(ctx.session, "listening.digest", ctx.org_id, {"force": True},
                      dedupe_key=f"listening.digest:{ctx.org_id}", max_attempts=1, priority=3, created_by=ctx.user.id)
    await ctx.session.commit()
    return {"job_id": j.id}
