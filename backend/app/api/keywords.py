"""Keywords & rankings: keyword research, competitor keyword gap, saved keyword lists with topic clusters, and daily
rank tracking. Paid keyword data comes from DataForSEO when it is configured (Settings → Integrations)."""

from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import Context, require
from app.models import KeywordList, KeywordReport, TrackedKeyword
from app.services import audit
from app.services.jobs import enqueue
from app.services.keywords import clusters, ranks
from app.services.keywords import service as svc
from app.services.keywords.provider import DEVICES, LANGUAGES, LOCATIONS, SERP_LIVE, ProviderError, clean_domain

router = APIRouter(prefix="/keywords", tags=["keywords"])
MAX_LIST_ITEMS = 2000
INTENTS = ("informational", "commercial", "transactional", "navigational")


# ------------------------------------------------------------------ schemas
class SettingsIn(BaseModel):
    location_code: int | None = None
    language_code: str | None = Field(None, max_length=10)
    device: str | None = Field(None, pattern="^(desktop|mobile)$")
    daily_budget_usd: float | None = Field(None, ge=0, le=1000)
    max_tracked: int | None = Field(None, ge=1, le=2000)
    check_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    rank_tracking_enabled: bool | None = None
    alerts: dict | None = None
    research_limit: int | None = Field(None, ge=10, le=1000)
    gap_limit: int | None = Field(None, ge=10, le=1000)
    cache_days: int | None = Field(None, ge=1, le=90)
    serp_depth: int | None = Field(None, ge=10, le=100)
    cluster_overlap: int | None = Field(None, ge=1, le=8)
    our_domain: str | None = Field(None, max_length=253)


class ResearchIn(BaseModel):
    seeds: list[str] = Field(min_length=1, max_length=5)
    mode: str = Field("suggestions", pattern="^(suggestions|related|ideas|domain)$")
    location_code: int | None = None
    language_code: str | None = Field(None, max_length=10)
    limit: int | None = Field(None, ge=10, le=1000)


class GapIn(BaseModel):
    our_domain: str | None = Field(None, max_length=253)
    competitors: list[str] = Field(min_length=1, max_length=4)
    location_code: int | None = None
    language_code: str | None = Field(None, max_length=10)
    limit: int | None = Field(None, ge=10, le=1000)


class EstimateIn(BaseModel):
    action: str = Field(pattern="^(research|gap|rank_check|cluster)$")
    research: ResearchIn | None = None
    gap: GapIn | None = None
    list_id: uuid.UUID | None = None
    use_serp: bool = True


class ListIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    items: list[dict] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)


class ListPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class ItemsIn(BaseModel):
    items: list[dict] = Field(min_length=1, max_length=MAX_LIST_ITEMS)


class RemoveIn(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=MAX_LIST_ITEMS)


class ClusterIn(BaseModel):
    use_serp: bool = True


class WriteIn(BaseModel):
    keyword: str | None = Field(None, min_length=2, max_length=200)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=30)
    intent: str | None = None
    list_id: uuid.UUID | None = None
    cluster_id: str | None = Field(None, max_length=40)
    word_target: int = Field(1500, ge=500, le=5000)


class TrackIn(BaseModel):
    keywords: list[str | dict] = Field(min_length=1, max_length=500)
    target_url: str | None = Field(None, max_length=500)
    location_code: int | None = None
    language_code: str | None = Field(None, max_length=10)
    device: str | None = Field(None, pattern="^(desktop|mobile)$")
    tags: list[str] = Field(default_factory=list, max_length=10)


class TrackPatch(BaseModel):
    target_url: str | None = Field(None, max_length=500)
    active: bool | None = None
    tags: list[str] | None = Field(None, max_length=10)
    volume: int | None = Field(None, ge=0)


class CheckIn(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    force: bool = False


# ------------------------------------------------------------------ helpers
def _num(v, cast=float):
    try:
        return cast(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def clean_item(raw: dict) -> dict | None:
    kw = " ".join(str(raw.get("keyword") or "").split())[:200]
    if not kw:
        return None
    intent = str(raw.get("intent") or "").lower() or None
    gsc = raw.get("gsc") if isinstance(raw.get("gsc"), dict) else None
    return {
        "keyword": kw, "volume": _num(raw.get("volume"), int), "kd": _num(raw.get("kd"), int), "cpc": _num(raw.get("cpc")),
        "competition": _num(raw.get("competition")), "intent": intent if intent in INTENTS else None,
        "trend": [int(x) for x in (raw.get("trend") or [])[:24] if isinstance(x, int | float)],
        "trend_months": [str(x)[:7] for x in (raw.get("trend_months") or [])[:24]],
        "serp_features": [str(x)[:40] for x in (raw.get("serp_features") or [])[:12]],
        "source": str(raw.get("source") or "")[:30] or None, "estimated": bool(raw.get("estimated")),
        "demand": _num(raw.get("demand"), int), "intent_guessed": bool(raw.get("intent_guessed")),
        "gsc": {k: gsc.get(k) for k in ("clicks", "impressions", "position", "ctr")} if gsc else None,
        "added_at": svc.now().isoformat(),
    }


def report_out(r: KeywordReport, rows: bool = True) -> dict:
    out = {"id": r.id, "kind": r.kind, "title": r.title, "status": r.status, "params": r.params, "meta": r.meta or {},
           "error": r.error, "job_id": r.job_id, "created_at": r.created_at, "count": len(r.rows or [])}
    if rows:
        out["rows"] = r.rows or []
    return out


def list_out(kl: KeywordList, full: bool = True) -> dict:
    items = kl.items or []
    vols = [i.get("volume") for i in items if i.get("volume") is not None]
    out = {"id": kl.id, "name": kl.name, "description": kl.description, "count": len(items),
           "volume": sum(vols) if vols else None, "clusters_count": len(kl.clusters or []),
           "cluster_meta": kl.cluster_meta or {}, "created_at": kl.created_at, "updated_at": kl.updated_at}
    if full:
        out.update({"items": items, "clusters": kl.clusters or []})
    return out


async def _list(ctx: Context, lid: uuid.UUID) -> KeywordList:
    kl = await ctx.session.scalar(select(KeywordList).where(KeywordList.id == lid))
    if kl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keyword list not found.")
    return kl


async def _report(ctx: Context, rid: uuid.UUID) -> KeywordReport:
    r = await ctx.session.scalar(select(KeywordReport).where(KeywordReport.id == rid))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found.")
    return r


def _csv(rows: list[list], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _merge_items(kl: KeywordList, items: list[dict]) -> int:
    current = list(kl.items or [])
    index = {svc.norm(i["keyword"]): n for n, i in enumerate(current)}
    added = 0
    for raw in items:
        it = clean_item(raw) if isinstance(raw, dict) else None
        if not it:
            continue
        k = svc.norm(it["keyword"])
        if k in index:
            old = current[index[k]]
            current[index[k]] = {**old, **{f: v for f, v in it.items() if v not in (None, [], "") and f not in ("added_at", "keyword")}}
        elif len(current) < MAX_LIST_ITEMS:
            index[k] = len(current)
            current.append(it)
            added += 1
    kl.items = current
    return added


# ------------------------------------------------------------------ overview & settings
@router.get("/overview")
async def overview(ctx: Context = Depends(require("keywords.view"))):
    s = ctx.session
    row = await s.scalar(select(svc.KeywordSettings).where(svc.KeywordSettings.organization_id == ctx.org_id))
    cfg = svc.effective_config(row)
    pcfg = await svc.provider_config(s)
    gsc = await svc.latest_gsc(s)
    return {
        "provider": {"configured": pcfg is not None, "name": pcfg.name if pcfg else None, "id": pcfg.provider if pcfg else None},
        "gsc_connected": gsc is not None, "gsc_site": (gsc or {}).get("site"),
        "our_domain": await svc.our_domain(s, ctx.org, cfg),
        "settings": cfg, "spend": await svc.spend_summary(s, ctx.org, cfg),
        "locations": [{"code": c, "name": n} for c, n in LOCATIONS.items()],
        "languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()], "devices": list(DEVICES),
        "counts": {
            "lists": await s.scalar(select(func.count()).select_from(KeywordList)) or 0,
            "tracked": await s.scalar(select(func.count()).select_from(TrackedKeyword)) or 0,
            "reports": await s.scalar(select(func.count()).select_from(KeywordReport)) or 0,
        },
        "recent_seeds": ((row.data or {}).get("recent_seeds") if row else None) or [],
        "can_write_articles": "seo.manage" in ctx.permissions,
    }


@router.get("/settings")
async def get_settings(ctx: Context = Depends(require("keywords.view"))):
    return await svc.config_for(ctx.session, ctx.org_id)


@router.put("/settings")
async def put_settings(body: SettingsIn, ctx: Context = Depends(require("keywords.manage"))):
    row = await svc.get_settings_row(ctx.session, ctx.org_id)
    data = body.model_dump(exclude_unset=True)
    if "location_code" in data and data["location_code"] not in LOCATIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a location from the list.")
    if "language_code" in data and data["language_code"] not in LANGUAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a language from the list.")
    if "our_domain" in data:
        data["our_domain"] = clean_domain(data["our_domain"] or "")
    if "alerts" in data:
        a = data["alerts"] or {}
        data["alerts"] = {"enabled": bool(a.get("enabled", True)), "drop": max(1, min(int(a.get("drop") or 5), 50)),
                          "top3": bool(a.get("top3", True)), "top10": bool(a.get("top10", True))}
    row.config = {**(row.config or {}), **data}
    audit.record(ctx.session, action="keywords.settings_changed", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="keyword_settings", entity_id=row.id, summary="Changed keyword & ranking settings", ip=ctx.ip)
    await ctx.session.commit()
    return svc.effective_config(row)


@router.get("/spend")
async def spend(ctx: Context = Depends(require("keywords.view"))):
    cfg = await svc.config_for(ctx.session, ctx.org_id)
    return await svc.spend_summary(ctx.session, ctx.org, cfg)


@router.post("/estimate")
async def estimate(body: EstimateIn, ctx: Context = Depends(require("keywords.view"))):
    s = ctx.session
    cfg = await svc.config_for(s, ctx.org_id)
    prov = await svc.get_provider(s)
    if body.action == "research":
        if body.research is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add a seed keyword.")
        return await svc.estimate_research(s, prov, svc.research_params(cfg, body.research.model_dump()))
    if body.action == "gap":
        if body.gap is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add competitor domains.")
        ours = await svc.our_domain(s, ctx.org, cfg)
        return await svc.estimate_gap(s, prov, svc.gap_params(cfg, body.gap.model_dump(), ours))
    if body.action == "cluster":
        if body.list_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a keyword list.")
        return await clusters.estimate(s, ctx.org, await _list(ctx, body.list_id), body.use_serp)
    n = await s.scalar(select(func.count()).select_from(TrackedKeyword).where(TrackedKeyword.active.is_(True))) or 0
    if prov is None:
        return {"cost_usd": 0.0, "keywords": n,
                "note": "Free: approximate positions from web search (top 20), plus Search Console's average position."}
    per = prov.estimate("serp_organic", depth=int(cfg.get("serp_depth") or 100))
    return {"cost_usd": round(per * n, 4), "keywords": n, "per_keyword_usd": per,
            "note": f"About ${per:.3f} per keyword per day (≈ ${per * n * 30:.2f} a month)."}


# ------------------------------------------------------------------ research & gap reports
async def _start_report(ctx: Context, kind: str, title: str, params: dict) -> dict:
    rep = KeywordReport(organization_id=ctx.org_id, kind=kind, title=title[:300], status="queued", params=params, rows=[],
                        meta={}, created_by=ctx.user.id)
    ctx.session.add(rep)
    await ctx.session.flush()
    job = await enqueue(ctx.session, f"keywords.{kind}", ctx.org_id, {"report_id": str(rep.id)}, created_by=ctx.user.id,
                        dedupe_key=f"keywords.{kind}:{rep.id}", max_attempts=2, priority=4)
    rep.job_id = job.id
    await ctx.session.commit()
    return {"report": report_out(rep, rows=False), "job_id": job.id}


@router.post("/research", status_code=202)
async def start_research(body: ResearchIn, ctx: Context = Depends(require("keywords.manage"))):
    cfg = await svc.config_for(ctx.session, ctx.org_id)
    p = svc.research_params(cfg, body.model_dump())
    if not p["seeds"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add a seed keyword or a domain.")
    row = await svc.get_settings_row(ctx.session, ctx.org_id)
    recent = [x for x in ((row.data or {}).get("recent_seeds") or []) if x not in p["seeds"]]
    row.data = {**(row.data or {}), "recent_seeds": (p["seeds"] + recent)[:10]}
    label = {"domain": "Keywords for", "related": "Related to", "ideas": "Ideas for"}.get(p["mode"], "Keywords like")
    return await _start_report(ctx, "research", f"{label} {', '.join(p['seeds'])}", p)


@router.post("/gap", status_code=202)
async def start_gap(body: GapIn, ctx: Context = Depends(require("keywords.manage"))):
    cfg = await svc.config_for(ctx.session, ctx.org_id)
    ours = await svc.our_domain(ctx.session, ctx.org, cfg)
    p = svc.gap_params(cfg, body.model_dump(), ours)
    if not p["our_domain"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add your website address first.")
    if not p["competitors"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add at least one competitor domain (different from yours).")
    return await _start_report(ctx, "gap", f"{p['our_domain']} vs {', '.join(p['competitors'])}", p)


@router.get("/gap/competitors")
async def gap_competitors(ctx: Context = Depends(require("keywords.view"))):
    return {"competitors": await svc.competitor_domains(ctx.session)}


@router.post("/gap/suggest")
async def gap_suggest(ctx: Context = Depends(require("keywords.manage"))):
    try:
        out = await svc.suggest_competitors(ctx.session, ctx.org)
    except (ValueError, svc.BudgetError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except ProviderError as e:
        await ctx.session.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    await ctx.session.commit()
    return out


@router.get("/reports")
async def list_reports(kind: str | None = None, ctx: Context = Depends(require("keywords.view"))):
    q = select(KeywordReport).order_by(KeywordReport.created_at.desc()).limit(30)
    if kind:
        q = q.where(KeywordReport.kind == kind)
    return [report_out(r, rows=False) for r in (await ctx.session.scalars(q)).all()]


@router.get("/reports/{rid}")
async def get_report(rid: uuid.UUID, ctx: Context = Depends(require("keywords.view"))):
    return report_out(await _report(ctx, rid))


@router.delete("/reports/{rid}", status_code=204)
async def delete_report(rid: uuid.UUID, ctx: Context = Depends(require("keywords.manage"))):
    await ctx.session.delete(await _report(ctx, rid))
    await ctx.session.commit()
    return Response(status_code=204)


@router.get("/reports/{rid}/export.csv")
async def export_report(rid: uuid.UUID, category: str | None = None, ctx: Context = Depends(require("keywords.view"))):
    r = await _report(ctx, rid)
    name = f"keyword-{r.kind}{'-' + category if category and category != 'all' else ''}-{r.created_at:%Y%m%d}.csv"
    return _csv(svc.csv_rows(r, category), name)


# ------------------------------------------------------------------ keyword lists
@router.get("/lists")
async def lists(ctx: Context = Depends(require("keywords.view"))):
    rows = (await ctx.session.scalars(select(KeywordList).order_by(KeywordList.updated_at.desc()))).all()
    return [list_out(k, full=False) for k in rows]


@router.post("/lists", status_code=201)
async def create_list(body: ListIn, ctx: Context = Depends(require("keywords.manage"))):
    kl = KeywordList(organization_id=ctx.org_id, name=body.name.strip(), description=(body.description or "").strip() or None,
                     items=[], clusters=[], cluster_meta={}, created_by=ctx.user.id)
    _merge_items(kl, body.items)
    ctx.session.add(kl)
    await ctx.session.flush()
    audit.record(ctx.session, action="keywords.list_created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="keyword_list", entity_id=kl.id, summary=f"Created keyword list “{kl.name}”", ip=ctx.ip)
    await ctx.session.commit()
    return list_out(kl)


@router.get("/lists/{lid}")
async def get_list(lid: uuid.UUID, ctx: Context = Depends(require("keywords.view"))):
    return list_out(await _list(ctx, lid))


@router.patch("/lists/{lid}")
async def patch_list(lid: uuid.UUID, body: ListPatch, ctx: Context = Depends(require("keywords.manage"))):
    kl = await _list(ctx, lid)
    if body.name is not None:
        kl.name = body.name.strip()
    if body.description is not None:
        kl.description = body.description.strip() or None
    await ctx.session.commit()
    return list_out(kl)


@router.delete("/lists/{lid}", status_code=204)
async def delete_list(lid: uuid.UUID, ctx: Context = Depends(require("keywords.manage"))):
    kl = await _list(ctx, lid)
    audit.record(ctx.session, action="keywords.list_deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="keyword_list", entity_id=kl.id, summary=f"Deleted keyword list “{kl.name}”", ip=ctx.ip)
    await ctx.session.delete(kl)
    await ctx.session.commit()
    return Response(status_code=204)


@router.post("/lists/{lid}/items")
async def add_items(lid: uuid.UUID, body: ItemsIn, ctx: Context = Depends(require("keywords.manage"))):
    kl = await _list(ctx, lid)
    added = _merge_items(kl, body.items)
    await ctx.session.commit()
    return {**list_out(kl), "added": added}


@router.post("/lists/{lid}/items/remove")
async def remove_items(lid: uuid.UUID, body: RemoveIn, ctx: Context = Depends(require("keywords.manage"))):
    kl = await _list(ctx, lid)
    drop = {svc.norm(k) for k in body.keywords}
    kl.items = [i for i in (kl.items or []) if svc.norm(i["keyword"]) not in drop]
    kl.clusters = [c for c in ({**c, "keywords": [k for k in c["keywords"] if svc.norm(k) not in drop]}
                               for c in (kl.clusters or [])) if c["keywords"]]
    await ctx.session.commit()
    return list_out(kl)


@router.post("/lists/{lid}/cluster", status_code=202)
async def cluster(lid: uuid.UUID, body: ClusterIn, ctx: Context = Depends(require("keywords.manage"))):
    kl = await _list(ctx, lid)
    if len(kl.items or []) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add at least two keywords to the list first.")
    job = await enqueue(ctx.session, "keywords.cluster", ctx.org_id, {"list_id": str(kl.id), "use_serp": body.use_serp},
                        created_by=ctx.user.id, dedupe_key=f"keywords.cluster:{kl.id}", max_attempts=2, priority=4)
    await ctx.session.commit()
    return {"job_id": job.id}


@router.get("/lists/{lid}/export.csv")
async def export_list(lid: uuid.UUID, ctx: Context = Depends(require("keywords.view"))):
    kl = await _list(ctx, lid)
    cluster_of = {svc.norm(k): c["name"] for c in (kl.clusters or []) for k in c["keywords"]}
    rows = [["keyword", "cluster", "volume", "kd", "cpc", "intent", "source", "estimated"]]
    for i in kl.items or []:
        rows.append([i["keyword"], cluster_of.get(svc.norm(i["keyword"])), i.get("volume"), i.get("kd"), i.get("cpc"),
                     i.get("intent"), i.get("source"), "yes" if i.get("estimated") else "no"])
    return _csv(rows, f"{svc.norm(kl.name).replace(' ', '-')[:60] or 'keywords'}.csv")


# ------------------------------------------------------------------ write an article
@router.post("/write-article", status_code=201)
async def write_article(body: WriteIn, ctx: Context = Depends(require("keywords.manage"))):
    if "seo.manage" not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You need the 'seo.manage' permission to start articles.")
    from app.api import seo as seo_api

    keyword, secondary, intent = body.keyword, list(body.secondary_keywords), body.intent
    if body.list_id and body.cluster_id:
        kl = await _list(ctx, body.list_id)
        c = next((c for c in (kl.clusters or []) if c.get("id") == body.cluster_id), None)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found. Group the list again.")
        keyword = c["pillar"]
        secondary = [k for k in c["keywords"] if k != c["pillar"]]
        intent = intent or c.get("intent")
    if not keyword:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a keyword.")
    out = await seo_api.create_article(seo_api.ArticleIn(
        keyword=keyword, secondary_keywords=[k for k in secondary if k.strip()][:15], word_target=body.word_target,
        search_intent=intent if intent in INTENTS else None), ctx)
    return out


# ------------------------------------------------------------------ rank tracking
@router.get("/ranks")
async def rank_overview(days: int = 30, ctx: Context = Depends(require("keywords.view"))):
    out = await ranks.overview(ctx.session, ctx.org, days)
    pcfg = await svc.provider_config(ctx.session)
    cfg = await svc.config_for(ctx.session, ctx.org_id)
    out["source"] = "dataforseo" if pcfg else "search"
    out["approximate"] = pcfg is None
    out["depth"] = 100 if pcfg else ranks.FREE_DEPTH
    out["gsc_connected"] = await svc.latest_gsc(ctx.session) is not None
    out["our_domain"] = await svc.our_domain(ctx.session, ctx.org, cfg)
    out["max_tracked"] = cfg["max_tracked"]
    out["check_time"] = cfg["check_time"]
    out["enabled"] = cfg["rank_tracking_enabled"]
    out["per_keyword_usd"] = SERP_LIVE if pcfg else 0.0
    return out


@router.post("/ranks", status_code=201)
async def track(body: TrackIn, ctx: Context = Depends(require("keywords.manage"))):
    s = ctx.session
    cfg = await svc.config_for(s, ctx.org_id)
    loc = body.location_code or cfg["location_code"]
    lang = body.language_code or cfg["language_code"]
    device = body.device or cfg["device"]
    if loc not in LOCATIONS or lang not in LANGUAGES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a location and language from the list.")
    have = await s.scalar(select(func.count()).select_from(TrackedKeyword)) or 0
    existing = {(svc.norm(k), lo, la, d) for k, lo, la, d in (await s.execute(select(
        TrackedKeyword.keyword, TrackedKeyword.location_code, TrackedKeyword.language_code, TrackedKeyword.device))).all()}
    added, skipped, over = [], 0, 0
    for raw in body.keywords:
        item = raw if isinstance(raw, dict) else {"keyword": raw}
        kw = " ".join(str(item.get("keyword") or "").split())[:200]
        if not kw:
            continue
        key = (svc.norm(kw), loc, lang, device)
        if key in existing:
            skipped += 1
            continue
        if have + len(added) >= cfg["max_tracked"]:
            over += 1
            continue
        existing.add(key)
        t = TrackedKeyword(organization_id=ctx.org_id, keyword=kw, target_url=(body.target_url or "").strip() or None,
                           location_code=loc, language_code=lang, device=device, active=True,
                           tags=[t.strip()[:40] for t in body.tags if t.strip()], volume=_num(item.get("volume"), int), data={})
        s.add(t)
        added.append(t)
    await s.flush()
    if added:
        audit.record(s, action="keywords.tracked", organization_id=ctx.org_id, actor=ctx.user, entity_type="tracked_keyword",
                     summary=f"Started tracking {len(added)} keyword(s)", ip=ctx.ip)
    await s.commit()
    msg = f"Tracking {len(added)} new keyword{'s' if len(added) != 1 else ''}."
    if skipped:
        msg += f" {skipped} already tracked."
    if over:
        msg += f" {over} not added: the limit is {cfg['max_tracked']} keywords (change it in Settings)."
    return {"added": len(added), "skipped": skipped, "over_limit": over, "ids": [t.id for t in added], "message": msg}


async def _tracked(ctx: Context, kid: uuid.UUID) -> TrackedKeyword:
    t = await ctx.session.scalar(select(TrackedKeyword).where(TrackedKeyword.id == kid))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tracked keyword not found.")
    return t


@router.patch("/ranks/{kid}")
async def patch_tracked(kid: uuid.UUID, body: TrackPatch, ctx: Context = Depends(require("keywords.manage"))):
    t = await _tracked(ctx, kid)
    data = body.model_dump(exclude_unset=True)
    if "target_url" in data:
        t.target_url = (data["target_url"] or "").strip() or None
    if data.get("active") is not None:
        t.active = data["active"]
    if data.get("tags") is not None:
        t.tags = [x.strip()[:40] for x in data["tags"] if x.strip()]
    if "volume" in data:
        t.volume = data["volume"]
    await ctx.session.commit()
    return {"id": t.id, "keyword": t.keyword, "target_url": t.target_url, "active": t.active, "tags": t.tags, "volume": t.volume}


@router.delete("/ranks/{kid}", status_code=204)
async def untrack(kid: uuid.UUID, ctx: Context = Depends(require("keywords.manage"))):
    t = await _tracked(ctx, kid)
    audit.record(ctx.session, action="keywords.untracked", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="tracked_keyword", entity_id=t.id, summary=f"Stopped tracking “{t.keyword}”", ip=ctx.ip)
    await ctx.session.delete(t)
    await ctx.session.commit()
    return Response(status_code=204)


@router.get("/ranks/{kid}/history")
async def rank_history(kid: uuid.UUID, days: int = 90, ctx: Context = Depends(require("keywords.view"))):
    return await ranks.history(ctx.session, await _tracked(ctx, kid), days)


@router.post("/ranks/check", status_code=202)
async def check_now(body: CheckIn, ctx: Context = Depends(require("keywords.manage"))):
    n = await ctx.session.scalar(select(func.count()).select_from(TrackedKeyword).where(TrackedKeyword.active.is_(True))) or 0
    if not n:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Track some keywords first.")
    job = await enqueue(ctx.session, "keywords.rank_check", ctx.org_id, {"ids": [str(i) for i in body.ids], "force": body.force},
                        created_by=ctx.user.id, dedupe_key=f"keywords.rank_check:{ctx.org_id}:manual", max_attempts=2, priority=4)
    await ctx.session.commit()
    return {"job_id": job.id}


@router.get("/ranks-export.csv")
async def export_ranks(ctx: Context = Depends(require("keywords.view"))):
    o = await ranks.overview(ctx.session, ctx.org, 30)
    rows = [["keyword", "position", "previous", "change", "best", "search_console_position", "url", "target_url", "volume",
             "device", "location", "source", "last_checked"]]
    for r in o["rows"]:
        rows.append([r["keyword"], r["position"], r["previous_position"], r["change"], r["best_position"], r["gsc_position"],
                     r["url"], r["target_url"], r["volume"], r["device"], LOCATIONS.get(r["location_code"], r["location_code"]),
                     r["source"], r["last_checked_at"].isoformat() if r["last_checked_at"] else None])
    return _csv(rows, f"rankings-{o['today']}.csv")
