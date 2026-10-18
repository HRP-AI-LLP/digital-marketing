"""AI search visibility: does the company show up when buyers ask AI assistants?

Pipeline for one run (job `aivis.run`):
  1. ask every tracked prompt to every chosen answer engine (app/services/aivis_engines.py), one short call each
  2. analyse each answer: rules first (brand name / aliases / domain, competitor names and domains, list position,
     cited URLs and whether they are ours), then one LLM call per prompt to pick out other brands and the tone
     used about us (skipped quietly when no AI model is available)
  3. compute metrics (visibility score, share of voice, average position, sentiment, cited domains, missed prompts)
     and store them on the run so trends compare runs
  4. write recommendations from the gaps (LLM, grounded in the numbers; simple rules when no AI is available)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AivisAnswer, AivisPrompt, AivisRun, AivisSettings, Competitor, KnowledgeDocument, Organization
from app.services import aivis_engines as engines_svc
from app.services import knowledge
from app.services.crawler import domain_of, registrable_domain
from app.services.llm import router as llm_router
from app.services.llm.router import NoProviderError

log = logging.getLogger(__name__)

MAX_PROMPTS = 50
MAX_ALIASES = 15
NO_AI = ("Add a free AI text model in Settings → Integrations first (for example a free Gemini or Groq key, or Ollama on "
         "this computer). Paid models are optional.")
NO_PROMPTS = "Add at least one prompt to track first."
NO_ENGINES = "Choose at least one answer engine in AI visibility → Settings."

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "weekday": 0,  # Monday
    "run_time": "09:00",
    "engines": [],  # [] = automatic (one web-grounded engine per capable integration)
    "aliases": [],
    "own_domains": [],
    "drop_alert_points": 10,
    "llm_analysis": True,
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

Progress = Callable[[str], Awaitable[None]]


def now() -> datetime:
    return datetime.now(UTC)


def org_tz(org: Organization | None) -> ZoneInfo:
    try:
        return ZoneInfo(org.timezone if org and org.timezone else "Asia/Kolkata")
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


# ------------------------------------------------------------------ settings
async def get_settings_row(session: AsyncSession, org_id: uuid.UUID) -> AivisSettings | None:
    return await session.scalar(select(AivisSettings).where(AivisSettings.organization_id == org_id))


async def ensure_settings_row(session: AsyncSession, org_id: uuid.UUID) -> AivisSettings:
    row = await get_settings_row(session, org_id)
    if row is None:
        row = AivisSettings(organization_id=org_id, config={}, data={})
        session.add(row)
        await session.flush()
    return row


def effective_config(row: AivisSettings | None) -> dict:
    cfg = {**DEFAULTS, **((row.config if row else None) or {})}
    cfg["engines"] = list(cfg.get("engines") or [])
    cfg["aliases"] = list(cfg.get("aliases") or [])
    cfg["own_domains"] = list(cfg.get("own_domains") or [])
    return cfg


# ------------------------------------------------------------------ brands and matching
LEGAL = re.compile(r"[,\s]+(llp|l\.l\.p\.|pvt\.?\s*ltd\.?|private\s+limited|ltd\.?|limited|inc\.?|llc|corp\.?|"
                   r"corporation|co\.|gmbh|plc|s\.?a\.?|pte\.?\s*ltd\.?)$", re.I)


def clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    prev = None
    while prev != name:
        prev, name = name, LEGAL.sub("", name).strip()
    return name or prev or ""


GENERIC = re.compile(r"(\s+(technologies|technology|tech|software|solutions|systems|labs|group|india|services|global|"
                     r"hq|online|digital|consulting|ai))+$", re.I)


def short_name(name: str) -> str:
    """'Keka Technologies' -> 'Keka' (answers usually use the short brand). Empty when nothing distinctive is left."""
    s = GENERIC.sub("", name or "").strip()
    return s if s != name and len(s) >= 3 else ""


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


@dataclass
class Brand:
    name: str
    terms: list[str]
    is_us: bool = False
    tracked: bool = False  # from the Competitors list
    domain: str = ""
    patterns: list[re.Pattern] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen, pats = set(), []
        for t in self.terms:
            t = (t or "").strip()
            if len(t) < 3 or norm(t) in seen:
                continue
            seen.add(norm(t))
            body = r"\s+".join(re.escape(w) for w in t.split())
            pats.append(re.compile(rf"(?<![\w]){body}(?![\w])", re.I))
        self.patterns = pats

    def first(self, text: str) -> int | None:
        hits = [m.start() for p in self.patterns if (m := p.search(text or ""))]
        return min(hits) if hits else None


def _domain_terms(domain: str) -> list[str]:
    return [domain] if domain and "." in domain else []


async def brand_set(session: AsyncSession, cfg: dict | None = None) -> tuple[Brand, list[Brand], set[str]]:
    """(us, tracked competitors, our registrable domains)."""
    org_id = session.info.get("org_id")
    org = await session.get(Organization, org_id) if org_id else None
    cfg = cfg if cfg is not None else effective_config(await get_settings_row(session, org_id))
    own_domains = {registrable_domain(d.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0])
                   for d in cfg["own_domains"] if d and d.strip()}
    if org and org.website:
        own_domains.add(domain_of(org.website if "://" in org.website else f"https://{org.website}"))
    own_domains.discard("")
    name = clean_name(org.name if org else "") or (org.name if org else "Us")
    terms = [name, org.name if org else "", org.legal_name if org and org.legal_name else "", *cfg["aliases"]]
    for d in own_domains:
        terms += _domain_terms(d)
    us = Brand(name, [t for t in terms if t], is_us=True, domain=next(iter(sorted(own_domains)), ""))
    comps = []
    for c in (await session.scalars(select(Competitor).where(Competitor.status != "rejected")
                                    .order_by(Competitor.overlap_score.desc()))).all():
        cname = clean_name(c.name) or c.name
        if not cname or norm(cname) == norm(us.name):
            continue
        aliases = [str(a) for a in ((c.data or {}).get("aliases") or []) if isinstance(a, str)]
        comps.append(Brand(cname, [cname, c.name, short_name(cname), *aliases, *_domain_terms(c.domain)], tracked=True,
                           domain=c.domain or ""))
    return us, comps[:30], own_domains


LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•]|#{2,4})\s+", re.M)


def brand_order(text: str, brands: list[Brand]) -> list[Brand]:
    """Brands in the order the answer presents them. For list answers, the list order counts (an intro sentence
    naming a brand doesn't put it first)."""
    items = list(LIST_ITEM.finditer(text or ""))
    start = items[0].start() if len(items) >= 2 else 0
    listed = (text or "")[start:]
    scored = []
    for b in brands:
        pos = b.first(listed)
        if pos is not None:
            scored.append((0, pos, b))
        else:
            pos = b.first(text)
            if pos is not None:
                scored.append((1, pos, b))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [b for _, _, b in scored]


POSITIVE = ("best", "leading", "excellent", "strong", "popular", "recommended", "top", "great", "reliable", "robust",
            "user-friendly", "easy to use", "affordable", "comprehensive", "trusted", "standout", "well-regarded",
            "praised", "innovative", "ideal", "good choice", "highly rated")
NEGATIVE = ("expensive", "limited", "lacks", "lacking", "poor", "complaints", "outdated", "difficult", "issues",
            "slow", "drawback", "downside", "not recommended", "avoid", "clunky", "buggy", "weak", "costly", "concerns")


def snippet(text: str, pos: int | None, width: int = 220) -> str:
    if pos is None:
        return ""
    a, b = max(0, pos - width // 2), min(len(text), pos + width)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def rule_sentiment(snip: str) -> str:
    s = snip.lower()
    pos = sum(1 for w in POSITIVE if w in s)
    neg = sum(1 for w in NEGATIVE if w in s)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def tag_citations(cites: list[dict], own: set[str]) -> list[dict]:
    out = []
    for c in cites:
        d = domain_of(c.get("url") or "")
        title = c.get("title")
        # Gemini cites through a redirect host and puts the real site in the title.
        if d in ("vertexaisearch.cloud.google.com", "google.com") and title and re.fullmatch(r"[\w.-]+\.[a-z]{2,}", title.strip(), re.I):
            d = registrable_domain(title.strip().lower())
        if not d:
            continue
        out.append({"url": c["url"], "title": title, "domain": d, "own": d in own})
    return out


@dataclass
class Analysis:
    mentioned: bool
    position: int | None
    sentiment: str | None
    competitors: list[str]
    brands: list[str]
    citations: list[dict]
    own_cited: bool
    snippet: str
    method: str = "rules"


def analyse(text: str, api_cites: list[dict], us: Brand, comps: list[Brand], own: set[str],
            extra_brands: list[str] | None = None) -> Analysis:
    """Rules-based analysis. `extra_brands` (from the LLM) adds brands that aren't on the Competitors list."""
    cites = tag_citations(api_cites or engines_svc.urls_in_text(text), own)
    known = {norm(b.name) for b in [us, *comps]}
    others = []
    for name in extra_brands or []:
        n = clean_name(str(name))[:120]
        if not n or norm(n) in known or any(b.first(n) is not None for b in [us, *comps]):
            continue
        known.add(norm(n))
        others.append(Brand(n, [n]))
    order = brand_order(text, [us, *comps, *others])
    first_us = us.first(text)
    mentioned = first_us is not None
    position = next((i + 1 for i, b in enumerate(order) if b.is_us), None) if mentioned else None
    snip = snippet(text, first_us)
    return Analysis(
        mentioned=mentioned, position=position, sentiment=rule_sentiment(snip) if mentioned else None,
        competitors=[b.name for b in order if b.tracked], brands=[b.name for b in order],
        citations=cites, own_cited=any(c["own"] for c in cites), snippet=snip,
    )


EXTRACT_SYSTEM = (
    "You read answers that AI assistants gave to a buyer's question and list the companies, products or brands each "
    "answer recommends or names, in the order they appear. Use the names exactly as written in the answer. Do not add "
    "brands that are not in the answer. For answers that mention the company named as 'our company', also judge the "
    "tone the answer uses about it: positive, neutral or negative, with a short reason quoting the answer.\n"
    'Return JSON: {"answers": [{"id": "A1", "brands": ["..."], "sentiment": "positive|neutral|negative|null", '
    '"reason": "..."}]}'
)


async def llm_extract(session: AsyncSession, us: Brand, items: list[tuple[str, str, bool]]) -> dict[str, dict]:
    """items: [(id, answer text, mentions us)] -> {id: {brands, sentiment, reason}}. Empty dict when AI isn't available."""
    if not items:
        return {}
    blocks = []
    for aid, text, ours in items:
        blocks.append(f"[{aid}] (mentions our company: {'yes' if ours else 'no'})\n{text[:2500]}")
    prompt = f"Our company: {us.name}\n\n" + "\n\n".join(blocks)
    try:
        data = await llm_router.complete_json(session, "extract", prompt, system=EXTRACT_SYSTEM, temperature=0.1,
                                              max_tokens=1200)
    except Exception as e:  # noqa: BLE001 - analysis falls back to rules
        log.info("aivis extraction skipped: %s", type(e).__name__)
        return {}
    out = {}
    rows = data.get("answers") if isinstance(data, dict) else None
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        brands = [str(b)[:120] for b in (r.get("brands") or []) if isinstance(b, str) and b.strip()][:25]
        sent = r.get("sentiment") if r.get("sentiment") in ("positive", "neutral", "negative") else None
        out[str(r["id"])] = {"brands": brands, "sentiment": sent, "reason": str(r.get("reason") or "")[:300]}
    return out


# ------------------------------------------------------------------ prompts
def prompt_key(text: str) -> str:
    return norm(text)


def clean_prompt(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:300]


def clean_topic(topic: str | None) -> str:
    t = re.sub(r"\s+", " ", (topic or "").strip())[:60]
    return t[:1].upper() + t[1:] if t else "General"


SUGGEST_SYSTEM = (
    "You suggest the questions real buyers type into AI assistants (ChatGPT, Perplexity, Gemini, Claude) when they "
    "look for what this company sells. Write them the way a buyer would ask: mostly unbranded category questions "
    "(\"best ... for ...\", \"... vs ...\", \"how to choose ...\", \"top ... in <region>\"), a few comparisons with the "
    "named competitors, and at most two that name the company itself. Base every question on the company facts "
    "given; never invent products, prices or customers. Group them into short topics (2-4 words).\n"
    'Return JSON: {"prompts": [{"text": "...", "topic": "...", "reason": "why a buyer asks this"}]}'
)


async def suggest_prompts(session: AsyncSession, n: int = 15) -> list[dict]:
    us, comps, _ = await brand_set(session)
    ctx = await knowledge.company_context(session, max_chars=3000)
    existing = (await session.scalars(select(AivisPrompt.text))).all()
    have = {prompt_key(t) for t in existing}
    prompt = (f"Company facts:\n{ctx or us.name}\n\n"
              f"Competitors: {', '.join(c.name for c in comps[:12]) or 'none listed'}\n\n"
              f"Already tracked (don't repeat):\n" + ("\n".join(f"- {t}" for t in existing[:60]) or "- none") +
              f"\n\nSuggest {n} new questions.")
    data = await llm_router.complete_json(session, "plan", prompt, system=SUGGEST_SYSTEM, temperature=0.6, max_tokens=2000)
    out = []
    for r in (data.get("prompts") if isinstance(data, dict) else None) or []:
        if isinstance(r, str):
            r = {"text": r}
        if not isinstance(r, dict):
            continue
        text = clean_prompt(str(r.get("text") or ""))
        k = prompt_key(text)
        if len(text) < 8 or k in have:
            continue
        have.add(k)
        out.append({"text": text, "topic": clean_topic(str(r.get("topic") or "")), "reason": str(r.get("reason") or "")[:300]})
    return out[:n]


# ------------------------------------------------------------------ metrics
def _pct(a: int, b: int) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


def compute_metrics(answers: list[AivisAnswer], us_name: str, tracked: list[str]) -> dict:
    ok = [a for a in answers if a.status == "ok"]
    mentioned = [a for a in ok if a.mentioned]
    by_engine: dict[str, dict] = {}
    by_topic: dict[str, dict] = {}
    for a in ok:
        e = by_engine.setdefault(a.engine_key, {"key": a.engine_key, "label": a.engine_label, "answers": 0, "mentioned": 0})
        e["answers"] += 1
        e["mentioned"] += int(a.mentioned)
        t = by_topic.setdefault(a.topic or "General", {"topic": a.topic or "General", "answers": 0, "mentioned": 0})
        t["answers"] += 1
        t["mentioned"] += int(a.mentioned)
    for d in (*by_engine.values(), *by_topic.values()):
        d["visibility"] = _pct(d["mentioned"], d["answers"])

    # Share of voice: each answer counts each brand once.
    counts: Counter = Counter()
    names: dict[str, str] = {}
    positions: dict[str, list[int]] = defaultdict(list)
    tracked_keys = {norm(t) for t in tracked}
    for a in ok:
        seen = set()
        for i, b in enumerate(a.brands or []):
            k = norm(b)
            if not k or k in seen:
                continue
            seen.add(k)
            names.setdefault(k, b)
            counts[k] += 1
            positions[k].append(i + 1)
    us_key = norm(us_name)
    names[us_key] = us_name
    counts[us_key] = len(mentioned)  # rules decide whether we're mentioned
    total = sum(counts.values())
    sov = []
    for k, c in counts.most_common():
        if c == 0 and k != us_key:
            continue
        pos = positions.get(k) or []
        sov.append({"name": names[k], "mentions": c, "share": _pct(c, total), "visibility": _pct(c, len(ok)),
                    "avg_position": round(sum(pos) / len(pos), 2) if pos else None,
                    "is_us": k == us_key, "tracked": k in tracked_keys})
    sov = sorted(sov, key=lambda r: (-r["mentions"], not r["is_us"]))
    top = sov[:12]
    if not any(r["is_us"] for r in top):
        top.append(next(r for r in sov if r["is_us"]))

    pos_us = [a.position for a in mentioned if a.position]
    sentiment = Counter(a.sentiment or "neutral" for a in mentioned)
    domains: Counter = Counter()
    own_dom: dict[str, bool] = {}
    for a in ok:
        for d in {c.get("domain") for c in a.citations or [] if c.get("domain")}:
            domains[d] += 1
            own_dom[d] = own_dom.get(d, False) or any(c.get("own") for c in a.citations if c.get("domain") == d)

    # Missed prompts: competitors appear somewhere, we appear nowhere.
    per_prompt: dict[str, dict] = {}
    for a in ok:
        key = str(a.prompt_id or a.prompt_text)
        p = per_prompt.setdefault(key, {"prompt_id": str(a.prompt_id) if a.prompt_id else None, "text": a.prompt_text,
                                        "topic": a.topic, "answers": 0, "mentioned": 0, "competitors": Counter(),
                                        "others": Counter()})
        p["answers"] += 1
        p["mentioned"] += int(a.mentioned)
        for c in a.competitors or []:
            p["competitors"][c] += 1
        for b in a.brands or []:
            if norm(b) != us_key and norm(b) not in tracked_keys:
                p["others"][b] += 1
    missed = []
    for p in per_prompt.values():
        rivals = p["competitors"] + p["others"]
        if p["mentioned"] == 0 and rivals:
            missed.append({"prompt_id": p["prompt_id"], "text": p["text"], "topic": p["topic"], "answers": p["answers"],
                           "competitors": [n for n, _ in rivals.most_common(6)]})
    missed.sort(key=lambda m: -len(m["competitors"]))

    return {
        "visibility": _pct(len(mentioned), len(ok)),
        "answers": len(ok), "failed": len(answers) - len(ok), "mentioned": len(mentioned),
        "prompts": len(per_prompt),
        "share_of_voice": next((r["share"] for r in sov if r["is_us"]), 0.0),
        "avg_position": round(sum(pos_us) / len(pos_us), 2) if pos_us else None,
        "sentiment": {k: sentiment.get(k, 0) for k in ("positive", "neutral", "negative")},
        "own_cited": sum(1 for a in ok if a.own_cited), "own_cited_rate": _pct(sum(1 for a in ok if a.own_cited), len(ok)),
        "engines": list(by_engine.values()), "topics": sorted(by_topic.values(), key=lambda t: t["topic"]),
        "sov": top, "top_domains": [{"domain": d, "answers": c, "own": own_dom.get(d, False)} for d, c in domains.most_common(15)],
        "missed": missed[:25], "missed_count": len(missed),
    }


# ------------------------------------------------------------------ recommendations
REC_SYSTEM = (
    "You are an AI-search (GEO) strategist. From the visibility data below, write practical recommendations that "
    "would make AI assistants mention and cite this company more. Ground every recommendation in the data: name the "
    "prompts, competitors and cited domains it is based on. Typical actions: create or improve a specific page on our "
    "site that answers a missed prompt, get listed or reviewed on a domain AI engines cite often, publish comparison "
    "pages against competitors that win, add facts (pricing pages, FAQs, case studies) the answers lack, fix llms.txt "
    "or robots.txt. Never invent numbers, customers, prices or features.\n"
    'Return JSON: {"recommendations": [{"title": "...", "type": "content|listing|comparison|technical|reputation", '
    '"priority": "high|medium|low", "why": "data-based reason", "actions": ["step", "..."], '
    '"prompts": ["prompt text"], "domains": ["domain"]}]}'
)
REC_TYPES = ("content", "listing", "comparison", "technical", "reputation")


def _rec(title: str, typ: str, priority: str, why: str, actions: list[str], prompts=None, domains=None) -> dict:
    return {"title": title, "type": typ, "priority": priority, "why": why, "actions": actions,
            "prompts": prompts or [], "domains": domains or [], "source": "rules"}


def rule_recommendations(m: dict, site: dict | None) -> list[dict]:
    recs = []
    for p in m.get("missed", [])[:3]:
        recs.append(_rec(f"Answer “{p['text'][:90]}” on your site", "content", "high",
                         f"AI engines named {', '.join(p['competitors'][:3])} for this question but not you.",
                         ["Write a page or article that answers this question directly, with clear facts about your offering.",
                          "Link to it from your main navigation or a related product page."], prompts=[p["text"]]))
    others = [d for d in m.get("top_domains", []) if not d["own"]][:5]
    if others:
        recs.append(_rec("Get listed on the sites AI engines cite", "listing", "medium",
                         "These domains are cited most often in the answers: " + ", ".join(d["domain"] for d in others) + ".",
                         ["Check whether you have a profile, listing or review on each of these sites.",
                          "Create or update it with accurate company and product details."],
                         domains=[d["domain"] for d in others]))
    if m.get("answers") and not m.get("own_cited"):
        recs.append(_rec("Make your own pages citable", "content", "medium", "None of the answers cited your website.",
                         ["Publish clear, fact-rich pages (FAQs, pricing approach, use cases) AI engines can quote.",
                          "Generate an llms.txt on the llms.txt tab and upload it to your site root."]))
    site = site or {}
    blocked = [b["bot"] for b in site.get("bots", []) if b.get("status") == "blocked"]
    if blocked:
        recs.append(_rec("Let AI crawlers read your site", "technical", "high",
                         "robots.txt blocks: " + ", ".join(blocked) + ".",
                         ["Ask whoever manages your website to allow these crawlers in robots.txt, unless you block them on purpose."]))
    if site.get("checked_at") and not (site.get("llms_txt") or {}).get("found"):
        recs.append(_rec("Publish an llms.txt file", "technical", "low", "Your site doesn't serve /llms.txt yet.",
                         ["Generate it on the llms.txt tab, review it, then upload it to your website's root folder."]))
    return recs


async def recommendations(session: AsyncSession, m: dict, site: dict | None) -> list[dict]:
    us, _, own = await brand_set(session)
    pages = (await session.execute(select(KnowledgeDocument.url, KnowledgeDocument.title).limit(40))).all()
    facts = {
        "brand": us.name, "visibility_percent": m.get("visibility"), "share_of_voice_percent": m.get("share_of_voice"),
        "average_position": m.get("avg_position"), "answers": m.get("answers"),
        "sentiment": m.get("sentiment"), "own_site_cited_in_answers": m.get("own_cited"),
        "engines": [{"engine": e["label"], "visibility": e["visibility"]} for e in m.get("engines", [])],
        "topics": [{"topic": t["topic"], "visibility": t["visibility"]} for t in m.get("topics", [])],
        "share_of_voice": [{"brand": r["name"], "share": r["share"], "avg_position": r["avg_position"]} for r in m.get("sov", [])[:10]],
        "missed_prompts": [{"prompt": p["text"], "competitors_named": p["competitors"]} for p in m.get("missed", [])[:12]],
        "top_cited_domains": m.get("top_domains", [])[:12],
        "our_pages": [{"url": u, "title": t} for u, t in pages if u][:30],
        "site_check": {"llms_txt_found": bool(((site or {}).get("llms_txt") or {}).get("found")),
                       "blocked_ai_crawlers": [b["bot"] for b in (site or {}).get("bots", []) if b.get("status") == "blocked"]}
        if (site or {}).get("checked_at") else "not checked",
    }
    try:
        data = await llm_router.complete_json(session, "plan", "Visibility data (JSON):\n" + json.dumps(facts, default=str)[:12000],
                                              system=REC_SYSTEM, temperature=0.4, max_tokens=2200)
    except Exception as e:  # noqa: BLE001
        log.info("aivis recommendations fell back to rules: %s", type(e).__name__)
        return rule_recommendations(m, site)
    out = []
    for r in (data.get("recommendations") if isinstance(data, dict) else None) or []:
        if not isinstance(r, dict) or not str(r.get("title") or "").strip():
            continue
        out.append({
            "title": str(r["title"]).strip()[:200],
            "type": r.get("type") if r.get("type") in REC_TYPES else "content",
            "priority": r.get("priority") if r.get("priority") in ("high", "medium", "low") else "medium",
            "why": str(r.get("why") or "")[:600],
            "actions": [str(a)[:300] for a in (r.get("actions") or []) if isinstance(a, str)][:6],
            "prompts": [str(p)[:300] for p in (r.get("prompts") or []) if isinstance(p, str)][:6],
            "domains": [str(d)[:120] for d in (r.get("domains") or []) if isinstance(d, str)][:8],
            "source": "ai",
        })
    return out[:10] or rule_recommendations(m, site)


# ------------------------------------------------------------------ run pipeline
def _step(run: AivisRun, message: str, level: str = "info") -> None:
    run.steps = [*(run.steps or []), {"at": now().isoformat(), "message": message[:500], "level": level}][-60:]


async def enabled_prompts(session: AsyncSession) -> list[AivisPrompt]:
    return list((await session.scalars(select(AivisPrompt).where(AivisPrompt.enabled.is_(True))
                                       .order_by(AivisPrompt.topic, AivisPrompt.created_at).limit(MAX_PROMPTS))).all())


async def answers_of(session: AsyncSession, run_id: uuid.UUID) -> list[AivisAnswer]:
    return list((await session.scalars(select(AivisAnswer).where(AivisAnswer.run_id == run_id))).all())


async def run_pipeline(session: AsyncSession, run: AivisRun, progress: Progress | None = None) -> dict:
    async def say(msg: str) -> None:
        _step(run, msg)
        if progress:
            try:
                await progress(msg)
            except Exception:  # noqa: BLE001
                pass

    org_id = run.organization_id
    row = await get_settings_row(session, org_id)
    cfg = effective_config(row)
    run.status, run.started_at, run.error = "running", run.started_at or now(), None
    prompts = await enabled_prompts(session)
    engines = [engines_svc.Engine(**e) for e in run.engines or []]
    if not prompts:
        raise ValueError(NO_PROMPTS)
    if not engines:
        raise ValueError(NO_ENGINES)
    cfgs = {str(c.id): c for c in await engines_svc.text_providers(session)}
    us, comps, own = await brand_set(session, cfg)
    await say(f"Asking {len(prompts)} prompts on {len(engines)} engine{'s' if len(engines) != 1 else ''}")
    await session.commit()

    # Resume: keep good answers from an earlier attempt, retry the failed ones.
    await session.execute(delete(AivisAnswer).where(AivisAnswer.run_id == run.id, AivisAnswer.status != "ok"))
    done = {(a.prompt_id, a.engine_key) for a in await answers_of(session, run.id)}

    for i, p in enumerate(prompts, 1):
        todo = [e for e in engines if (p.id, e.key) not in done]
        if not todo:
            continue
        results = await engines_svc.ask_many(session, cfgs, todo, p.text)
        rows: list[tuple[AivisAnswer, engines_svc.EngineAnswer | None]] = []
        for eng, r, err in results:
            a = AivisAnswer(organization_id=org_id, run_id=run.id, prompt_id=p.id, prompt_text=p.text, topic=p.topic,
                            engine_key=eng.key, engine_label=eng.label, provider=eng.provider, model=eng.model, web=eng.web,
                            status="ok" if r else "failed", answer=r.text if r else "", error=err, citations=[], brands=[],
                            competitors=[], analysis={})
            if r:
                a.input_tokens, a.output_tokens = r.input_tokens, r.output_tokens
            rows.append((a, r))
        ok_rows = [(a, r) for a, r in rows if r]
        extracted = {}
        if cfg.get("llm_analysis", True) and ok_rows:
            first = [(f"A{j + 1}", r.text, us.first(r.text) is not None) for j, (_, r) in enumerate(ok_rows)]
            extracted = await llm_extract(session, us, first)
        for j, (a, r) in enumerate(ok_rows):
            x = extracted.get(f"A{j + 1}") or {}
            an = analyse(r.text, r.citations, us, comps, own, x.get("brands"))
            a.mentioned, a.position, a.competitors, a.brands = an.mentioned, an.position, an.competitors, an.brands
            a.citations, a.own_cited = an.citations, an.own_cited
            a.sentiment = (x.get("sentiment") or an.sentiment) if an.mentioned else None
            a.analysis = {"method": "rules+ai" if x else "rules", "snippet": an.snippet,
                          "sentiment_reason": x.get("reason") if an.mentioned else None,
                          "citations_from": "engine" if r.citations else "text", "searches": r.searches}
        for a, _ in rows:
            session.add(a)
        await session.commit()
        if progress and (i % 3 == 0 or i == len(prompts)):
            try:
                await progress(f"Checked {i} of {len(prompts)} prompts")
            except Exception:  # noqa: BLE001
                pass

    answers = await answers_of(session, run.id)
    m = compute_metrics(answers, us.name, [c.name for c in comps])
    run.metrics = m
    ok = m["answers"]
    if not ok:
        errs = Counter(a.error for a in answers if a.error)
        reason = errs.most_common(1)[0][0] if errs else "No engine answered."
        run.status, run.error = "failed", reason
        _step(run, f"No answers: {reason}", "error")
        run.finished_at = now()
        return m
    await say("Writing recommendations")
    site = (row.data or {}).get("site_check") if row else None
    run.recommendations = await recommendations(session, m, site)
    run.status = "partial" if m["failed"] else "done"
    run.finished_at = now()
    _step(run, f"Done: visible in {m['mentioned']} of {ok} answers ({m['visibility']}%)"
          + (f", {m['failed']} engine calls failed" if m["failed"] else ""))
    return m


async def previous_run(session: AsyncSession, run: AivisRun) -> AivisRun | None:
    return (await session.scalars(select(AivisRun).where(
        AivisRun.id != run.id, AivisRun.status.in_(("done", "partial")), AivisRun.created_at < run.created_at)
        .order_by(AivisRun.created_at.desc()).limit(1))).first()


def drop_points(prev: AivisRun | None, run: AivisRun) -> float | None:
    if prev is None or not prev.metrics or not run.metrics:
        return None
    return round(float(prev.metrics.get("visibility") or 0) - float(run.metrics.get("visibility") or 0), 1)


async def start_run(session: AsyncSession, org_id: uuid.UUID, *, trigger: str, run_key: str | None = None,
                    created_by: uuid.UUID | None = None) -> AivisRun:
    """Create a run (with its engines snapshot) and its job in the caller's transaction. Caller commits."""
    from app.services.jobs import enqueue

    cfg = effective_config(await get_settings_row(session, org_id))
    engines = await engines_svc.resolve(session, cfg["engines"])
    if not engines:
        raise ValueError(NO_ENGINES)
    if not await enabled_prompts(session):
        raise ValueError(NO_PROMPTS)
    run = AivisRun(organization_id=org_id, run_key=run_key, trigger=trigger, status="queued",
                   engines=[e.to_dict() for e in engines], metrics={}, recommendations=[], steps=[], created_by=created_by)
    session.add(run)
    await session.flush()
    job = await enqueue(session, "aivis.run", org_id, {"run_id": str(run.id)}, dedupe_key=f"aivis.run:{run.id}",
                        priority=6, max_attempts=2, created_by=created_by)
    run.job_id = job.id
    return run


__all__ = ["NoProviderError"]
