"""Company Intelligence background work: crawling, profile extraction, competitor discovery and
analysis, the digital presence audit and topic ideas.

Job kinds
  intel.crawl            {source_id, max_pages?, chain?}  crawl a website/competitor source into the knowledge base
  intel.extract          {chain?}                         LLM map-reduce → CompanyProfile + CompanyEntity rows
  intel.competitors      {}                               discover competitors through web search (D17)
  intel.competitor_crawl {competitor_id}                  crawl + analyse one competitor, detect changes
  intel.audit            {}                               digital presence audit → PresenceAudit
  intel.ideas            {}                               topic ideas → Opportunity rows
  intel.run_all          {max_pages?}                     the whole chain in one job (for the "Analyze everything" button)

Periodic: weekly re-crawl of the org website and confirmed competitors; monthly competitor rediscovery.
"""

import asyncio
import logging
import re
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_json
from app.core.db import SessionLocal, set_tenant
from app.models import (
    AnalyticsSnapshot,
    CompanyEntity,
    CompanyProfile,
    Competitor,
    ContentPillar,
    KnowledgeDocument,
    KnowledgeSource,
    Opportunity,
    Organization,
    PresenceAudit,
    ProviderConfig,
    SocialAccount,
)
from app.services import crawler, knowledge, search
from app.services.jobs import (
    JobContext,
    PermanentError,
    enqueue,
    handler,
    org_ids_active,
    periodic,
)
from app.services.knowledge import ENTITY_TYPES, normalize_name
from app.services.llm.router import NoProviderError, complete_json
from app.services.notify import notify_org

log = logging.getLogger(__name__)

NO_AI = "Add an AI text model in Settings → Integrations, then run this again."
NO_WEBSITE = "Add your website in Organization settings (or on the Sources tab), then run this again."
COMPETITOR_MAX_PAGES = 40
BATCH_CHARS = 10_000
MAX_PROMPT_CHARS = 12_000
RECRAWL_DAYS = 7
REDISCOVER_DAYS = 30


def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ coercion of LLM output
def _s(v, n: int = 500) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()[:n]


def _ls(v, items: int = 20, n: int = 200) -> list[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[;\n]|, ", v) if x.strip()]
    if not isinstance(v, list):
        return []
    out: dict[str, str] = {}
    for x in v:
        s = _s(x if not isinstance(x, dict) else (x.get("name") or x.get("title") or ""), n)
        if s and normalize_name(s) not in out:
            out[normalize_name(s)] = s
        if len(out) >= items:
            break
    return list(out.values())


def _f01(v, default: float = 0.5) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f > 1 and f <= 100:
        f = f / 100
    return round(min(1.0, max(0.0, f)), 3)


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


async def _ai_json(session: AsyncSession, task: str, prompt: str, system: str, max_tokens: int = 2500):
    """complete_json with the house rules: no provider → permanent failure; unparsable JSON → None."""
    try:
        return await complete_json(session, task, prompt[:MAX_PROMPT_CHARS + 2000], system=system,
                                   max_tokens=max_tokens, temperature=0.3)
    except NoProviderError:
        raise PermanentError(NO_AI)
    except ValueError as e:
        log.warning("model returned invalid JSON for %s: %s", task, e)
        return None


async def _notify(org_id: uuid.UUID | None, event: str, title: str, body: str | None, link: str = "/intelligence") -> None:
    if not org_id:
        return
    try:
        await notify_org(org_id, event, title, body, link=link, permission="intel.view")
    except Exception:
        log.exception("intel notification failed")


# ------------------------------------------------------------------ sources
async def ensure_website_source(session: AsyncSession, org_id: uuid.UUID) -> KnowledgeSource | None:
    """The org's website source; created from Organization.website when missing."""
    src = (await session.scalars(
        select(KnowledgeSource).where(KnowledgeSource.kind == "website").order_by(KnowledgeSource.created_at).limit(1)
    )).first()
    if src is not None:
        return src
    org = await session.get(Organization, org_id)
    url = crawler.normalize_url(org.website) if org and org.website else None
    if not url:
        return None
    src = KnowledgeSource(organization_id=org_id, kind="website", title=org.name or url, url=url, status="pending")
    session.add(src)
    await session.flush()
    return src


async def competitor_source(session: AsyncSession, comp: Competitor) -> KnowledgeSource:
    src = (await session.scalars(
        select(KnowledgeSource).where(KnowledgeSource.kind == "competitor", KnowledgeSource.competitor_id == comp.id).limit(1)
    )).first()
    if src is None:
        src = KnowledgeSource(organization_id=comp.organization_id, kind="competitor", title=comp.name, url=comp.website,
                              competitor_id=comp.id, status="pending", meta={"max_pages": COMPETITOR_MAX_PAGES})
        session.add(src)
        await session.flush()
    elif src.url != comp.website:
        src.url = comp.website
    return src


def _merge_social(pages_social: list[dict]) -> dict[str, str]:
    """Most common profile URL per platform across pages (the homepage's when tied)."""
    by_platform: dict[str, Counter] = {}
    for social in pages_social:
        for plat, url in (social or {}).items():
            by_platform.setdefault(plat, Counter())[url] += 1
    return {p: c.most_common(1)[0][0] for p, c in by_platform.items()}


async def crawl_source(ctx: JobContext, source: KnowledgeSource, max_pages: int | None) -> dict:
    """Crawl one source into KnowledgeDocument/KnowledgeChunk rows and update its status."""
    s = ctx.session
    source.status, source.status_message = "running", "Crawling…"
    await s.commit()
    try:
        result = await crawler.crawl(source.url or "", max_pages=max_pages, progress=ctx.progress)
    except crawler.UnsafeURLError as e:
        source.status, source.status_message = "error", str(e)
        await s.commit()
        raise PermanentError(str(e))
    if not result.pages:
        if result.blocked_by_robots and not result.errors:
            msg = "The site's robots.txt doesn't allow crawling these pages."
        elif result.errors:
            msg = f"Couldn't read {result.start_url}: {result.errors[0]['error']}"
        else:
            msg = "No readable pages were found. Check the address and that the site is online."
        source.status, source.status_message = "error", msg
        source.meta = {**(source.meta or {}), "crawl": {**result.summary(), "finished_at": now().isoformat()}}
        await s.commit()
        raise PermanentError(msg)

    await ctx.progress(f"Saving {len(result.pages)} pages")
    previous = await s.scalar(select(func.count()).select_from(KnowledgeDocument)
                              .where(KnowledgeDocument.source_id == source.id)) or 0
    new_urls: list[str] = []
    changed_urls: list[str] = []
    seen: set[str] = set()
    for page in result.pages:
        title = page.title or (page.h1[0] if page.h1 else "") or page.url
        text = page.text or "\n".join(x for x in (page.title, page.description, *page.h1) if x)
        if not text.strip():
            continue
        _, state = await knowledge.add_document(s, source, url=page.url, title=title, text=text, meta=page.seo_meta())
        seen.add(page.url)
        if state == "new":
            new_urls.append(page.url)
        elif state == "changed":
            changed_urls.append(page.url)
    # Remove pages that disappeared, unless this crawl looks partial (site down, rate limited, ...).
    removed = 0
    if seen and len(seen) >= 0.5 * previous and not result.truncated:
        res = await s.execute(delete(KnowledgeDocument).where(
            KnowledgeDocument.source_id == source.id, KnowledgeDocument.url.not_in(seen)))
        removed = res.rowcount or 0

    source.page_count = await s.scalar(select(func.count()).select_from(KnowledgeDocument)
                                       .where(KnowledgeDocument.source_id == source.id)) or 0
    source.last_crawled_at = now()
    source.status = "ready"
    source.status_message = (
        f"{source.page_count} pages" + (f", {len(new_urls)} new" if previous and new_urls else "")
        + (f", {len(changed_urls)} changed" if changed_urls else "")
        + (" (page limit reached)" if result.truncated else "")
    )
    source.meta = {
        **(source.meta or {}),
        "crawl": {**result.summary(), "finished_at": now().isoformat(), "new_pages": new_urls[:50],
                  "changed_pages": changed_urls[:50], "removed_pages": removed},
        # The home page counts twice so its links win ties.
        "social_links": _merge_social([p.social for p in result.pages[:1]] + [p.social for p in result.pages]),
    }
    await s.commit()
    # Capture results before the optional embedding step: a rollback expires every loaded row.
    out = {"source_id": str(source.id), "pages": source.page_count, "new": len(new_urls), "changed": len(changed_urls),
           "removed": removed, "embedded": 0, "new_urls": new_urls[:50], "first_crawl": previous == 0}
    try:
        out["embedded"] = await knowledge.embed_pending(s)
        await s.commit()
    except Exception as e:
        log.warning("embedding failed: %s", e)
        await s.rollback()
    return out


@handler("intel.crawl")
async def h_crawl(ctx: JobContext) -> dict:
    s = ctx.session
    try:
        sid = uuid.UUID(str(ctx.payload.get("source_id")))
    except ValueError:
        raise PermanentError("Missing source.")
    source = await s.scalar(select(KnowledgeSource).where(KnowledgeSource.id == sid))
    if source is None:
        raise PermanentError("This source was deleted.")
    if source.kind not in ("website", "competitor") or not source.url:
        raise PermanentError("Only website sources can be crawled.")
    default = COMPETITOR_MAX_PAGES if source.kind == "competitor" else None
    max_pages = ctx.payload.get("max_pages") or (source.meta or {}).get("max_pages") or default
    stats = await crawl_source(ctx, source, int(max_pages) if max_pages else None)

    if ctx.payload.get("chain", True):
        if source.kind == "website":
            has_profile = await s.scalar(select(func.count()).select_from(CompanyProfile)) or 0
            if stats["new"] or stats["changed"] or not has_profile:
                await enqueue(s, "intel.extract", ctx.org_id, {"chain": True}, dedupe_key=f"intel.extract:{ctx.org_id}")
        elif source.competitor_id:
            comp = await s.scalar(select(Competitor).where(Competitor.id == source.competitor_id))
            if comp is not None:
                await analyze_competitor(ctx, comp, source, stats)
    await s.commit()
    return stats


# ------------------------------------------------------------------ profile extraction (map-reduce)
EXTRACT_SYSTEM = """You extract facts about a company from its own web pages and documents.
Use only facts stated in the text. Never invent names, numbers or clients.
Return ONLY a JSON object with exactly this shape:
{"summary": "2-4 sentence description of what the company does",
 "positioning": "one sentence on how it positions itself",
 "industries": ["industry the company serves"],
 "audiences": ["buyer persona or audience, e.g. HR heads at mid-size companies"],
 "differentiators": ["what sets it apart, as stated"],
 "geography": "where it operates / is based, or empty string",
 "entities": [{"type": "product|studio|service|use_case|client|industry|case_study|team",
               "name": "short name", "description": "under 200 characters", "url": "page URL or empty"}]}
Entity types: product = software/platform it sells; studio = a named studio or lab (e.g. "AI Studio");
service = a service offered; use_case = a specific problem solved; client = a customer organisation named as a
client, customer, testimonial or logo; industry = an industry served; case_study = a named success story;
team = a named leader or team member (role in description). Use empty strings or lists when unknown."""

MERGE_SYSTEM = """You merge partial company profiles extracted from different pages into one profile.
Keep only facts present in the input. Remove duplicates. Return ONLY JSON:
{"summary": "", "positioning": "", "industries": [], "audiences": [], "differentiators": [], "geography": ""}"""


def _doc_priority(url: str | None) -> tuple:
    if not url:
        return (2, 0, 0)
    path = urlsplit(url).path.strip("/")
    if not path:
        return (-1, 0, 0)
    return crawler._url_priority(url)


def _batches(docs: list[tuple[str | None, str | None, str]], per_doc: int = 1600, limit: int = 8) -> list[str]:
    batches: list[str] = []
    cur = ""
    for url, title, text in docs:
        block = f"### {title or url or 'Document'}\nURL: {url or '(uploaded document)'}\n{text[:per_doc]}\n\n"
        if cur and len(cur) + len(block) > BATCH_CHARS:
            batches.append(cur)
            cur = ""
            if len(batches) >= limit:
                return batches
        cur += block
    if cur and len(batches) < limit:
        batches.append(cur)
    return batches


def _merge_entities(parts: list[tuple[str, list]]) -> dict[tuple[str, str], dict]:
    """parts: [(source, entities)] → {(type, key): entity}. Website facts win over uploads."""
    merged: dict[tuple[str, str], dict] = {}
    for src, ents in parts:
        for e in ents if isinstance(ents, list) else []:
            if not isinstance(e, dict):
                continue
            etype = _s(e.get("type"), 30).lower().replace(" ", "_").replace("-", "_")
            etype = {"usecase": "use_case", "casestudy": "case_study", "customer": "client", "products": "product",
                     "services": "service", "clients": "client"}.get(etype, etype)
            name = _s(e.get("name"), 300)
            if etype not in ENTITY_TYPES or not name or len(normalize_name(name)) < 2:
                continue
            key = (etype, normalize_name(name))
            desc = _s(e.get("description"), 400)
            url = _s(e.get("url"), 1000)
            url = url if url.startswith(("http://", "https://")) else ""
            cur = merged.get(key)
            if cur is None:
                merged[key] = {"type": etype, "name": name, "description": desc, "url": url, "source": src}
            else:
                if len(desc) > len(cur["description"]):
                    cur["description"] = desc
                cur["url"] = cur["url"] or url
                if src == "website":
                    cur["source"] = "website"
    return merged


def _union(values: list[list[str]], items: int = 12) -> list[str]:
    return _ls([x for v in values for x in v], items)


@handler("intel.extract")
async def h_extract(ctx: JobContext) -> dict:
    s = ctx.session
    rows = (await s.execute(
        select(KnowledgeDocument.url, KnowledgeDocument.title, KnowledgeDocument.text, KnowledgeSource.kind,
               KnowledgeDocument.meta)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(KnowledgeSource.kind.in_(("website", "upload")))
    )).all()
    if not rows:
        raise PermanentError("There are no pages or documents to read yet. " + NO_WEBSITE)
    web = sorted([(u, t, x) for u, t, x, k, _ in rows if k == "website" and x], key=lambda r: _doc_priority(r[0]))
    up = [(u, t, x) for u, t, x, k, _ in rows if k == "upload" and x]
    groups = [("website", b) for b in _batches(web, limit=9)] + [("upload", b) for b in _batches(up, per_doc=4000, limit=3)]

    partials: list[dict] = []
    entity_parts: list[tuple[str, list]] = []
    for i, (src, text) in enumerate(groups, 1):
        await ctx.progress(f"Reading pages ({i} of {len(groups)})")
        data = await _ai_json(s, "extract", f"Pages from the company's {'website' if src == 'website' else 'documents'}:\n\n{text}",
                              EXTRACT_SYSTEM, max_tokens=3000)
        await s.commit()  # keep LLM usage rows even if a later step fails
        if not isinstance(data, dict):
            continue
        partials.append({k: data.get(k) for k in ("summary", "positioning", "industries", "audiences", "differentiators", "geography")})
        entity_parts.append((src, data.get("entities") or []))
    if not partials:
        raise PermanentError("The AI model didn't return usable results. Try a larger model in Settings → Integrations.")

    if len(partials) > 1:
        await ctx.progress("Combining what was found")
        prompt = "Partial profiles:\n" + "\n".join(
            f"{i}. " + str({k: v for k, v in p.items() if v}) for i, p in enumerate(partials, 1))
        merged = await _ai_json(s, "extract", prompt[:MAX_PROMPT_CHARS], MERGE_SYSTEM, max_tokens=1500)
        merged = merged if isinstance(merged, dict) else {}
    else:
        merged = partials[0]
    fallback = {
        "summary": max((_s(p.get("summary"), 2000) for p in partials), key=len, default=""),
        "positioning": next((_s(p.get("positioning"), 1000) for p in partials if _s(p.get("positioning"))), ""),
        "geography": next((_s(p.get("geography"), 300) for p in partials if _s(p.get("geography"))), ""),
    }
    final = {
        "summary": _s(merged.get("summary"), 2000) or fallback["summary"],
        "positioning": _s(merged.get("positioning"), 1000) or fallback["positioning"],
        "geography": _s(merged.get("geography"), 300) or fallback["geography"],
        "industries": _ls(merged.get("industries"), 15) or _union([_ls(p.get("industries")) for p in partials], 15),
        "audiences": _ls(merged.get("audiences"), 12) or _union([_ls(p.get("audiences")) for p in partials]),
        "differentiators": _ls(merged.get("differentiators"), 10, 300)
        or _union([_ls(p.get("differentiators"), 10, 300) for p in partials], 10),
    }

    # ---- profile (never overwrite fields a person edited)
    profile = await s.scalar(select(CompanyProfile).limit(1))
    if profile is None:
        profile = CompanyProfile(organization_id=ctx.org_id, industries=[], audiences=[], differentiators=[],
                                 social_links={}, data={})
        s.add(profile)
    edited = set((profile.data or {}).get("edited_fields") or [])
    for field, value in final.items():
        if field not in edited and value:
            setattr(profile, field, value)
    site_social: dict[str, str] = {}
    for src in (await s.scalars(select(KnowledgeSource).where(KnowledgeSource.kind == "website"))).all():
        site_social.update(_d((src.meta or {}).get("social_links")))
    if "social_links" in edited:
        profile.social_links = {**site_social, **(profile.social_links or {})}
    else:
        profile.social_links = {**(profile.social_links or {}), **site_social}
    profile.data = {**(profile.data or {}), "extracted_at": now().isoformat(), "pages_read": len(web) + len(up)}

    # ---- entities: upsert by (type, normalized name); skip what people confirmed or edited
    found = _merge_entities(entity_parts)
    existing = {(e.type, normalize_name(e.name)): e for e in (await s.scalars(select(CompanyEntity))).all()}
    added = updated = 0
    per_type: Counter = Counter()
    for key, ent in found.items():
        if per_type[key[0]] >= 60:
            continue
        per_type[key[0]] += 1
        ex = existing.get(key)
        if ex is None:
            s.add(CompanyEntity(
                organization_id=ctx.org_id, type=ent["type"], name=ent["name"], description=ent["description"] or None,
                url=ent["url"] or None, source=ent["source"], confirmed=False,
                public_mention_ok=ent["type"] == "client" and ent["source"] == "website",  # D20
                data={"extracted_at": now().isoformat()},
            ))
            added += 1
            continue
        if ex.confirmed or (ex.data or {}).get("edited"):
            continue
        if ent["description"] and len(ent["description"]) > len(ex.description or ""):
            ex.description = ent["description"]
        ex.url = ex.url or ent["url"] or None
        if ent["source"] == "website" and ex.source != "website" and ex.source != "manual":
            ex.source = "website"
            if ex.type == "client":
                ex.public_mention_ok = True
        updated += 1
    await s.commit()

    if ctx.payload.get("chain", True):
        await enqueue(s, "intel.audit", ctx.org_id, {}, dedupe_key=f"intel.audit:{ctx.org_id}")
        await enqueue(s, "intel.ideas", ctx.org_id, {}, dedupe_key=f"intel.ideas:{ctx.org_id}")
        await s.commit()
        await _notify(ctx.org_id, "intel_ready", "Your company profile is ready to review",
                      f"Read {len(web) + len(up)} pages and found {len(found)} products, services, clients and other facts.")
    return {"pages_read": len(web) + len(up), "entities_found": len(found), "added": added, "updated": updated}


# ------------------------------------------------------------------ competitor discovery (D17)
EXCLUDED_DOMAINS = {
    # social networks and video
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "youtu.be", "pinterest.com",
    "reddit.com", "quora.com", "medium.com", "tiktok.com", "threads.net", "t.me", "whatsapp.com", "slideshare.net",
    "scribd.com", "substack.com", "blogspot.com", "wordpress.com", "github.com", "stackoverflow.com",
    # encyclopaedias, search engines, big platforms
    "wikipedia.org", "wikimedia.org", "wikihow.com", "britannica.com", "google.com", "bing.com", "duckduckgo.com",
    "yahoo.com", "apple.com", "microsoft.com", "play.google.com",
    # marketplaces and app stores
    "amazon.com", "amazon.in", "flipkart.com", "indiamart.com", "alibaba.com", "ebay.com", "tradeindia.com",
    # review sites and directories
    "g2.com", "capterra.com", "capterra.in", "getapp.com", "softwaresuggest.com", "trustradius.com",
    "softwareadvice.com", "gartner.com", "crunchbase.com", "zoominfo.com", "clutch.co", "goodfirms.co", "tracxn.com",
    "owler.com", "f6s.com", "producthunt.com", "saasworthy.com", "techjockey.com", "softwareworld.co", "selecthub.com",
    "justdial.com", "sulekha.com", "yelp.com", "yellowpages.com", "trustpilot.com", "glassdoor.com", "glassdoor.co.in",
    "ambitionbox.com", "comparably.com", "dnb.com", "zaubacorp.com", "tofler.in", "economictimes.com",
    # job boards
    "indeed.com", "naukri.com", "monster.com", "shine.com", "foundit.in", "instahyre.com", "internshala.com",
    "wellfound.com", "angel.co", "iimjobs.com", "hirist.com",
    # news and media
    "indiatimes.com", "thehindu.com", "livemint.com", "moneycontrol.com", "business-standard.com", "forbes.com",
    "forbesindia.com", "techcrunch.com", "yourstory.com", "inc42.com", "bbc.com", "bbc.co.uk", "cnn.com",
    "reuters.com", "bloomberg.com", "nytimes.com", "hindustantimes.com", "ndtv.com", "financialexpress.com",
    "businessinsider.com", "businessinsider.in", "entrackr.com", "techcircle.in", "analyticsindiamag.com",
    "theverge.com", "wired.com", "zdnet.com", "venturebeat.com", "prnewswire.com", "businesswire.com",
    "globenewswire.com", "outlookindia.com", "indianexpress.com", "deccanherald.com", "thequint.com",
    # learning platforms
    "coursera.org", "udemy.com", "edx.org", "researchgate.net", "academia.edu", "springer.com", "sciencedirect.com",
}
_EXCLUDED_TOKENS = ("news", "jobs", "wiki", "careers", "forum")
_EXCLUDED_SUFFIXES = (".gov", ".gov.in", ".nic.in", ".edu", ".edu.in", ".ac.in", ".ac.uk", ".mil", ".int")


def is_excluded_domain(domain: str, own_domains: set[str]) -> bool:
    d = (domain or "").lower()
    if not d or "." not in d or d in own_domains or d in EXCLUDED_DOMAINS:
        return True
    if d.endswith(_EXCLUDED_SUFFIXES):
        return True
    first = d.split(".")[0]
    return any(tok in first for tok in _EXCLUDED_TOKENS)


QUERIES_SYSTEM = """You plan web searches that find a company's direct competitors.
Combine the company's offerings with its industries and geography, the way a buyer would search,
e.g. "AI HRMS software India", "payroll software for manufacturing companies".
Return ONLY JSON: {"queries": ["6 to 10 distinct search queries"]}"""

SCORE_SYSTEM = """You judge whether websites belong to direct competitors of a company.
Score overlap from 0 (unrelated) to 1 (sells the same things to the same buyers). Directories, news,
marketplaces, agencies listing many vendors and the company's own sites score 0.
Return ONLY JSON: {"candidates": [{"domain": "", "name": "company or brand name", "overlap": 0.0,
"reason": "one sentence", "offerings": ["main offerings"], "positioning": "one sentence"}]}"""


def _origin(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme or "https", p.netloc, "/", "", ""))


async def _own_domains(session: AsyncSession, org_id: uuid.UUID) -> set[str]:
    own: set[str] = set()
    org = await session.get(Organization, org_id)
    if org and org.website:
        own.add(crawler.domain_of(crawler.normalize_url(org.website) or ""))
    for src in (await session.scalars(select(KnowledgeSource).where(KnowledgeSource.kind == "website"))).all():
        if src.url:
            own.add(crawler.domain_of(src.url))
    return {d for d in own if d}


@handler("intel.competitors")
async def h_competitors(ctx: JobContext) -> dict:
    s = ctx.session
    profile = await s.scalar(select(CompanyProfile).limit(1))
    entities = (await s.scalars(select(CompanyEntity).where(
        CompanyEntity.type.in_(("product", "studio", "service", "industry"))))).all()
    if profile is None or not (profile.summary or entities):
        raise PermanentError("Analyze your website first so there is a company profile to compare against.")
    offerings = [e.name for e in entities if e.type in ("product", "studio", "service")][:12]
    industries = list(dict.fromkeys([*(profile.industries or []), *[e.name for e in entities if e.type == "industry"]]))[:10]
    geo = profile.geography or ""

    await ctx.progress("Planning searches")
    prompt = (f"Company summary: {profile.summary or ''}\nPositioning: {profile.positioning or ''}\n"
              f"Offerings: {', '.join(offerings)}\nIndustries: {', '.join(map(str, industries))}\nGeography: {geo}")
    data = await _ai_json(s, "plan", prompt, QUERIES_SYSTEM, max_tokens=600)
    queries = _ls(_d(data).get("queries") if isinstance(data, dict) else data, 10, 120)
    if len(queries) < 3:  # fallback: offerings × geography
        region = geo.split(",")[0].strip() if geo else ""
        queries = list(dict.fromkeys(queries + [f"{o} {region}".strip() for o in offerings[:6]]
                                     + [f"{o} software" for o in offerings[:3]]))[:10]
    if not queries:
        raise PermanentError("Add products or services to the company profile so competitors can be searched for.")

    own = await _own_domains(s, ctx.org_id)
    known = {c.domain for c in (await s.scalars(select(Competitor))).all()}  # rejected ones stay rejected
    hits: dict[str, dict] = {}
    failures: list[str] = []
    for i, q in enumerate(queries, 1):
        await ctx.progress(f"Searching the web ({i} of {len(queries)})")
        try:
            results = await search.web_search(s, q, n=10)
        except search.SearchError as e:
            failures.append(str(e))
            continue
        for rank, r in enumerate(results):
            dom = crawler.domain_of(r["url"])
            if is_excluded_domain(dom, own):
                continue
            h = hits.setdefault(dom, {"domain": dom, "count": 0, "best_rank": 99, "url": r["url"], "snippets": [], "queries": []})
            h["count"] += 1
            h["best_rank"] = min(h["best_rank"], rank)
            if len(h["snippets"]) < 3:
                h["snippets"].append(f"{r['title']}: {r['snippet']}")
            if q not in h["queries"]:
                h["queries"].append(q)
    if failures and len(failures) == len(queries):
        raise PermanentError("Web search isn't working: " + failures[0][:300]
                             + " Add a search provider in Settings → Integrations.")

    candidates = sorted((h for d, h in hits.items() if d not in known), key=lambda h: (-h["count"], h["best_rank"]))[:15]
    if not candidates:
        await s.commit()
        return {"queries": queries, "candidates": 0, "suggested": 0}

    sem = asyncio.Semaphore(4)

    async def describe(h: dict) -> dict:
        async with sem:
            home = await crawler.fetch_page(_origin(h["url"]))
            text = ""
            name = ""
            if home is not None:
                name = home.og.get("og:site_name") or home.title
                text = f"Home page — {home.title}\n{home.description}\n{home.text[:1300]}"
                about_url = crawler.find_about_link(home)
                if about_url:
                    about = await crawler.fetch_page(about_url)
                    if about is not None:
                        text += f"\nAbout page:\n{about.text[:700]}"
            h["name_hint"] = name
            h["text"] = text or "Search snippets only:\n" + "\n".join(h["snippets"])
            return h

    await ctx.progress(f"Reading {len(candidates)} candidate websites")
    described = await asyncio.gather(*(describe(h) for h in candidates))

    me = f"Our company: {profile.summary or ''}\nOfferings: {', '.join(offerings)}\nIndustries: {', '.join(map(str, industries))}\nGeography: {geo}\n"
    scored: dict[str, dict] = {}
    for i in range(0, len(described), 5):
        await ctx.progress(f"Scoring candidates ({min(i + 5, len(described))} of {len(described)})")
        block = "\n\n".join(f"## Candidate domain: {h['domain']}\n{h['text'][:1900]}" for h in described[i:i + 5])
        res = await _ai_json(s, "extract", me + "\n" + block, SCORE_SYSTEM, max_tokens=1500)
        for c in _d(res).get("candidates") or (res if isinstance(res, list) else []):
            if not isinstance(c, dict):
                continue
            dom = crawler.registrable_domain(_s(c.get("domain"), 253).lower().removeprefix("https://").removeprefix("http://").split("/")[0])
            if dom in hits:
                scored[dom] = c
        await s.commit()

    saved = 0
    ranked = sorted(scored.items(), key=lambda kv: -_f01(kv[1].get("overlap"), 0))
    for dom, c in ranked:
        overlap = _f01(c.get("overlap"), 0)
        if overlap < 0.3 or saved >= 10 or dom in known:
            continue
        h = hits[dom]
        s.add(Competitor(
            organization_id=ctx.org_id, name=_s(c.get("name"), 300) or h.get("name_hint") or dom, website=_origin(h["url"]),
            domain=dom, status="suggested", overlap_score=overlap, reason=_s(c.get("reason"), 600) or None,
            data={"offerings": _ls(c.get("offerings"), 12), "positioning": _s(c.get("positioning"), 500),
                  "discovered_at": now().isoformat(), "queries": h["queries"][:5], "origin": "auto"},
        ))
        known.add(dom)
        saved += 1
    profile.data = {**(profile.data or {}), "competitors_discovered_at": now().isoformat()}
    await s.commit()
    if saved:
        await _notify(ctx.org_id, "intel_ready", f"{saved} possible competitors found",
                      "Review them on the Competitors tab and confirm the ones that matter.")
    return {"queries": queries, "candidates": len(candidates), "suggested": saved}


# ------------------------------------------------------------------ competitor crawl + analysis
COMPETITOR_SYSTEM = """You analyse a competitor's public website for a marketing team.
Use only what the pages say. Return ONLY JSON:
{"offerings": ["products/services they sell"], "positioning": "one or two sentences",
 "pricing_signals": ["pricing facts or models mentioned, e.g. 'per employee per month', 'free trial'"],
 "content_themes": ["recurring themes in their content"], "blog_topics": ["specific blog/article topics"],
 "audiences": ["who they sell to"], "industries": ["industries they target"], "keywords": ["search keywords they target"]}"""

_STOP = frozenset(["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "how", "in", "into", "is", "it", "its", "of", "on", "or", "our", "that", "the", "their", "this", "to", "we", "what", "when", "where", "which", "who", "why", "with", "you", "your", "about", "more", "home", "page", "blog", "new", "best", "top", "vs", "get", "use", "using", "all", "can", "will"])


def _title_keywords(titles: list[str], n: int = 20) -> list[str]:
    words: Counter = Counter()
    for t in titles:
        for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+\-]{2,}", t.lower()):
            if w not in _STOP:
                words[w] += 1
    return [w for w, c in words.most_common(n) if c > 1] or [w for w, _ in words.most_common(min(n, 10))]


async def analyze_competitor(ctx: JobContext, comp: Competitor, source: KnowledgeSource, stats: dict) -> dict:
    s = ctx.session
    await ctx.progress(f"Analysing {comp.name}")
    docs = (await s.execute(select(KnowledgeDocument.url, KnowledgeDocument.title, KnowledgeDocument.text)
                            .where(KnowledgeDocument.source_id == source.id))).all()
    docs = sorted(docs, key=lambda r: _doc_priority(r[0]))
    titles = [t for _, t, _ in docs if t]
    page_list = "\n".join(f"- {t} ({u})" for u, t, _ in docs[:80])
    excerpts = "".join(f"\n### {t}\n{x[:1200]}\n" for _, t, x in docs[:12])
    prompt = f"Competitor: {comp.name} ({comp.website})\nPages:\n{page_list}\n\nExcerpts:{excerpts}"[:MAX_PROMPT_CHARS]

    keywords = _title_keywords(titles)
    data: dict = dict(comp.data or {})
    data.update({"title_keywords": keywords, "page_count": len(docs), "crawled_at": now().isoformat()})
    try:
        res = _d(await _ai_json(s, "extract", prompt, COMPETITOR_SYSTEM, max_tokens=1800))
    except PermanentError:
        comp.data, comp.last_crawled_at = data, now()
        await s.commit()
        raise
    analysis = {
        "offerings": _ls(res.get("offerings"), 20), "positioning": _s(res.get("positioning"), 600),
        "pricing_signals": _ls(res.get("pricing_signals"), 10, 300), "content_themes": _ls(res.get("content_themes"), 15),
        "blog_topics": _ls(res.get("blog_topics"), 25, 300), "audiences": _ls(res.get("audiences"), 10),
        "industries": _ls(res.get("industries"), 12), "keywords": _ls(res.get("keywords"), 25),
    }
    for k, v in analysis.items():
        if v:
            data[k] = v

    prev = _d(data.get("snapshot"))
    change: dict = {}
    if prev:
        before = {normalize_name(o) for o in prev.get("offerings") or []}
        new_off = [o for o in analysis["offerings"] if normalize_name(o) not in before]
        new_pages = stats.get("new_urls") or []
        if new_off or new_pages:
            change = {"at": now().isoformat(), "new_offerings": new_off[:10], "new_pages": new_pages[:15]}
            data["changes"] = [change, *(data.get("changes") or [])][:20]
    data["snapshot"] = {"offerings": analysis["offerings"] or prev.get("offerings") or [], "page_count": len(docs),
                        "at": now().isoformat()}
    data["analyzed_at"] = now().isoformat()
    comp.data = data
    comp.last_crawled_at = now()
    await s.commit()
    if change:
        parts = []
        if change["new_offerings"]:
            parts.append("New offerings: " + ", ".join(change["new_offerings"][:5]))
        if change["new_pages"]:
            parts.append(f"{len(change['new_pages'])} new pages, e.g. {change['new_pages'][0]}")
        await _notify(ctx.org_id, "competitor_change", f"{comp.name} changed their website", ". ".join(parts))
    return {"competitor_id": str(comp.id), "pages": len(docs), "changed": bool(change)}


@handler("intel.competitor_crawl")
async def h_competitor_crawl(ctx: JobContext) -> dict:
    s = ctx.session
    try:
        cid = uuid.UUID(str(ctx.payload.get("competitor_id")))
    except ValueError:
        raise PermanentError("Missing competitor.")
    comp = await s.scalar(select(Competitor).where(Competitor.id == cid))
    if comp is None:
        raise PermanentError("This competitor was deleted.")
    if comp.status == "rejected":
        raise PermanentError("This competitor was rejected. Confirm it before crawling.")
    source = await competitor_source(s, comp)
    await s.commit()
    stats = await crawl_source(ctx, source, int(ctx.payload.get("max_pages") or COMPETITOR_MAX_PAGES))
    return {**stats, **(await analyze_competitor(ctx, comp, source, stats))}


# ------------------------------------------------------------------ digital presence audit
PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


async def fetch_pagespeed(url: str, key: str | None) -> dict:
    """Mobile PageSpeed Insights summary. Returns {"error": ...} instead of raising."""
    params: list[tuple[str, str]] = [("url", url), ("strategy", "mobile")]
    params += [("category", c) for c in ("performance", "accessibility", "best-practices", "seo")]
    if key:
        params.append(("key", key))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0)) as c:
            r = await c.get(PAGESPEED_URL, params=params)
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                pass
            hint = " Add a PageSpeed API key to the Google integration for reliable results." if r.status_code == 429 else ""
            return {"error": f"PageSpeed Insights returned {r.status_code}. {msg[:200]}{hint}".strip()}
        lh = r.json().get("lighthouseResult") or {}
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"PageSpeed Insights couldn't be reached ({type(e).__name__})."}
    cats = lh.get("categories") or {}
    audits = lh.get("audits") or {}

    def cat(k: str) -> int | None:
        sc = (cats.get(k) or {}).get("score")
        return round(sc * 100) if isinstance(sc, (int, float)) else None

    def num(k: str) -> float | None:
        v = (audits.get(k) or {}).get("numericValue")
        return round(v, 3) if isinstance(v, (int, float)) else None

    return {
        "performance": cat("performance"), "accessibility": cat("accessibility"),
        "best_practices": cat("best-practices"), "seo": cat("seo"),
        "lcp_ms": num("largest-contentful-paint"), "cls": num("cumulative-layout-shift"),
        "tbt_ms": num("total-blocking-time"), "fcp_ms": num("first-contentful-paint"), "speed_index_ms": num("speed-index"),
    }


async def _pagespeed_key(s: AsyncSession) -> str | None:
    cfg = await s.scalar(select(ProviderConfig).where(ProviderConfig.provider == "google_app", ProviderConfig.enabled.is_(True)))
    return (decrypt_json(cfg.secrets_enc).get("pagespeed_key") or None) if cfg else None


_BLOG = re.compile(r"/(blog|blogs|news|insights|articles|resources|posts?|stories|updates)(/|$)", re.IGNORECASE)
SOCIAL_LABELS = {"linkedin": "LinkedIn", "facebook": "Facebook", "instagram": "Instagram", "x": "X", "youtube": "YouTube",
                 "pinterest": "Pinterest"}


def _platform_family(p: str) -> str:
    p = (p or "").lower()
    for fam in ("linkedin", "facebook", "instagram", "pinterest", "youtube", "threads", "gbp", "google"):
        if p.startswith(fam):
            return "gbp" if fam == "google" else fam
    return "x" if p in ("x", "twitter") else p


def _ratio(good: int, total: int) -> float:
    return good / total if total else 1.0


def _technical(pages: list[dict], crawl: dict) -> tuple[dict, list[dict]]:
    n = len(pages)
    titles = [(p.get("title") or "").strip() for p in pages]
    title_counts = Counter(t.lower() for t in titles if t)
    missing_title = sum(1 for t in titles if not t)
    bad_title_len = sum(1 for t in titles if t and not 30 <= len(t) <= 60)
    dup_titles = sum(c for c in title_counts.values() if c > 1)
    descs = [(p.get("description") or "").strip() for p in pages]
    missing_desc = sum(1 for d in descs if not d)
    bad_desc_len = sum(1 for d in descs if d and not 70 <= len(d) <= 160)
    no_h1 = sum(1 for p in pages if not p.get("h1"))
    multi_h1 = sum(1 for p in pages if len(p.get("h1") or []) > 1)
    no_canonical = sum(1 for p in pages if not p.get("canonical"))
    no_og = sum(1 for p in pages if not (p.get("og") or {}).get("og:title") or not (p.get("og") or {}).get("og:image"))
    with_jsonld = sum(1 for p in pages if p.get("jsonld_types"))
    org_schema = any(t in ("Organization", "Corporation", "LocalBusiness", "ProfessionalService")
                     for p in pages for t in p.get("jsonld_types") or [])
    imgs = sum(int(p.get("images_total") or 0) for p in pages)
    no_alt = sum(int(p.get("images_missing_alt") or 0) for p in pages)
    thin = sum(1 for p in pages if int(p.get("text_chars") or 0) < 300)
    broken = crawl.get("broken_links") or []
    https, sitemap, robots = bool(crawl.get("https")), bool(crawl.get("sitemap_found")), bool(crawl.get("robots_found"))

    weights = [
        (15, _ratio(n - missing_title - bad_title_len, n)), (15, _ratio(n - missing_desc - bad_desc_len, n)),
        (10, _ratio(n - no_h1 - multi_h1, n)), (10, _ratio(n - no_canonical, n)), (10, _ratio(n - no_og, n)),
        (10, 1.0 if with_jsonld else 0.0), (10, _ratio(imgs - no_alt, imgs)), (10, 1.0 if https else 0.0),
        (5, 1.0 if sitemap else 0.0), (5, 1.0 if robots else 0.0),
    ]
    score = sum(w * max(0.0, r) for w, r in weights) - min(10, 2 * len(broken))
    section = {
        "score": max(0, min(100, round(score))), "pages": n, "https": https, "sitemap": sitemap, "robots_txt": robots,
        "missing_title": missing_title, "title_length_issues": bad_title_len, "duplicate_titles": dup_titles,
        "missing_description": missing_desc, "description_length_issues": bad_desc_len,
        "pages_without_h1": no_h1, "pages_with_multiple_h1": multi_h1, "pages_without_canonical": no_canonical,
        "pages_without_og": no_og, "pages_with_jsonld": with_jsonld, "organization_schema": org_schema,
        "images_total": imgs, "images_missing_alt": no_alt, "thin_pages": thin,
        "broken_links": broken[:10], "broken_link_count": len(broken),
    }
    recs: list[dict] = []
    if not https:
        recs.append({"title": "Serve the website over HTTPS", "impact": "high",
                     "detail": "Browsers flag plain HTTP sites as not secure and search engines rank them lower."})
    if broken:
        recs.append({"title": f"Fix {len(broken)} broken internal links", "impact": "high",
                     "detail": "Links to missing pages, e.g. " + ", ".join(b["url"] for b in broken[:3]) + "."})
    if missing_desc or bad_desc_len:
        recs.append({"title": f"Write meta descriptions for {missing_desc + bad_desc_len} pages", "impact": "high" if missing_desc > n / 3 else "medium",
                     "detail": "Aim for 70–160 characters that say what the page offers; search results show this text."})
    if missing_title or bad_title_len or dup_titles:
        recs.append({"title": "Improve page titles", "impact": "high" if missing_title or dup_titles > n / 3 else "medium",
                     "detail": f"{missing_title} missing, {bad_title_len} outside 30–60 characters, {dup_titles} duplicated. "
                               "Give every page a unique, descriptive title."})
    if not sitemap:
        recs.append({"title": "Publish a sitemap.xml", "impact": "medium",
                     "detail": "A sitemap helps search engines find every page. Most CMSs can generate one."})
    if not with_jsonld or not org_schema:
        recs.append({"title": "Add Organization structured data (JSON-LD)", "impact": "medium",
                     "detail": "Structured data with your name, logo and social profiles helps search engines show rich results."})
    if no_h1 or multi_h1:
        recs.append({"title": "Use exactly one H1 heading per page", "impact": "medium",
                     "detail": f"{no_h1} pages have no H1 and {multi_h1} have more than one."})
    if no_og:
        recs.append({"title": f"Add Open Graph tags to {no_og} pages", "impact": "medium",
                     "detail": "og:title and og:image control how links look when shared on LinkedIn, Facebook and WhatsApp."})
    if no_alt:
        recs.append({"title": f"Add alt text to {no_alt} images", "impact": "low" if no_alt < 10 else "medium",
                     "detail": "Alt text makes images accessible and helps them appear in image search."})
    if no_canonical > n / 2:
        recs.append({"title": "Add canonical tags", "impact": "low",
                     "detail": "Canonical links tell search engines which URL is the main version of a page."})
    if not robots:
        recs.append({"title": "Add a robots.txt file", "impact": "low",
                     "detail": "Point it to your sitemap so crawlers find your pages quickly."})
    if thin > n / 3 and n > 3:
        recs.append({"title": f"Expand {thin} thin pages", "impact": "medium",
                     "detail": "Pages with under 300 characters of text rarely rank. Add useful detail or merge them."})
    return section, recs


def _content(pages: list[tuple[str | None, dict]]) -> tuple[dict, list[dict]]:
    today = datetime.now(UTC).date()
    blog_pages = [u for u, _ in pages if u and _BLOG.search(urlsplit(u).path)]
    dates: list[date] = []
    for _, m in pages:
        for d in m.get("dates") or []:
            try:
                dt = date.fromisoformat(str(d)[:10])
            except ValueError:
                continue
            if dt <= today + timedelta(days=1):
                dates.append(dt)
    latest = max(dates) if dates else None
    recent = sum(1 for d in dates if (today - d).days <= 90)
    if latest is None:
        score = 30 if blog_pages else 20
    else:
        age = (today - latest).days
        score = 100 if age <= 30 else 75 if age <= 90 else 50 if age <= 180 else 25 if age <= 365 else 10
    section = {"score": score, "blog_pages": len(blog_pages), "latest_date": latest.isoformat() if latest else None,
               "dated_items_last_90_days": recent, "blog_examples": blog_pages[:5]}
    recs: list[dict] = []
    if not blog_pages:
        recs.append({"title": "Start a blog or insights section", "impact": "high",
                     "detail": "Regular articles give search engines and social posts something to link to. The SEO module can draft them."})
    elif latest is None or (today - latest).days > 90:
        recs.append({"title": "Publish fresh content", "impact": "high" if latest is None or (today - latest).days > 180 else "medium",
                     "detail": "No new dated content in the last 90 days. Aim for at least two articles a month."})
    return section, recs


async def _social(s: AsyncSession, site_social: dict[str, str]) -> tuple[dict, list[dict]]:
    accounts = (await s.scalars(select(SocialAccount))).all()
    connected: dict[str, list[dict]] = {}
    for a in accounts:
        connected.setdefault(_platform_family(a.platform), []).append(
            {"name": a.name, "status": a.status, "profile_url": a.profile_url})
    linked = {_platform_family(k): v for k, v in site_social.items()}
    core = ["linkedin", "facebook", "instagram", "x", "youtube", "pinterest"]
    rows = []
    for p in core:
        rows.append({"platform": p, "label": SOCIAL_LABELS.get(p, p), "linked_from_website": linked.get(p),
                     "connected": bool(connected.get(p)), "accounts": connected.get(p, [])})
    both = [r["platform"] for r in rows if r["linked_from_website"] and r["connected"]]
    union = [r["platform"] for r in rows if r["linked_from_website"] or r["connected"]]
    healthy = sum(1 for p, accs in connected.items() if any(a["status"] == "connected" for a in accs))
    score = round(40 * min(1, len(linked) / 4) + 40 * min(1, healthy / 3) + 20 * (len(both) / len(union) if union else 0))
    recs: list[dict] = []
    not_linked = [SOCIAL_LABELS.get(r["platform"], r["platform"]) for r in rows if r["connected"] and not r["linked_from_website"]]
    not_connected = [SOCIAL_LABELS.get(r["platform"], r["platform"]) for r in rows if r["linked_from_website"] and not r["connected"]]
    if not linked:
        recs.append({"title": "Link your social profiles from the website", "impact": "high",
                     "detail": "No social profile links were found on the website. Add them to the header or footer."})
    elif not_linked:
        recs.append({"title": "Link every active social profile from the website", "impact": "medium",
                     "detail": "Connected here but not linked on the site: " + ", ".join(not_linked) + "."})
    if not_connected:
        recs.append({"title": "Connect your social accounts", "impact": "high" if not connected else "medium",
                     "detail": "Linked on the website but not connected for publishing: " + ", ".join(not_connected)
                               + ". Connect them on the Social accounts page."})
    broken = [a["name"] for accs in connected.values() for a in accs if a["status"] in ("expired", "error")]
    if broken:
        recs.append({"title": "Reconnect accounts that stopped working", "impact": "high",
                     "detail": ", ".join(broken) + " need to be reconnected before posts can be published."})
    if "linkedin" not in linked and "linkedin" not in connected:
        recs.append({"title": "Set up a LinkedIn company page", "impact": "high",
                     "detail": "LinkedIn is the main B2B channel. Create a company page and link it from the website."})
    return {"score": score, "platforms": rows, "linked_count": len(linked), "connected_count": len(connected)}, recs


@handler("intel.audit")
async def h_audit(ctx: JobContext) -> dict:
    s = ctx.session
    source = (await s.scalars(select(KnowledgeSource).where(KnowledgeSource.kind == "website", KnowledgeSource.status == "ready")
                              .order_by(KnowledgeSource.last_crawled_at.desc().nulls_last()).limit(1))).first()
    if source is None:
        raise PermanentError("Crawl your website first. " + NO_WEBSITE)
    docs = (await s.execute(select(KnowledgeDocument.url, KnowledgeDocument.meta)
                            .where(KnowledgeDocument.source_id == source.id))).all()
    if not docs:
        raise PermanentError("The website crawl found no pages. Re-crawl it on the Sources tab.")
    await ctx.progress("Checking technical SEO")
    crawl = _d((source.meta or {}).get("crawl"))
    metas = [_d(m) for _, m in docs]
    technical, recs = _technical(metas, crawl)
    content, rc = _content([(u, _d(m)) for u, m in docs])
    recs += rc
    profile = await s.scalar(select(CompanyProfile).limit(1))
    site_social = {**_d((source.meta or {}).get("social_links")), **(_d(profile.social_links) if profile else {})}
    social, rs = await _social(s, site_social)
    recs += rs

    await ctx.progress("Measuring page speed (PageSpeed Insights)")
    home = next((u for u, _ in docs if u and urlsplit(u).path.strip("/") == ""), source.url)
    perf = await fetch_pagespeed(home or source.url or "", await _pagespeed_key(s))
    performance: dict = {**perf, "url": home}
    if perf.get("performance") is not None:
        performance["score"] = perf["performance"]
        if perf["performance"] < 50:
            recs.append({"title": "Speed up the mobile home page", "impact": "high",
                         "detail": f"Mobile performance score is {perf['performance']}/100. Compress images, defer scripts and use caching."})
        elif perf["performance"] < 80:
            recs.append({"title": "Improve mobile page speed", "impact": "medium",
                         "detail": f"Mobile performance score is {perf['performance']}/100. Largest contentful paint: "
                                   f"{round((perf.get('lcp_ms') or 0) / 1000, 1)} s (aim for under 2.5 s)."})

    parts = [(technical["score"], 0.4), (social["score"], 0.2), (content["score"], 0.2)]
    if performance.get("score") is not None:
        parts.append((performance["score"], 0.2))
    score = round(sum(v * w for v, w in parts) / sum(w for _, w in parts))
    order = {"high": 0, "medium": 1, "low": 2}
    recs = sorted(recs, key=lambda r: order.get(r["impact"], 3))[:20]
    audit = PresenceAudit(organization_id=ctx.org_id, score=max(0, min(100, score)), data={
        "website": source.url, "pages_analyzed": len(docs), "crawled_at": source.last_crawled_at.isoformat() if source.last_crawled_at else None,
        "sections": {"technical": technical, "performance": performance, "social": social, "content": content},
        "recommendations": recs,
    })
    s.add(audit)
    await s.commit()
    return {"audit_id": str(audit.id), "score": audit.score, "recommendations": len(recs)}


# ------------------------------------------------------------------ topic ideas
IDEAS_SYSTEM = """You are a B2B content strategist. Propose specific, non-generic social post and article topics
grounded in the company facts provided. Prefer topics that fill gaps (themes competitors cover that the company
does not), answer real search queries, and showcase the company's own products, use cases and clients.
Never invent client names or statistics. Return ONLY JSON:
{"ideas": [{"title": "specific topic, max 120 characters", "angle": "the hook or point of view in one sentence",
 "source": "idea|gap|gsc|competitor", "score": 0.0, "pillar": "exact pillar name or empty",
 "keywords": ["2-5 keywords"], "formats": ["text|image|carousel|video|article|poll"]}]}
score = relevance × search demand × gap, from 0 to 1. Use source "gsc" only for ideas based on the search queries given,
"gap" for themes competitors cover that we don't, "competitor" for reacting to a competitor's positioning."""


def _gsc_queries(snapshot: AnalyticsSnapshot | None) -> list[dict]:
    if snapshot is None:
        return []
    data = _d(snapshot.data)
    rows = data.get("queries") or data.get("rows") or data.get("top_queries") or []
    out: list[dict] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        q = r.get("query")
        if not q and isinstance(r.get("keys"), list) and r["keys"]:
            q = r["keys"][0]
        if not q:
            continue
        out.append({"query": str(q)[:150], "impressions": r.get("impressions") or 0, "clicks": r.get("clicks") or 0,
                    "position": r.get("position")})
    out.sort(key=lambda r: -(float(r["impressions"]) if isinstance(r["impressions"], (int, float)) else 0))
    return out[:25]


@handler("intel.ideas")
async def h_ideas(ctx: JobContext) -> dict:
    s = ctx.session
    context = await knowledge.company_context(s, max_chars=3000)
    if len(context) < 80:
        raise PermanentError("Analyze your website first so ideas can be based on your company profile.")
    pillars = (await s.scalars(select(ContentPillar).where(ContentPillar.active.is_(True)))).all()
    pillar_by_name = {normalize_name(p.name): p.id for p in pillars}
    comps = (await s.scalars(select(Competitor).where(Competitor.status == "confirmed"))).all()
    if not comps:
        comps = (await s.scalars(select(Competitor).where(Competitor.status == "suggested")
                                 .order_by(Competitor.overlap_score.desc()).limit(5))).all()
    themes: list[str] = []
    for c in comps:
        d = _d(c.data)
        themes += [f"{c.name}: {t}" for t in (d.get("content_themes") or [])[:6] + (d.get("blog_topics") or [])[:6]]
    our_titles = (await s.scalars(
        select(KnowledgeDocument.title).join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(KnowledgeSource.kind == "website").limit(80))).all()
    gsc = _gsc_queries(await s.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "gsc")
                                      .order_by(AnalyticsSnapshot.created_at.desc()).limit(1)))
    existing_titles = (await s.scalars(select(Opportunity.title))).all()
    existing = {normalize_name(t) for t in existing_titles}

    def build_prompt(avoid: list[str], count: int) -> str:
        parts = [f"Company facts:\n{context}"]
        if pillars:
            parts.append("Content pillars:\n" + "\n".join(f"- {p.name}: {p.description or ''}" for p in pillars))
        if our_titles:
            parts.append("Topics our website already covers:\n" + "\n".join(f"- {t}" for t in our_titles[:50] if t))
        if themes:
            parts.append("Competitor content themes:\n" + "\n".join(f"- {t}" for t in themes[:40]))
        if gsc:
            parts.append("Google Search Console queries (impressions, clicks, position):\n"
                         + "\n".join(f"- {g['query']} ({g['impressions']}, {g['clicks']}, {g['position']})" for g in gsc))
        if avoid:
            parts.append("Do NOT repeat these existing ideas:\n" + "\n".join(f"- {t}" for t in avoid[:60]))
        parts.append(f"Propose {count} ideas.")
        return "\n\n".join(parts)[:MAX_PROMPT_CHARS]

    created: list[Opportunity] = []
    for attempt in range(2):
        await ctx.progress("Generating topic ideas" if attempt == 0 else "Generating more ideas")
        avoid = list(existing_titles[-40:]) + [o.title for o in created]
        res = await _ai_json(s, "plan", build_prompt(avoid, 30), IDEAS_SYSTEM, max_tokens=4000)
        ideas = _d(res).get("ideas") if isinstance(res, dict) else res
        for it in ideas if isinstance(ideas, list) else []:
            if not isinstance(it, dict) or len(created) >= 40:
                continue
            title = _s(it.get("title"), 200)
            key = normalize_name(title)
            if len(key) < 8 or key in existing:
                continue
            existing.add(key)
            src = _s(it.get("source"), 20).lower()
            src = src if src in ("idea", "gap", "gsc", "competitor") else "idea"
            if src == "gsc" and not gsc:
                src = "idea"
            if src in ("gap", "competitor") and not themes:
                src = "idea"
            opp = Opportunity(
                organization_id=ctx.org_id, title=title, angle=_s(it.get("angle"), 500) or None,
                pillar_id=pillar_by_name.get(normalize_name(_s(it.get("pillar"), 120))), source=src,
                score=_f01(it.get("score")), status="new",
                data={"keywords": _ls(it.get("keywords"), 6, 60), "formats": _ls(it.get("formats"), 4, 30),
                      "generated_at": now().isoformat()},
            )
            s.add(opp)
            created.append(opp)
        await s.commit()
        if len(created) >= 20:
            break
    return {"created": len(created)}


# ------------------------------------------------------------------ the whole chain
@handler("intel.run_all")
async def h_run_all(ctx: JobContext) -> dict:
    """Crawl website → extract profile → audit → ideas → competitors, with progress on one job."""
    s = ctx.session
    source = await ensure_website_source(s, ctx.org_id)
    if source is None:
        raise PermanentError(NO_WEBSITE)
    await s.commit()
    steps: dict[str, dict] = {}

    def sub(kind: str, payload: dict) -> JobContext:
        return JobContext(ctx.job_id, kind, ctx.org_id, payload, s, ctx.attempt)

    async def step(n: int, label: str, kind: str, fn, payload: dict, required: bool) -> None:
        await ctx.progress(f"Step {n} of 5: {label}")
        orig = ctx.progress

        async def prog(msg: str) -> None:
            await orig(f"Step {n} of 5: {label} — {msg}")

        sc = sub(kind, payload)
        sc.progress = prog  # type: ignore[method-assign]
        try:
            steps[kind] = {"ok": True, **(await fn(sc) or {})}
        except Exception as e:
            await s.rollback()
            steps[kind] = {"ok": False, "error": str(e)[:500]}
            if required:
                raise PermanentError(f"{label} failed: {e}") if not isinstance(e, PermanentError) else e

    await step(1, "Crawling your website", "intel.crawl", h_crawl,
               {"source_id": str(source.id), "chain": False, "max_pages": ctx.payload.get("max_pages")}, True)
    await step(2, "Building the company profile", "intel.extract", h_extract, {"chain": False}, True)
    await step(3, "Auditing your digital presence", "intel.audit", h_audit, {}, False)
    await step(4, "Finding competitors", "intel.competitors", h_competitors, {}, False)
    await step(5, "Generating topic ideas", "intel.ideas", h_ideas, {}, False)
    await s.commit()
    ok = sum(1 for v in steps.values() if v.get("ok"))
    await _notify(ctx.org_id, "intel_ready", "Company analysis finished",
                  f"{ok} of {len(steps)} steps completed. Review the company profile and suggested competitors.")
    return {"steps": steps}


# ------------------------------------------------------------------ periodic refresh
async def _org_session(org_id: uuid.UUID) -> AsyncSession:
    s = SessionLocal()
    set_tenant(s, org_id)
    return s


@periodic(3600, "intel_weekly_recrawl")
async def intel_weekly_recrawl() -> None:
    """Re-crawl org websites and confirmed competitors whose last crawl is older than a week."""
    cutoff = now() - timedelta(days=RECRAWL_DAYS)
    for org_id in await org_ids_active():
        try:
            async with await _org_session(org_id) as s:
                await ensure_website_source(s, org_id)
                sources = (await s.scalars(select(KnowledgeSource).where(
                    KnowledgeSource.kind == "website", KnowledgeSource.last_crawled_at.is_not(None),
                    KnowledgeSource.last_crawled_at < cutoff, KnowledgeSource.status != "running"))).all()
                for src in sources:
                    await enqueue(s, "intel.crawl", org_id, {"source_id": str(src.id), "chain": True},
                                  dedupe_key=f"intel.crawl:{src.id}", priority=7)
                comps = (await s.scalars(select(Competitor).where(Competitor.status == "confirmed"))).all()
                for c in comps:
                    if c.last_crawled_at is None or c.last_crawled_at < cutoff:
                        await enqueue(s, "intel.competitor_crawl", org_id, {"competitor_id": str(c.id)},
                                      dedupe_key=f"intel.competitor_crawl:{c.id}", priority=8)
                await s.commit()
        except Exception:
            log.exception("weekly re-crawl scheduling failed for %s", org_id)


@periodic(3600, "intel_monthly_competitors")
async def intel_monthly_competitors() -> None:
    """Re-run competitor discovery monthly for orgs that ran it before."""
    cutoff = now() - timedelta(days=REDISCOVER_DAYS)
    for org_id in await org_ids_active():
        try:
            async with await _org_session(org_id) as s:
                profile = await s.scalar(select(CompanyProfile).limit(1))
                last = _d(profile.data if profile else {}).get("competitors_discovered_at")
                if not last:
                    continue
                try:
                    when = datetime.fromisoformat(last)
                except ValueError:
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=UTC)
                if when < cutoff:
                    await enqueue(s, "intel.competitors", org_id, {}, dedupe_key=f"intel.competitors:{org_id}", priority=8)
                    await s.commit()
        except Exception:
            log.exception("competitor rediscovery scheduling failed for %s", org_id)
