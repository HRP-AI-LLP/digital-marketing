"""Deep research → daily idea briefs.

A run is an agentic, multi-step pipeline (each stage is logged to ResearchRun.steps and its output kept in
ResearchRun.data, so a retried job resumes where it stopped):

  1. agenda      themes × what the organization knows about itself (deterministic, see build_agenda)
  2. queries     the "plan" model turns the agenda into focused web/news searches (with recency)
  3. search      every configured search provider; URLs de-duplicated; blocked domains dropped; ranked
  4. read        top pages fetched politely (SSRF-safe client, robots.txt, timeouts, size cap, page budget)
  5. facts       the "extract" model pulls dated facts, each tied to the page it came from
  6. ideas       the "plan" model synthesises idea candidates from the facts (+ upcoming occasions)
  7. critique    the "qa" model scores relevance, timeliness, business value and evidence; novelty is
                 measured with embeddings against past briefs and posts (word overlap without embeddings)
  8. follow-up   deep runs search again for the most promising ideas to strengthen their evidence
  9. save        top N briefs; occasion briefs; high scorers optionally added to the planner's topics

Every LLM call goes through app/services/llm/router.py (task routing, budgets, usage logging).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CompanyEntity,
    CompanyProfile,
    Competitor,
    ContentPillar,
    IdeaBrief,
    Opportunity,
    Organization,
    Post,
    ResearchRun,
    ResearchSettings,
    ResearchSource,
)
from app.services import crawler, knowledge, occasions, search
from app.services.knowledge import normalize_name
from app.services.llm import router as llm_router

log = logging.getLogger(__name__)

NO_AI = "Add an AI text model in Settings → Integrations, then run research again."
NO_SEARCH_HINT = ("Web search isn't working, so these ideas are based on your company knowledge and the occasions "
                  "calendar only. For the best results add a free Brave Search or Tavily key in Settings → Integrations.")
NO_CONTEXT = "Analyze your website on the Company intelligence page first, so research knows what your company does."

# ------------------------------------------------------------------ catalogues and defaults
THEMES: list[dict] = [
    {"key": "company", "label": "Company and products", "recency": "month",
     "about": "news and demand around the company's own products, services and use cases"},
    {"key": "tech_ai", "label": "Technology, AI and machine learning", "recency": "week",
     "about": "AI, machine learning, generative AI, AI agents, RAG and data platforms"},
    {"key": "studios", "label": "AI studios and tools", "recency": "month",
     "about": "AI studios, low-code AI builders, developer tools and model releases"},
    {"key": "industries", "label": "Industries we serve", "recency": "month",
     "about": "trends and problems in the business domains the company sells to"},
    {"key": "challenges", "label": "Business challenges and AI solutions", "recency": "month",
     "about": "current business challenges and how AI solves them, with numbers"},
    {"key": "market_news", "label": "Hot market topics and news", "recency": "week",
     "about": "hot business and technology news in India and globally"},
    {"key": "policy", "label": "Policy and regulation", "recency": "month",
     "about": "India's labour codes, the DPDP Act, AI regulation and compliance changes"},
    {"key": "events", "label": "Events and observances", "recency": "month",
     "about": "upcoming conferences, observances, festivals and awareness days"},
    {"key": "thought_leadership", "label": "Thought leadership", "recency": "year",
     "about": "research reports, surveys and expert views that support a strong point of view"},
]
THEME_LABELS = {t["key"]: t["label"] for t in THEMES}
FORMATS = {
    "linkedin_post": "LinkedIn post", "instagram_carousel": "Instagram carousel", "short_video": "Short video script",
    "x_thread": "X / Threads thread", "seo_article": "SEO article or blog", "newsletter": "Newsletter blurb",
}
_FORMAT_ALIASES = {
    "linkedin": "linkedin_post", "post": "linkedin_post", "carousel": "instagram_carousel", "instagram": "instagram_carousel",
    "video": "short_video", "reel": "short_video", "reels": "short_video", "short": "short_video", "thread": "x_thread",
    "twitter": "x_thread", "x": "x_thread", "threads": "x_thread", "article": "seo_article", "blog": "seo_article",
    "seo": "seo_article", "newsletter": "newsletter", "email": "newsletter",
}


@dataclass(frozen=True)
class Depth:
    themes: int
    queries_per_theme: int
    results_per_query: int
    pages: int
    candidates: int
    follow_up_ideas: int
    max_llm_calls: int


DEPTHS = {
    "quick": Depth(themes=4, queries_per_theme=2, results_per_query=6, pages=8, candidates=12, follow_up_ideas=0, max_llm_calls=8),
    "standard": Depth(themes=8, queries_per_theme=3, results_per_query=8, pages=20, candidates=18, follow_up_ideas=0,
                      max_llm_calls=16),
    "deep": Depth(themes=12, queries_per_theme=4, results_per_query=10, pages=40, candidates=24, follow_up_ideas=4,
                  max_llm_calls=32),
}

DEFAULT_CONFIG: dict = {
    "themes": {t["key"]: True for t in THEMES},
    "custom_themes": [],  # [{"label": "...", "focus": "..."}]
    "keywords": [],
    "trusted_domains": [],
    "blocked_domains": [],
    "excluded_topics": [],
    "auto_topics": {"enabled": True, "min_score": 0.75, "max_per_day": 3},
    "occasions": occasions.DEFAULT_SETTINGS,
}
DEFAULTS = {"enabled": True, "run_time": "07:00", "depth": "standard", "ideas_per_day": 8}

# Pages that are rarely useful evidence for B2B content.
LOW_VALUE_DOMAINS = {
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "pinterest.com", "youtube.com", "youtu.be",
    "linkedin.com", "threads.net", "t.me", "whatsapp.com", "amazon.com", "amazon.in", "flipkart.com", "indiamart.com",
    "ebay.com", "naukri.com", "indeed.com", "glassdoor.com", "glassdoor.co.in", "ambitionbox.com", "justdial.com",
    "quora.com", "play.google.com", "apps.apple.com",
}
MAX_PAGE_CHARS = 1800
FETCH_CONCURRENCY = 4
FETCH_TIMEOUT_S = 25
SEEN_SOURCE_DAYS = 21
NOVELTY_LOOKBACK_DAYS = 60
MIN_NOVELTY = 0.3  # below this an idea repeats a recent brief or post and is dropped
MAX_FACTS_PER_BRIEF = 6


def now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ small coercion helpers
def _s(v: Any, n: int = 500) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()[:n]


def _ls(v: Any, items: int = 10, n: int = 200) -> list[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[;\n]|, ", v) if x.strip()]
    if not isinstance(v, list):
        return []
    out: dict[str, str] = {}
    for x in v:
        s = _s(x, n)
        if s and normalize_name(s) not in out:
            out[normalize_name(s)] = s
        if len(out) >= items:
            break
    return list(out.values())


def _f01(v: Any, default: float = 0.5) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if 1 < f <= 10:
        f = f / 10
    elif 10 < f <= 100:
        f = f / 100
    return round(min(1.0, max(0.0, f)), 3)


def _iso_date(v: Any) -> str | None:
    m = re.search(r"(19|20)\d{2}-\d{2}-\d{2}", str(v or ""))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(0)).isoformat()
    except ValueError:
        return None


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


# ------------------------------------------------------------------ settings
def effective_config(row: ResearchSettings | None) -> dict:
    """Settings merged over the defaults (also used by the API)."""
    cfg = _d(row.config if row else {})
    out = {**DEFAULTS, **{k: v for k, v in DEFAULT_CONFIG.items()}}
    if row is not None:
        out.update({"enabled": row.enabled, "run_time": row.run_time, "depth": row.depth, "ideas_per_day": row.ideas_per_day})
    for k in ("custom_themes", "keywords", "trusted_domains", "blocked_domains", "excluded_topics"):
        if isinstance(cfg.get(k), list):
            out[k] = cfg[k]
    out["themes"] = {**DEFAULT_CONFIG["themes"], **{k: bool(v) for k, v in _d(cfg.get("themes")).items()}}
    out["auto_topics"] = {**DEFAULT_CONFIG["auto_topics"], **_d(cfg.get("auto_topics"))}
    out["occasions"] = occasions.merged_settings(_d(cfg.get("occasions")))
    if out["depth"] not in DEPTHS:
        out["depth"] = "standard"
    return out


async def get_settings_row(session: AsyncSession, org_id: uuid.UUID, create: bool = False) -> ResearchSettings | None:
    row = await session.scalar(select(ResearchSettings).limit(1))
    if row is None and create:
        row = ResearchSettings(organization_id=org_id, enabled=True, run_time="07:00", depth="standard", ideas_per_day=8,
                               config={}, data={})
        session.add(row)
        await session.flush()
    return row


def org_tz(org: Organization | None) -> ZoneInfo:
    try:
        return ZoneInfo(org.timezone if org and org.timezone else "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


def verified_dates(row: ResearchSettings | None) -> dict:
    return _d(_d(row.data if row else {}).get("verified_occasions"))


# ------------------------------------------------------------------ URLs and domains
def url_key(url: str) -> str:
    """Canonical form used to de-duplicate sources: no scheme, no www, no tracking params, no trailing slash."""
    norm = crawler.normalize_url(url) or url
    p = urlsplit(norm)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/+$", "", p.path or "")
    q = f"?{p.query}" if p.query else ""
    return f"{host}{path}{q}"[:300]


def domain_matches(domain: str, patterns: list[str]) -> bool:
    d = (domain or "").lower().removeprefix("www.")
    for pat in patterns:
        pat = (pat or "").lower().strip().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]
        if pat and (d == pat or d.endswith("." + pat)):
            return True
    return False


def is_blocked(url: str, cfg: dict, own_domains: set[str]) -> bool:
    dom = crawler.domain_of(url)
    if not dom:
        return True
    if domain_matches(dom, list(cfg.get("trusted_domains") or [])):
        return False
    if dom in own_domains or domain_matches(dom, sorted(LOW_VALUE_DOMAINS)):
        return True
    return domain_matches(dom, list(cfg.get("blocked_domains") or []))


def publisher_of(url: str, site_name: str | None = None) -> str:
    if site_name:
        return _s(site_name, 120)
    dom = crawler.domain_of(url)
    return dom or "web"


# ------------------------------------------------------------------ research context
@dataclass
class Context:
    org_name: str = ""
    website: str = ""
    facts: str = ""
    products: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    clients: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    differentiators: list[str] = field(default_factory=list)
    geography: str = ""
    pillars: list[dict] = field(default_factory=list)
    recent_topics: list[str] = field(default_factory=list)
    past_briefs: list[str] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)  # [{name, url}] on the org's own site, for CTAs
    own_domains: set[str] = field(default_factory=set)

    @property
    def offerings(self) -> list[str]:
        return [*self.products, *self.studios, *self.services]


async def gather_context(session: AsyncSession, org_id: uuid.UUID) -> Context:
    org = await session.get(Organization, org_id)
    profile = await session.scalar(select(CompanyProfile).limit(1))
    ents = (await session.scalars(select(CompanyEntity).order_by(CompanyEntity.confirmed.desc(), CompanyEntity.name))).all()
    by: dict[str, list[CompanyEntity]] = {}
    for e in ents:
        if e.type == "client" and not e.public_mention_ok:
            continue
        by.setdefault(e.type, []).append(e)
    comps = (await session.scalars(select(Competitor).where(Competitor.status != "rejected")
                                   .order_by(Competitor.overlap_score.desc()).limit(8))).all()
    pillars = (await session.scalars(select(ContentPillar).where(ContentPillar.active.is_(True)))).all()
    recent = (await session.scalars(select(Post.topic).where(Post.topic.is_not(None))
                                    .order_by(Post.created_at.desc()).limit(40))).all()
    since = (now() - timedelta(days=NOVELTY_LOOKBACK_DAYS)).date().isoformat()
    past = (await session.scalars(select(IdeaBrief.title).where(IdeaBrief.run_date >= since)
                                  .order_by(IdeaBrief.created_at.desc()).limit(80))).all()
    website = crawler.normalize_url(org.website) if org and org.website else ""
    own = {crawler.domain_of(website)} if website else set()
    pages = [{"name": e.name, "url": e.url} for e in ents
             if e.url and e.type in ("product", "studio", "service", "use_case", "case_study")
             and crawler.domain_of(e.url) in own][:20]

    def names(t: str, n: int = 12) -> list[str]:
        return [e.name for e in by.get(t, [])][:n]

    industries = list(dict.fromkeys([*map(str, (profile.industries if profile else []) or []), *names("industry")]))[:12]
    return Context(
        org_name=org.name if org else "", website=website or "", facts=await knowledge.company_context(session, 2500),
        products=names("product"), studios=names("studio"), services=names("service"), use_cases=names("use_case"),
        industries=industries, clients=names("client", 8), competitors=[c.name for c in comps],
        differentiators=[str(x) for x in ((profile.differentiators if profile else []) or [])][:6],
        geography=(profile.geography if profile else "") or "", pillars=[{"id": str(p.id), "name": p.name} for p in pillars],
        recent_topics=[t for t in recent if t], past_briefs=list(past), pages=pages, own_domains={d for d in own if d},
    )


# ------------------------------------------------------------------ 1. agenda
@dataclass
class AgendaItem:
    theme: str
    label: str
    focus: str
    keywords: list[str] = field(default_factory=list)
    recency: str | None = None


def _join(items: list[str], n: int = 6) -> str:
    return ", ".join(items[:n])


def build_agenda(ctx: Context, cfg: dict, depth: Depth, today: date, upcoming: list[occasions.Occasion] | None = None
                 ) -> list[AgendaItem]:
    """Deterministic research agenda: enabled themes made specific with what the company knows about itself.
    When more themes are enabled than the depth allows, they rotate by day so each gets covered regularly."""
    region = "India" if not ctx.geography or "india" in ctx.geography.lower() else ctx.geography.split(",")[0].strip()
    kw = _ls(cfg.get("keywords"), 10, 80)
    offerings = ctx.offerings
    focus: dict[str, str] = {
        "company": f"{_join(offerings) or ctx.org_name}; use cases: {_join(ctx.use_cases, 4)}",
        "tech_ai": "AI, machine learning, generative AI, AI agents, RAG chatbots, data lakehouse AI",
        "studios": f"{_join(ctx.studios) or 'AI studios'}; AI development tools, model and agent platforms",
        "industries": _join(ctx.industries, 6) or "HR, manufacturing, retail, BFSI, IT services",
        "challenges": f"business challenges in {_join(ctx.industries, 4) or 'Indian companies'} that "
                      f"{_join(ctx.use_cases, 4) or 'AI and automation'} solve",
        "market_news": f"business and technology news {region} and global; {_join(ctx.industries, 3)}",
        "policy": f"{region} labour codes, DPDP Act data protection, AI regulation and compliance for employers",
        "events": "; ".join([*(o.name for o in (upcoming or [])[:5]), f"AI and HR tech conferences {region}"]),
        "thought_leadership": f"surveys and research reports on {_join([*ctx.differentiators, *offerings], 4) or 'AI adoption'}",
    }
    items: list[AgendaItem] = []
    for t in THEMES:
        if cfg.get("themes", {}).get(t["key"], True):
            items.append(AgendaItem(t["key"], t["label"], focus[t["key"]], kw, t["recency"]))
    for i, c in enumerate(cfg.get("custom_themes") or []):
        label = _s(_d(c).get("label") if isinstance(c, dict) else c, 80)
        if label:
            items.append(AgendaItem(f"custom_{i + 1}", label, _s(_d(c).get("focus"), 300) or label, kw, "week"))
    if len(items) > depth.themes:
        shift = today.toordinal() % len(items)
        items = (items[shift:] + items[:shift])[:depth.themes]
    return items


# ------------------------------------------------------------------ 2. queries
QUERIES_SYSTEM = """You plan web searches for a B2B marketing team's daily research.
For each research theme write focused search queries a journalist or analyst would type: specific, current,
4-10 words, mixing India and global angles, including the year when it helps. Prefer queries that surface
fresh news, statistics, reports and real examples. Never include the company's own name.
Return ONLY JSON: {"queries": [{"theme": "<theme key>", "q": "query", "recency": "day|week|month|year|any"}]}"""


@dataclass
class Query:
    theme: str
    q: str
    recency: str | None = None


def fallback_queries(item: AgendaItem, year: int, n: int) -> list[Query]:
    parts = [p.strip() for p in re.split(r"[;,]", item.focus) if p.strip()]
    out = [Query(item.theme, f"{p} {year}".strip(), item.recency) for p in parts[:n]]
    if item.keywords and len(out) < n:
        out.append(Query(item.theme, f"{item.keywords[0]} {item.label} {year}", item.recency))
    return out[:n]


def normalize_queries(raw: Any, agenda: list[AgendaItem], per_theme: int, year: int) -> list[Query]:
    by_theme = {a.theme: a for a in agenda}
    rows = _d(raw).get("queries") if isinstance(raw, dict) else raw
    seen: set[str] = set()
    counts: dict[str, int] = {}
    out: list[Query] = []

    def add(q: Query) -> None:
        key = normalize_name(q.q)
        if len(key) < 4 or key in seen or counts.get(q.theme, 0) >= per_theme:
            return
        seen.add(key)
        counts[q.theme] = counts.get(q.theme, 0) + 1
        out.append(q)

    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, str):
            r = {"q": r, "theme": agenda[0].theme if agenda else "tech_ai"}
        if not isinstance(r, dict):
            continue
        theme = _s(r.get("theme"), 60)
        if theme not in by_theme:
            continue
        rec = _s(r.get("recency"), 10).lower()
        add(Query(theme, _s(r.get("q") or r.get("query"), 160),
                  rec if rec in search.FRESHNESS else (None if rec == "any" else by_theme[theme].recency)))
    for a in agenda:  # every theme gets queries even when the model skipped it
        if counts.get(a.theme, 0) < min(2, per_theme):
            for q in fallback_queries(a, year, per_theme):
                add(q)
    return out


# ------------------------------------------------------------------ 3. search
def rank_hits(hits: dict[str, dict], cfg: dict, seen_keys: set[str]) -> list[dict]:
    trusted = list(cfg.get("trusted_domains") or [])

    def score(h: dict) -> float:
        s = len(h["queries"]) * 1.0 + 1.0 / (1 + h["rank"])
        if domain_matches(crawler.domain_of(h["url"]), trusted):
            s += 1.5
        if h["key"] in seen_keys:
            s -= 1.2  # read in a recent run: prefer fresh sources
        return s

    return sorted(hits.values(), key=lambda h: (-score(h), h["rank"]))


# ------------------------------------------------------------------ 4. read pages
class Fetcher:
    """Polite page reader: SSRF-safe client from the crawler, robots.txt per host, size cap and time limit."""

    def __init__(self) -> None:
        self.safe = crawler._SafeHosts()
        self.robots: dict[str, RobotFileParser] = {}
        self._lock = asyncio.Lock()

    async def _allowed(self, client, url: str) -> bool:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        async with self._lock:
            if origin not in self.robots:
                rp = RobotFileParser()
                f = await crawler.fetch(client, f"{origin}/robots.txt", self.safe)
                if f.status >= 500 or (f.status == 0 and f.error and "Blocked" not in f.error):
                    rp.disallow_all = True
                elif f.status >= 400 or f.status == 0 or not f.body:
                    rp.allow_all = True
                else:
                    rp.parse(f.text().splitlines())
                self.robots[origin] = rp
        try:
            return self.robots[origin].can_fetch(crawler.USER_AGENT, url)
        except Exception:  # noqa: BLE001
            return True

    async def read(self, client, url: str) -> crawler.Page | None:
        try:
            start = await crawler.check_public_url(url)
        except crawler.UnsafeURLError:
            return None
        if not await self._allowed(client, start):
            return None
        f = await crawler.fetch(client, start, self.safe)
        if f.error or f.status >= 400 or not f.is_html or not f.body:
            return None
        final = crawler.normalize_url(f.final_url) or start
        page = await asyncio.to_thread(crawler.parse_html, f.text(), final)
        page.status = f.status
        return page

    async def read_many(self, urls: list[str]) -> dict[str, crawler.Page]:
        sem = asyncio.Semaphore(FETCH_CONCURRENCY)
        out: dict[str, crawler.Page] = {}
        async with crawler.make_client() as client:
            async def one(u: str) -> None:
                async with sem:
                    try:
                        page = await asyncio.wait_for(self.read(client, u), timeout=FETCH_TIMEOUT_S)
                    except (TimeoutError, Exception):  # noqa: BLE001 - a bad page must not stop the run
                        page = None
                    if page is not None and len(page.text) >= 200:
                        out[u] = page

            await asyncio.gather(*(one(u) for u in urls))
        return out


def page_record(hit: dict, page: crawler.Page | None) -> dict:
    """What the fact extractor sees about one source."""
    published = None
    if page is not None:
        published = page.dates[0] if page.dates else None
    text = (page.text if page is not None else "") or hit.get("snippet") or ""
    return {
        "url": hit["url"], "key": hit["key"], "title": _s((page.title if page else "") or hit.get("title"), 300),
        "publisher": publisher_of(hit["url"], page.og.get("og:site_name") if page else None),
        "date": _iso_date(published), "theme": hit.get("theme"), "query": (hit.get("queries") or [""])[0],
        "snippet": _s(hit.get("snippet"), 600), "fetched": page is not None, "excerpt": text[:MAX_PAGE_CHARS],
    }


# ------------------------------------------------------------------ 5. facts
FACTS_SYSTEM = """You extract facts for a B2B marketing team from web pages.
Pick facts that are concrete, recent and quotable: numbers, launches, rule changes, survey results, named events.
Use only what each source says; never invent numbers. Attribute each fact to its source id.
Return ONLY JSON: {"facts": [{"source": "S1", "fact": "one sentence, max 300 characters",
 "date": "YYYY-MM-DD the fact refers to or was published, or empty"}]}
Return at most 4 facts per source and skip sources with nothing useful."""


def parse_facts(raw: Any, sources: list[dict]) -> list[dict]:
    by_id = {f"S{i + 1}": s for i, s in enumerate(sources)}
    rows = _d(raw).get("facts") if isinstance(raw, dict) else raw
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        sid = _s(r.get("source"), 10).upper().replace(" ", "")
        if sid.isdigit():
            sid = f"S{sid}"
        src = by_id.get(sid)
        text = _s(r.get("fact"), 320)
        if src is None or len(text) < 15 or normalize_name(text) in seen:
            continue
        seen.add(normalize_name(text))
        out.append({"fact": text, "source_url": src["url"], "publisher": src["publisher"], "title": src["title"],
                    "date": _iso_date(r.get("date")) or src.get("date"), "theme": src.get("theme")})
    return out


# ------------------------------------------------------------------ 6. ideas
IDEAS_SYSTEM = """You are the head of content at a B2B AI company. From today's research facts, propose fresh,
specific content ideas the company can publish now. Each idea must:
- connect a fact (news, trend, number, rule change, occasion) to the company's offerings, audiences or expertise;
- cite the facts it relies on by id (F1, F2 ...), at least two when possible; never invent statistics or clients;
- avoid repeating the recent topics and past ideas listed.
Formats: linkedin_post, instagram_carousel, short_video, x_thread, seo_article, newsletter.
Return ONLY JSON: {"ideas": [{"title": "max 110 characters", "angle": "the hook or point of view",
 "why_now": "why this matters this week", "theme": "<theme key>", "fact_ids": ["F1"], "audience": "who it is for",
 "formats": [{"format": "linkedin_post", "note": "how to use this format"}],
 "article": {"keyword": "target search keyword", "secondary_keywords": [], "outline": ["H2 heading"]},
 "cta": {"text": "call to action", "url": "one of the company pages given, or the website"},
 "hashtags": ["#Tag"], "pillar": "exact pillar name or empty"}]}"""

OCCASIONS_SYSTEM = """You plan occasion-based content for a B2B AI company in India.
For each occasion propose ONE idea: a greeting or campaign that ties the occasion to the company tastefully.
Follow the tone guidance for each occasion exactly: inclusive language, respectful of every faith and region,
no hard selling on solemn or commemorative days, no discounts unless the occasion is commercial.
Return ONLY JSON: {"ideas": [{"occasion_id": "<id given>", "title": "max 110 characters", "angle": "",
 "why_now": "", "audience": "", "formats": [{"format": "linkedin_post|instagram_carousel|short_video|x_thread|seo_article|newsletter",
 "note": ""}], "cta": {"text": "", "url": ""}, "hashtags": [], "greeting": "a short greeting line"}]}"""

CRITIQUE_SYSTEM = """You are a strict content editor. Score each idea from 0 to 1:
relevance = fit with the company's offerings and audiences; timeliness = how much it depends on this week's news;
business_value = likelihood to create demand or authority for the company; evidence = how well the cited facts support it.
Penalise generic, clickbait or unsupported ideas. Return ONLY JSON:
{"scores": [{"i": 1, "relevance": 0.0, "timeliness": 0.0, "business_value": 0.0, "evidence": 0.0, "reason": "one sentence"}]}"""


def normalize_formats(v: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for x in v if isinstance(v, list) else []:
        fmt, note = (x, "") if isinstance(x, str) else (_d(x).get("format"), _d(x).get("note"))
        key = _s(fmt, 40).lower().replace(" ", "_").replace("-", "_")
        key = key if key in FORMATS else _FORMAT_ALIASES.get(key.split("_")[0], "")
        if key and key not in seen:
            seen.add(key)
            out.append({"format": key, "label": FORMATS[key], "note": _s(note, 300)})
    return out or [{"format": "linkedin_post", "label": FORMATS["linkedin_post"], "note": ""}]


def _tag(t: Any) -> str:
    s = re.sub(r"[^\w]", "", _s(t, 60).lstrip("#"))
    return f"#{s}" if s else ""


def _cta(raw: Any, ctx: Context) -> dict:
    d = _d(raw)
    url = _s(d.get("url"), 500)
    if not url.startswith(("http://", "https://")) or (ctx.own_domains and crawler.domain_of(url) not in ctx.own_domains):
        url = ctx.website or ""
    return {"text": _s(d.get("text"), 200) or "Learn more", "url": url}


def normalize_idea(raw: dict, facts: dict[str, dict], ctx: Context, theme_keys: set[str]) -> dict | None:
    title = _s(raw.get("title"), 160)
    if len(normalize_name(title)) < 8:
        return None
    ids = [f"F{x}" if str(x).isdigit() else _s(x, 8).upper() for x in (raw.get("fact_ids") or [])]
    cited = [facts[i] for i in dict.fromkeys(ids) if i in facts][:MAX_FACTS_PER_BRIEF]
    theme = _s(raw.get("theme"), 60)
    if theme not in theme_keys:
        theme = cited[0].get("theme") if cited and cited[0].get("theme") in theme_keys else "tech_ai"
    art = _d(raw.get("article"))
    pillar = normalize_name(_s(raw.get("pillar"), 120))
    return {
        "title": title, "angle": _s(raw.get("angle"), 600), "why_now": _s(raw.get("why_now"), 600), "theme": theme,
        "audience": _s(raw.get("audience"), 300), "facts": [{k: f.get(k) for k in ("fact", "source_url", "publisher", "date", "title")}
                                                            for f in cited],
        "formats": normalize_formats(raw.get("formats")),
        "article": {"keyword": _s(art.get("keyword"), 120) or title[:80], "secondary_keywords": _ls(art.get("secondary_keywords"), 8, 80),
                    "outline": _ls(art.get("outline"), 10, 160)},
        "cta": _cta(raw.get("cta"), ctx), "hashtags": [t for t in (_tag(h) for h in _ls(raw.get("hashtags"), 8, 60)) if t],
        "pillar_id": next((p["id"] for p in ctx.pillars if normalize_name(p["name"]) == pillar), None) if pillar else None,
    }


# ------------------------------------------------------------------ 7. scoring
WEIGHTS = {"relevance": 0.3, "timeliness": 0.2, "novelty": 0.2, "business_value": 0.2, "evidence": 0.1}


def evidence_score(facts: list[dict], today: date) -> float:
    """Deterministic evidence: distinct sources (3+ is full marks) and how fresh the facts are."""
    sources = {f.get("source_url") for f in facts if f.get("source_url")}
    base = min(1.0, len(sources) / 3) * 0.7
    ages = []
    for f in facts:
        try:
            ages.append((today - date.fromisoformat(str(f.get("date")))).days)
        except ValueError:
            continue
    if ages and min(ages) <= 30:
        base += 0.3
    elif ages and min(ages) <= 365:
        base += 0.15
    return round(min(1.0, base), 3)


def word_set(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())} - {"the", "and", "for", "with", "how", "why", "what", "your"}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def novelty_scores(session: AsyncSession, texts: list[str], past: list[str],
                         past_vectors: list[tuple[list[float], str]]) -> tuple[list[float], list[list[float]] | None, str | None]:
    """1 - max similarity to past briefs and posts. Embeddings when an embedding model is set up, else word overlap."""
    if not texts:
        return [], None, None
    vecs, model = None, None
    try:
        vecs, model = await llm_router.embed_texts(session, texts)
    except Exception as e:  # noqa: BLE001
        log.info("embeddings unavailable for novelty: %s", e)
    if vecs and model and len(vecs) == len(texts):
        refs = [v for v, m in past_vectors if m == model]
        if past:
            pv, pm = await llm_router.embed_texts(session, past[:80])
            if pv and pm == model:
                refs += pv
        sims = [max((cosine(v, r) for r in refs), default=0.0) for v in vecs]
        return [round(max(0.0, 1 - s), 3) for s in sims], vecs, model
    past_sets = [word_set(p) for p in past]
    return [round(1 - max((jaccard(word_set(t), p) for p in past_sets), default=0.0), 3) for t in texts], None, None


def combine(scores: dict) -> float:
    return round(sum(scores.get(k, 0.5) * w for k, w in WEIGHTS.items()), 3)


def excluded(idea: dict, topics: list[str]) -> bool:
    hay = f"{idea.get('title', '')} {idea.get('angle', '')}".lower()
    return any(t.strip().lower() and t.strip().lower() in hay for t in topics)


def select_top(ideas: list[dict], n: int, per_theme: int = 3, similar: float = 0.6) -> list[dict]:
    """Best ideas with theme variety and without near-duplicates."""
    out: list[dict] = []
    counts: dict[str, int] = {}
    for idea in sorted(ideas, key=lambda i: -i["score"]):
        if counts.get(idea["theme"], 0) >= per_theme:
            continue
        ws = word_set(idea["title"])
        if any(jaccard(ws, word_set(o["title"])) >= similar for o in out):
            continue
        out.append(idea)
        counts[idea["theme"]] = counts.get(idea["theme"], 0) + 1
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ run log
class AIError(Exception):
    """The AI model failed or no model is configured; the message is safe to show."""


class RunLog:
    def __init__(self, session: AsyncSession, run: ResearchRun, progress=None) -> None:
        self.session, self.run, self.progress = session, run, progress
        self.llm_calls = int((run.stats or {}).get("llm_calls") or 0)

    async def step(self, stage: str, message: str, level: str = "info") -> None:
        self.run.steps = [*(self.run.steps or []), {"at": now().isoformat(), "stage": stage, "message": message[:500],
                                                    "level": level}][-200:]
        await self.session.commit()
        if self.progress is not None:
            try:
                await self.progress(message)
            except Exception:  # noqa: BLE001
                pass

    def stat(self, **kw) -> None:
        self.run.stats = {**(self.run.stats or {}), **kw}

    def save(self, **kw) -> None:
        self.run.data = {**(self.run.data or {}), **kw}


async def ai_json(log_: RunLog, task: str, prompt: str, system: str, limit: int, max_tokens: int = 2500) -> Any:
    if log_.llm_calls >= limit:
        raise AIError("This run reached its AI call limit for the chosen depth.")
    log_.llm_calls += 1
    log_.stat(llm_calls=log_.llm_calls)
    try:
        return await llm_router.complete_json(log_.session, task, prompt[:14000], system=system, temperature=0.4,
                                              max_tokens=max_tokens)
    except llm_router.NoProviderError:
        raise AIError(NO_AI)
    except llm_router.AllProvidersFailed as e:
        msg = str(e)
        hint = " Raise the monthly budget or add a free model." if "budget" in msg.lower() else ""
        raise AIError(f"{msg[:400]}{hint}")
    except ValueError:
        return None


# ------------------------------------------------------------------ the pipeline
def _company_block(ctx: Context) -> str:
    lines = [f"Company: {ctx.org_name} ({ctx.website})", ctx.facts[:2200]]
    if ctx.competitors:
        lines.append("Competitors (never name them in posts): " + ", ".join(ctx.competitors[:6]))
    if ctx.pillars:
        lines.append("Content pillars: " + ", ".join(p["name"] for p in ctx.pillars))
    if ctx.pages:
        lines.append("Company pages for calls to action:\n" + "\n".join(f"- {p['name']}: {p['url']}" for p in ctx.pages[:12]))
    return "\n".join(x for x in lines if x)


def _avoid_block(ctx: Context) -> str:
    avoid = list(dict.fromkeys([*ctx.recent_topics[:25], *ctx.past_briefs[:40]]))
    return ("Recent topics and past ideas — do not repeat:\n" + "\n".join(f"- {a[:140]}" for a in avoid)) if avoid else ""


async def _seen_source_keys(session: AsyncSession, run_id: uuid.UUID) -> set[str]:
    since = now() - timedelta(days=SEEN_SOURCE_DAYS)
    rows = await session.scalars(select(ResearchSource.url_key).where(
        ResearchSource.created_at >= since, ResearchSource.fetched.is_(True), ResearchSource.run_id != run_id))
    return set(rows.all())


async def search_queries(session: AsyncSession, queries: list[Query], cfg: dict, depth: Depth, own: set[str],
                         log_: RunLog) -> tuple[dict[str, dict], list[str]]:
    hits: dict[str, dict] = {}
    errors: list[str] = []
    ok = 0
    for i, q in enumerate(queries, 1):
        if i == 1 or i % 4 == 0:
            await log_.step("search", f"Searching the web ({i} of {len(queries)}): {q.q}")
        try:
            rows, errs = await search.search_all(session, q.q, depth.results_per_query, freshness=q.recency)
        except search.SearchError as e:
            errors.append(str(e))
            continue
        ok += 1
        errors += errs
        for rank, r in enumerate(rows):
            url = r.get("url") or ""
            if not url.startswith(("http://", "https://")) or is_blocked(url, cfg, own):
                continue
            key = url_key(url)
            h = hits.setdefault(key, {"key": key, "url": url, "title": r.get("title"), "snippet": r.get("snippet"),
                                      "rank": rank, "queries": [], "theme": q.theme, "providers": []})
            h["rank"] = min(h["rank"], rank)
            if q.q not in h["queries"]:
                h["queries"].append(q.q)
            if r.get("provider") and r["provider"] not in h["providers"]:
                h["providers"].append(r["provider"])
            if not h.get("snippet") and r.get("snippet"):
                h["snippet"] = r["snippet"]
    if queries and not ok:
        errors.insert(0, "all searches failed")
    return hits, errors


async def extract_facts(log_: RunLog, sources: list[dict], depth: Depth, stage_label: str = "facts") -> list[dict]:
    facts: list[dict] = []
    batch = 4
    for i in range(0, len(sources), batch):
        part = sources[i:i + batch]
        await log_.step(stage_label, f"Reading sources and noting facts ({min(i + batch, len(sources))} of {len(sources)})")
        block = "\n\n".join(
            f"[S{j + 1}] {s['title']} — {s['publisher']}" + (f" ({s['date']})" if s.get("date") else "")
            + f"\nURL: {s['url']}\n{s['excerpt'] or s['snippet']}" for j, s in enumerate(part))
        raw = await ai_json(log_, "extract", block, FACTS_SYSTEM, depth.max_llm_calls, max_tokens=1800)
        facts += parse_facts(raw, part)
    return facts


async def knowledge_facts(session: AsyncSession, ctx: Context, agenda: list[AgendaItem]) -> list[dict]:
    """Facts from the company's own knowledge base, used when web search is unavailable."""
    out: list[dict] = []
    for a in agenda[:4]:
        for r in await knowledge.retrieve(session, a.focus, k=2):
            text = _s(r.get("text"), 300)
            if text:
                out.append({"fact": text, "source_url": r.get("url") or ctx.website, "publisher": ctx.org_name,
                            "title": r.get("title") or ctx.org_name, "date": None, "theme": a.theme})
    return out


async def verify_occasions(session: AsyncSession, settings_row: ResearchSettings | None, upcoming: list[occasions.Occasion],
                           log_: RunLog, limit: int) -> int:
    """Check variable dates (moon-sighting, derived) against the web; record what was found with its source."""
    if settings_row is None:
        return 0
    data = dict(settings_row.data or {})
    verified = dict(_d(data.get("verified_occasions")))
    checked = 0
    for o in upcoming:
        if not occasions.needs_verification(o) or o.id in verified or checked >= 2:
            continue
        checked += 1
        try:
            rows, _ = await search.search_all(session, occasions.verification_query(o), 6)
        except search.SearchError:
            continue
        if not rows:
            continue
        block = "\n".join(f"[S{i + 1}] {r['title']} — {r['url']}\n{r.get('snippet') or ''}" for i, r in enumerate(rows[:6]))
        raw = await ai_json(log_, "extract", f"Occasion: {o.name} in {o.date.year} (India).\n\n{block}",
                            'From the search results, find the date of the occasion in India. Return ONLY JSON: '
                            '{"date": "YYYY-MM-DD or empty", "source": "S1", "agreeing_sources": 1}', limit, max_tokens=200)
        found = _iso_date(_d(raw).get("date"))
        src_id = _s(_d(raw).get("source"), 5).upper()
        src = rows[int(src_id[1:]) - 1] if re.fullmatch(r"S\d", src_id) and int(src_id[1:]) <= len(rows) else None
        if found and src and abs((date.fromisoformat(found) - o.date).days) <= 3:
            verified[o.id] = {"date": found, "source_url": src["url"], "checked_at": now().isoformat(),
                              "calendar_date": o.date.isoformat()}
            changed = found != o.date.isoformat()
            await log_.step("occasions", f"Checked the date of {o.name}: {found}"
                            + (f" (calendar said {o.date.isoformat()})" if changed else " (matches the calendar)"))
        else:
            await log_.step("occasions", f"Couldn't confirm the date of {o.name} online; keeping {o.date.isoformat()} "
                            f"({o.confidence}).", "warn")
    if checked:
        data["verified_occasions"] = verified
        settings_row.data = data
    return checked


async def existing_occasion_ids(session: AsyncSession) -> set[str]:
    rows = (await session.scalars(select(IdeaBrief.occasion).where(IdeaBrief.occasion.is_not(None)))).all()
    return {str(_d(r).get("id")) for r in rows if _d(r).get("id")}


async def run_pipeline(session: AsyncSession, run: ResearchRun, progress=None) -> dict:
    """Execute one research run (caller handles status on failure). Returns stats."""
    org_id = run.organization_id
    log_ = RunLog(session, run, progress)
    row = await get_settings_row(session, org_id)
    cfg = effective_config(row)
    depth = DEPTHS.get(run.depth) or DEPTHS[cfg["depth"]]
    org = await session.get(Organization, org_id)
    tz = org_tz(org)
    today = date.fromisoformat(run.run_date)
    run.status, run.started_at, run.error = "running", run.started_at or now(), None
    data = dict(run.data or {})
    if data:
        await log_.step("start", "Resuming research where it stopped")
    else:
        await log_.step("start", f"Starting {run.depth} research for {today.strftime('%d %b %Y')}")

    ctx = await gather_context(session, org_id)
    if len(ctx.facts) < 60 and not ctx.offerings:
        raise AIError(NO_CONTEXT)

    occ_cfg = cfg["occasions"]
    verified = verified_dates(row)
    upcoming = occasions.upcoming(today, occ_cfg, verified) if occ_cfg.get("enabled", True) else []

    # 1. agenda
    agenda = [AgendaItem(**a) for a in data.get("agenda") or []] or build_agenda(ctx, cfg, depth, today, upcoming)
    if not data.get("agenda"):
        log_.save(agenda=[asdict(a) for a in agenda])
        await log_.step("agenda", "Research agenda: " + ", ".join(a.label for a in agenda))

    # 2. queries
    if data.get("queries"):
        queries = [Query(**q) for q in data["queries"]]
    else:
        await log_.step("queries", "Planning searches")
        prompt = (f"{_company_block(ctx)}\n\nToday: {today.isoformat()}\nResearch themes (key: what to look for):\n"
                  + "\n".join(f"- {a.theme}: {a.label} — {a.focus}" + (f" (also: {', '.join(a.keywords)})" if a.keywords else "")
                              for a in agenda)
                  + f"\n\nWrite {depth.queries_per_theme} queries per theme.")
        raw = await ai_json(log_, "plan", prompt, QUERIES_SYSTEM, depth.max_llm_calls, max_tokens=1500)
        queries = normalize_queries(raw, agenda, depth.queries_per_theme, today.year)
        log_.save(queries=[asdict(q) for q in queries])
        await log_.step("queries", f"Planned {len(queries)} searches")

    # 3. search
    search_ok = True
    if "hits" in data:
        hits = data["hits"]
        search_ok = bool(data.get("search_ok", True))
    else:
        hits_map, errors = await search_queries(session, queries, cfg, depth, ctx.own_domains, log_)
        search_ok = not (errors and errors[0] == "all searches failed")
        seen = await _seen_source_keys(session, run.id)
        hits = rank_hits(hits_map, cfg, seen)[:max(depth.pages * 2, 10)]
        log_.save(hits=hits, search_ok=search_ok)
        log_.stat(searches=len(queries), results=len(hits_map), search_errors=len(errors))
        if not search_ok:
            await log_.step("search", NO_SEARCH_HINT + " (" + "; ".join(e for e in errors[1:2])[:200] + ")", "warn")
        else:
            await log_.step("search", f"Found {len(hits_map)} unique sources")

    # 4. read
    if "sources" in data:
        sources = data["sources"]
    else:
        top = hits[:depth.pages]
        sources = []
        if top:
            await log_.step("read", f"Reading the top {len(top)} pages")
            pages = await Fetcher().read_many([h["url"] for h in top])
            sources = [page_record(h, pages.get(h["url"])) for h in top]
            for s in sources:
                session.add(ResearchSource(organization_id=org_id, run_id=run.id, url=s["url"], url_key=s["key"],
                                           title=s["title"], publisher=s["publisher"], published_at=s["date"],
                                           snippet=s["snippet"], theme=s["theme"], query=s["query"], fetched=s["fetched"],
                                           excerpt=s["excerpt"][:MAX_PAGE_CHARS]))
            log_.stat(pages_read=sum(1 for s in sources if s["fetched"]))
            await log_.step("read", f"Read {sum(1 for s in sources if s['fetched'])} pages "
                            f"({len(sources) - sum(1 for s in sources if s['fetched'])} used from search snippets)")
        log_.save(sources=sources)

    # 5. facts
    if "facts" in data:
        facts = data["facts"]
    else:
        facts = await extract_facts(log_, sources, depth) if sources else []
        if not facts:
            facts = await knowledge_facts(session, ctx, agenda)
        log_.save(facts=facts)
        log_.stat(facts=len(facts))
        await log_.step("facts", f"Collected {len(facts)} facts with sources")

    # occasion dates: check variable ones online before writing about them
    if search_ok and upcoming and not data.get("occasions_checked"):
        if await verify_occasions(session, row, upcoming, log_, depth.max_llm_calls):
            upcoming = occasions.upcoming(today, occ_cfg, verified_dates(row))
        log_.save(occasions_checked=True)

    fact_ids = {f"F{i + 1}": f for i, f in enumerate(facts)}
    theme_keys = {a.theme for a in agenda} | {t["key"] for t in THEMES}

    # 6. ideas
    if "candidates" in data:
        candidates = data["candidates"]
    else:
        await log_.step("ideas", "Turning facts into content ideas")
        fact_block = "\n".join(f"{k}: {f['fact']} ({f['publisher']}{', ' + f['date'] if f.get('date') else ''}) [theme: {f.get('theme')}]"
                               for k, f in list(fact_ids.items())[:60])
        prompt = "\n\n".join(x for x in [
            _company_block(ctx), f"Today: {today.isoformat()}",
            "Themes: " + ", ".join(f"{a.theme} ({a.label})" for a in agenda),
            f"Facts:\n{fact_block}" if fact_block else "No web facts today: use the company facts and evergreen expertise.",
            _avoid_block(ctx), f"Propose {depth.candidates} ideas across different themes.",
        ] if x)
        raw = await ai_json(log_, "plan", prompt, IDEAS_SYSTEM, depth.max_llm_calls, max_tokens=5000)
        rows = _d(raw).get("ideas") if isinstance(raw, dict) else raw
        candidates = []
        for r in rows if isinstance(rows, list) else []:
            idea = normalize_idea(r, fact_ids, ctx, theme_keys) if isinstance(r, dict) else None
            if idea and not excluded(idea, cfg.get("excluded_topics") or []):
                candidates.append(idea)
        log_.save(candidates=candidates)
        await log_.step("ideas", f"Drafted {len(candidates)} idea candidates")

    # 7. critique and score
    if candidates and "scored" not in data:
        await log_.step("critique", "Scoring ideas for relevance, timeliness, novelty and evidence")
        listing = "\n".join(f"{i + 1}. {c['title']} — {c['angle']} (facts cited: {len(c['facts'])}; "
                            f"sources: {', '.join(sorted({f['publisher'] for f in c['facts'] if f.get('publisher')}))[:120]})"
                            for i, c in enumerate(candidates))
        raw = await ai_json(log_, "qa", f"{_company_block(ctx)[:2500]}\n\nToday: {today.isoformat()}\n\nIdeas:\n{listing}",
                            CRITIQUE_SYSTEM, depth.max_llm_calls, max_tokens=2500)
        by_i = {}
        for r in (_d(raw).get("scores") if isinstance(raw, dict) else raw) or []:
            if isinstance(r, dict):
                try:
                    by_i[int(r.get("i"))] = r
                except (TypeError, ValueError):
                    continue
        past_vecs = [(list(v), m) for v, m in (await session.execute(
            select(IdeaBrief.embedding, IdeaBrief.embedding_model).where(
                IdeaBrief.embedding.is_not(None), IdeaBrief.run_date >= (today - timedelta(days=NOVELTY_LOOKBACK_DAYS)).isoformat())
        )).all() if v is not None]
        novelty, vecs, model = await novelty_scores(session, [f"{c['title']}. {c['angle']}" for c in candidates],
                                                    [*ctx.past_briefs, *ctx.recent_topics], past_vecs)
        for i, c in enumerate(candidates):
            r = by_i.get(i + 1, {})
            ev = evidence_score(c["facts"], today)
            sc = {"relevance": _f01(r.get("relevance")), "timeliness": _f01(r.get("timeliness")),
                  "business_value": _f01(r.get("business_value")),
                  "evidence": round((ev + _f01(r.get("evidence"), ev)) / 2, 3), "novelty": novelty[i]}
            c["scores"] = sc
            c["score"] = combine(sc)
            c["reason"] = _s(r.get("reason"), 300)
            if vecs and model:
                c["_vec"], c["_model"] = vecs[i], model
        log_.save(scored=True, candidates=[{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates])
        data_vecs = {i: (c.get("_vec"), c.get("_model")) for i, c in enumerate(candidates)}
    else:
        data_vecs = {}
        for c in candidates:
            c.setdefault("scores", {k: 0.5 for k in WEIGHTS})
            c.setdefault("score", combine(c["scores"]))

    n = max(1, min(int(cfg.get("ideas_per_day") or 8), 30))
    past_titles = {normalize_name(t) for t in [*ctx.past_briefs, *ctx.recent_topics]}
    fresh = [c for c in candidates if normalize_name(c["title"]) not in past_titles
             and c.get("scores", {}).get("novelty", 1) >= MIN_NOVELTY]
    if len(fresh) < len(candidates):
        await log_.step("critique", f"Dropped {len(candidates) - len(fresh)} ideas that repeat recent briefs or posts")
    chosen = select_top(fresh, n)

    # 8. deep follow-up: strengthen the evidence for the most promising ideas
    if depth.follow_up_ideas and search_ok and chosen and not data.get("followed_up"):
        await log_.step("follow_up", f"Digging deeper into the top {min(depth.follow_up_ideas, len(chosen))} ideas")
        known = {s["key"] for s in sources}
        for idea in chosen[:depth.follow_up_ideas]:
            qs = [idea["article"]["keyword"], f"{idea['title']} statistics {today.year}"]
            hits_map, _ = await search_queries(session, [Query(idea["theme"], q, "year") for q in qs], cfg, depth,
                                               ctx.own_domains, log_)
            fresh = [h for h in rank_hits(hits_map, cfg, known) if h["key"] not in known][:2]
            if not fresh:
                continue
            pages = await Fetcher().read_many([h["url"] for h in fresh])
            more = [page_record(h, pages.get(h["url"])) for h in fresh]
            known |= {s["key"] for s in more}
            new_facts = await extract_facts(log_, more, depth, "follow_up")
            have = {normalize_name(f["fact"]) for f in idea["facts"]}
            for f in new_facts:
                if len(idea["facts"]) >= MAX_FACTS_PER_BRIEF:
                    break
                if normalize_name(f["fact"]) not in have:
                    idea["facts"].append({k: f.get(k) for k in ("fact", "source_url", "publisher", "date", "title")})
            idea["scores"]["evidence"] = max(idea["scores"]["evidence"], evidence_score(idea["facts"], today))
            idea["score"] = combine(idea["scores"])
            idea["followed_up"] = True
        log_.save(followed_up=True)

    # occasion briefs (once per occasion and year)
    occasion_ideas: list[dict] = []
    if upcoming and "occasion_ideas" not in data:
        have = await existing_occasion_ids(session)
        todo = [o for o in upcoming if o.id not in have][:4]
        if todo:
            await log_.step("occasions", "Planning content for " + ", ".join(f"{o.name} ({o.date.strftime('%d %b')})" for o in todo))
            block = "\n".join(f"- id {o.id}: {o.name} on {o.date.isoformat()} ({occasions.CATEGORIES[o.category]}; "
                              f"regions: {', '.join(o.regions) or 'all India'}). Tone: {occasions.TONE_GUIDANCE[o.tone]} "
                              f"Ideas to consider: {o.hint}" for o in todo)
            raw = await ai_json(log_, "plan", f"{_company_block(ctx)[:3000]}\n\nOccasions:\n{block}", OCCASIONS_SYSTEM,
                                depth.max_llm_calls, max_tokens=2500)
            by_id = {o.id: o for o in todo}
            for r in (_d(raw).get("ideas") if isinstance(raw, dict) else raw) or []:
                if not isinstance(r, dict):
                    continue
                o = by_id.pop(_s(r.get("occasion_id"), 60), None)
                if o is None:
                    continue
                idea = normalize_idea({**r, "theme": "events", "fact_ids": []}, {}, ctx, theme_keys | {"events"})
                if idea is None:
                    continue
                idea["facts"] = [{"fact": f"{o.name} falls on {o.date.strftime('%A, %d %B %Y')}"
                                  + ("" if o.confidence in ("official", "fixed", "rule", "verified", "season") else
                                     f" (date {o.confidence}; confirm closer to the day)"),
                                  "source_url": o.source if o.source.startswith("http") else None,
                                  "publisher": "Occasions calendar" if not o.source.startswith("http") else publisher_of(o.source),
                                  "date": o.date.isoformat(), "title": o.source}]
                idea["occasion"] = o.brief_ref()
                idea["why_now"] = idea["why_now"] or f"{o.name} is on {o.date.strftime('%d %b')}."
                idea["greeting"] = _s(r.get("greeting"), 300)
                days_left = (o.date - today).days
                idea["scores"] = {"relevance": 0.7, "timeliness": round(max(0.4, 1 - days_left / 30), 3), "novelty": 0.9,
                                  "business_value": 0.6 if o.tone in ("business", "awareness") else 0.5,
                                  "evidence": 1.0 if o.confidence in ("official", "fixed", "verified") else 0.6}
                idea["score"] = combine(idea["scores"])
                occasion_ideas.append(idea)
        log_.save(occasion_ideas=occasion_ideas)
    else:
        occasion_ideas = data.get("occasion_ideas") or []

    # 9. save
    await log_.step("save", "Saving today's briefs")
    saved: list[IdeaBrief] = []
    vec_by_title = {candidates[i]["title"]: v for i, v in data_vecs.items() if v[0] is not None}
    for idea in [*chosen, *occasion_ideas]:
        vec, model = vec_by_title.get(idea["title"], (None, None))
        b = IdeaBrief(
            organization_id=org_id, run_id=run.id, run_date=run.run_date, title=idea["title"], angle=idea["angle"] or None,
            why_now=idea["why_now"] or None, theme=idea["theme"], audience=idea["audience"] or None, facts=idea["facts"],
            formats=idea["formats"], article=idea["article"], cta=idea["cta"], hashtags=idea["hashtags"],
            scores=idea["scores"], score=idea["score"], status="new", occasion=idea.get("occasion"),
            pillar_id=uuid.UUID(idea["pillar_id"]) if idea.get("pillar_id") else None,
            data={k: idea[k] for k in ("reason", "greeting", "followed_up") if idea.get(k)},
            embedding=vec, embedding_model=model,
        )
        session.add(b)
        saved.append(b)
    await session.flush()

    added_topics = 0
    auto = cfg["auto_topics"]
    if auto.get("enabled"):
        for b in sorted(saved, key=lambda x: -x.score):
            if added_topics >= int(auto.get("max_per_day") or 3) or b.score < float(auto.get("min_score") or 0.75):
                continue
            await add_to_topics(session, b, auto=True)
            added_topics += 1

    run.status = "done" if search_ok else "partial"
    run.finished_at = now()
    log_.stat(briefs=len(saved), occasion_briefs=len(occasion_ideas), topics_added=added_topics,
              duration_s=round((run.finished_at - run.started_at).total_seconds()) if run.started_at else None)
    await log_.step("done", f"{len(saved)} idea briefs ready" + (f"; {added_topics} added to the planner's topics" if added_topics else ""))
    return dict(run.stats or {})


# ------------------------------------------------------------------ integrations
def brief_text(b: IdeaBrief, *, max_chars: int = 3900) -> str:
    """Grounded brief for the post composer / planner: angle, why now, facts with sources."""
    lines = [b.title]
    if b.angle:
        lines.append(f"Angle: {b.angle}")
    if b.why_now:
        lines.append(f"Why now: {b.why_now}")
    if b.audience:
        lines.append(f"Audience: {b.audience}")
    if b.occasion:
        o = _d(b.occasion)
        lines.append(f"Occasion: {o.get('name')} on {o.get('date')}. Tone: {occasions.TONE_GUIDANCE.get(o.get('tone') or '', '')}")
        if _d(b.data).get("greeting"):
            lines.append(f"Greeting idea: {b.data['greeting']}")
    if b.facts:
        lines.append("Facts to use (cite the source names, do not invent numbers):")
        for f in b.facts[:MAX_FACTS_PER_BRIEF]:
            src = ", ".join(x for x in (f.get("publisher"), f.get("date")) if x)
            lines.append(f"- {f.get('fact')}" + (f" ({src})" if src else "") + (f" {f['source_url']}" if f.get("source_url") else ""))
    cta = _d(b.cta)
    if cta.get("text"):
        lines.append(f"Call to action: {cta['text']}" + (f" {cta['url']}" if cta.get("url") else ""))
    if b.hashtags:
        lines.append("Hashtags: " + " ".join(b.hashtags))
    return "\n".join(lines)[:max_chars]


def mark_used(b: IdeaBrief, kind: str, ref_id: uuid.UUID | str | None) -> None:
    b.used = [*(b.used or []), {"kind": kind, "id": str(ref_id) if ref_id else None, "at": now().isoformat()}]
    if kind in ("post", "article"):
        b.status = "used"
    elif b.status == "new":
        b.status = "saved"


async def add_to_topics(session: AsyncSession, b: IdeaBrief, *, auto: bool = False) -> Opportunity:
    """Feed the planner: an Opportunity the scheduler's next_opportunity() picks for auto-generated posts."""
    if b.opportunity_id:
        opp = await session.get(Opportunity, b.opportunity_id)
        if opp is not None:
            if opp.status == "dismissed":  # added again by hand: make it available to the planner again
                opp.status = "new"
            if b.status in ("new", "dismissed"):
                b.status = "saved"
            return opp
    opp = Opportunity(
        organization_id=b.organization_id, title=b.title[:300], angle=b.angle, pillar_id=b.pillar_id, source="trend",
        score=b.score, status="new",
        data={"research_brief_id": str(b.id), "brief": brief_text(b, max_chars=3000), "occasion": b.occasion,
              "keywords": [b.article.get("keyword")] if _d(b.article).get("keyword") else [],
              "formats": [f.get("format") for f in b.formats or []], "generated_at": now().isoformat(), "auto": auto},
    )
    session.add(opp)
    await session.flush()
    b.opportunity_id = opp.id
    mark_used(b, "topic", opp.id)
    return opp


async def sync_used_from_topics(session: AsyncSession) -> None:
    """Briefs whose planner topic was used by an auto-generated post become 'used'."""
    rows = (await session.execute(
        select(IdeaBrief, Opportunity.status).join(Opportunity, Opportunity.id == IdeaBrief.opportunity_id)
        .where(IdeaBrief.status.in_(("new", "saved")), Opportunity.status == "used")
    )).all()
    for b, _ in rows:
        b.status = "used"


def local_today(org: Organization | None, at: datetime | None = None) -> date:
    return (at or now()).astimezone(org_tz(org)).date()


async def brief_counts_by_day(session: AsyncSession, limit: int = 30) -> list[dict]:
    rows = (await session.execute(
        select(IdeaBrief.run_date, func.count()).group_by(IdeaBrief.run_date).order_by(IdeaBrief.run_date.desc()).limit(limit)
    )).all()
    return [{"date": d, "count": c} for d, c in rows]

