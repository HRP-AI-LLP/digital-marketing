"""llms.txt for the organization's website, plus a check of what the live site already serves.

Format (https://llmstxt.org): an H1 with the site or company name, a blockquote with a short summary, optional
paragraphs, then H2 sections that list links as `- [Title](url): one-line description`. A section called
"Optional" holds links an AI can skip when it is short on context. llms-full.txt carries the page text itself.

The file is built from the company profile and the website pages already crawled on the Company intelligence
page (knowledge documents). Page descriptions come from each page's meta description; pages without one get a
short AI-written line (or the page's first sentence when no AI model is available).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CompanyProfile, KnowledgeDocument, KnowledgeSource, Organization
from app.services import crawler
from app.services.llm import router as llm_router

log = logging.getLogger(__name__)

MAX_PAGES = 120
FULL_MAX_CHARS = 400_000
FULL_PAGE_CHARS = 12_000

AI_BOTS = [
    ("GPTBot", "OpenAI (training)"),
    ("OAI-SearchBot", "ChatGPT search"),
    ("ClaudeBot", "Anthropic Claude"),
    ("PerplexityBot", "Perplexity"),
    ("Google-Extended", "Google Gemini"),
    ("CCBot", "Common Crawl (used by many AI models)"),
]

SECTIONS = [
    ("Products and services", re.compile(r"/(products?|services?|solutions?|platform|features?|studio|offerings?|hrms|software|apps?)(/|$|-)", re.I)),
    ("Pricing", re.compile(r"/(pricing|plans?)(/|$)", re.I)),
    ("Industries and use cases", re.compile(r"/(industr(y|ies)|use-?cases?|sectors?|who-we-serve)(/|$)", re.I)),
    ("Customers and case studies", re.compile(r"/(case-?stud(y|ies)|customers?|clients?|success-stor(y|ies)|testimonials?)(/|$)", re.I)),
    ("Docs and help", re.compile(r"/(docs?|documentation|help|support|faqs?|guides?|knowledge-?base|api)(/|$)", re.I)),
    ("Blog and resources", re.compile(r"/(blog|news|articles?|insights?|resources?|press|media|webinars?|events?)(/|$)", re.I)),
    ("Company", re.compile(r"/(about(-us)?|company|team|careers?|jobs|contact(-us)?|our-story|leadership|partners?)(/|$)", re.I)),
    ("Optional", re.compile(r"/(privacy|terms|cookie|legal|disclaimer|refund|sitemap|login|signin|signup|register|cart)", re.I)),
]
ORDER = [s for s, _ in SECTIONS]


def section_of(url: str) -> str:
    path = urlsplit(url).path or "/"
    if path in ("", "/"):
        return "Main pages"
    for name, rx in SECTIONS:
        if rx.search(path):
            return name
    return "Main pages"


def _one_line(s: str | None, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if len(s) <= n:
        return s
    return s[: n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    m = re.search(r"(.{30,200}?[.!?])(\s|$)", text)
    return _one_line(m.group(1) if m else text[:160])


def _title(doc: KnowledgeDocument) -> str:
    t = re.split(r"\s+[|\-–—]\s+", (doc.title or "").strip())[0].strip()
    if not t or t.startswith("http"):
        path = urlsplit(doc.url or "").path.strip("/").split("/")[-1]
        t = path.replace("-", " ").replace("_", " ").strip().capitalize() or "Home"
    return _one_line(t, 90)


def _md_escape(s: str) -> str:
    return s.replace("[", "(").replace("]", ")")


async def website_pages(session: AsyncSession) -> list[KnowledgeDocument]:
    rows = (await session.scalars(
        select(KnowledgeDocument).join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(KnowledgeSource.kind == "website", KnowledgeDocument.url.is_not(None))
        .order_by(KnowledgeDocument.url).limit(MAX_PAGES * 3))).all()
    seen, out = set(), []
    for d in rows:
        u = crawler.normalize_url(d.url or "")
        if not u or u in seen or crawler.has_skipped_extension(u):
            continue
        seen.add(u)
        out.append(d)
    # Short paths first: they're usually the main pages.
    out.sort(key=lambda d: (len(urlsplit(d.url).path.strip("/").split("/")) if urlsplit(d.url).path.strip("/") else 0, d.url))
    return out[:MAX_PAGES]


DESCRIBE_SYSTEM = ("Write a one-line description (at most 20 words) of each web page for an llms.txt file, based only "
                   "on the page text given. Plain, factual, no marketing hype, no invented facts.\n"
                   'Return JSON: {"pages": [{"id": "P1", "description": "..."}]}')


async def _ai_descriptions(session: AsyncSession, docs: list[KnowledgeDocument]) -> dict[str, str]:
    if not docs:
        return {}
    items = "\n\n".join(f"[P{i + 1}] {d.url}\nTitle: {d.title or ''}\n{(d.text or '')[:700]}" for i, d in enumerate(docs))
    try:
        data = await llm_router.complete_json(session, "extract", items, system=DESCRIBE_SYSTEM, temperature=0.2,
                                              max_tokens=2500)
    except Exception as e:  # noqa: BLE001
        log.info("llms.txt descriptions fell back to first sentences: %s", type(e).__name__)
        return {}
    out = {}
    for r in (data.get("pages") if isinstance(data, dict) else None) or []:
        if isinstance(r, dict) and str(r.get("id", "")).startswith("P"):
            try:
                i = int(str(r["id"])[1:]) - 1
            except ValueError:
                continue
            if 0 <= i < len(docs) and r.get("description"):
                out[docs[i].url] = _one_line(str(r["description"]))
    return out


async def generate(session: AsyncSession, use_ai: bool = True) -> dict:
    org_id = session.info.get("org_id")
    org = await session.get(Organization, org_id) if org_id else None
    profile = await session.scalar(select(CompanyProfile).limit(1))
    docs = await website_pages(session)
    name = (org.name if org else "") or "Our company"
    summary = _one_line((profile.summary if profile else None) or (org.description if org else None) or
                        (org.tagline if org else None) or "", 400)

    need_ai = [d for d in docs if not _one_line((d.meta or {}).get("description"))][:60]
    ai = await _ai_descriptions(session, need_ai) if use_ai else {}
    groups: dict[str, list[str]] = {}
    for d in docs:
        desc = _one_line((d.meta or {}).get("description")) or ai.get(d.url) or _first_sentence(d.text)
        line = f"- [{_md_escape(_title(d))}]({d.url})" + (f": {desc}" if desc else "")
        groups.setdefault(section_of(d.url), []).append(line)

    lines = [f"# {name}", ""]
    if summary:
        lines += [f"> {summary}", ""]
    details = []
    if org and org.tagline and org.tagline not in summary:
        details.append(org.tagline.strip())
    if profile and profile.positioning:
        details.append(_one_line(profile.positioning, 300))
    if profile and profile.geography:
        details.append(f"Serves: {_one_line(profile.geography, 200)}.")
    if org and org.website:
        details.append(f"Website: {org.website}")
    if details:
        lines += [" ".join(details), ""]
    for sec in ["Main pages", *[s for s in ORDER if s != "Optional"], "Optional"]:
        if groups.get(sec):
            lines += [f"## {sec}", "", *groups[sec], ""]
    text = "\n".join(lines).strip() + "\n"

    full = [f"# {name}", ""]
    if summary:
        full += [f"> {summary}", ""]
    size = sum(len(x) for x in full)
    for d in docs:
        if section_of(d.url) == "Optional":
            continue
        block = f"## {_title(d)}\n\nSource: {d.url}\n\n{(d.text or '').strip()[:FULL_PAGE_CHARS]}\n"
        if size + len(block) > FULL_MAX_CHARS:
            break
        full.append(block)
        size += len(block)
    return {"text": text, "full_text": "\n".join(full).strip() + "\n", "pages": len(docs),
            "generated_at": datetime.now(UTC).isoformat(), "ai_descriptions": len(ai)}


LINK = re.compile(r"^\s*-\s*\[[^\]]+\]\([^)]+\)(:\s*.*)?$")


def validate(text: str) -> list[str]:
    """Plain-language problems with an llms.txt (empty = looks right)."""
    issues = []
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    body = [ln for ln in lines if ln.strip()]
    if not body:
        return ["The file is empty."]
    if not body[0].startswith("# "):
        issues.append("The first line should be the company or site name as a heading, like “# Company name”.")
    if sum(1 for ln in body if ln.startswith("# ")) > 1:
        issues.append("Use only one top heading (“# …”). Sections should start with “## ”.")
    if not any(ln.startswith("> ") for ln in body[:6]):
        issues.append("Add a one-line summary right under the name, starting with “> ”.")
    if not any(ln.startswith("## ") for ln in body):
        issues.append("Add at least one section (“## Section name”) with links to your key pages.")
    in_section = False
    for ln in body:
        if ln.startswith("## "):
            in_section = True
            continue
        if in_section and ln.lstrip().startswith("-") and not LINK.match(ln):
            issues.append(f"This list item isn't a link in the form “- [Title](https://…): description”: {ln[:80]}")
            break
    return issues


# ------------------------------------------------------------------ live site check
def site_root(website: str | None) -> str | None:
    u = crawler.normalize_url(website or "")
    if not u:
        return None
    p = urlsplit(u)
    return f"{p.scheme}://{p.netloc}"


def robots_status(robots: str | None, root: str) -> list[dict]:
    out = []
    rp = None
    if robots is not None:
        rp = RobotFileParser()
        rp.parse(robots.splitlines())
    named = {m.lower() for m in re.findall(r"(?im)^\s*user-agent\s*:\s*([^\s#]+)", robots or "")}
    for bot, who in AI_BOTS:
        if rp is None:
            status = "allowed"
        else:
            status = "allowed" if rp.can_fetch(bot, root + "/") else "blocked"
        out.append({"bot": bot, "who": who, "status": status, "named": bot.lower() in named})
    return out


async def _get(client, url: str) -> crawler.Fetched:
    return await crawler.fetch(client, url)


def _looks_like_llms(body: str) -> bool:
    first = next((ln for ln in body.splitlines() if ln.strip()), "")
    return first.startswith("# ")


async def check_site(website: str | None) -> dict:
    root = site_root(website)
    if not root:
        return {"checked_at": datetime.now(UTC).isoformat(), "error": "Add your website address in Organization settings first."}
    async with crawler.make_client() as client:
        llms, full, robots = await asyncio.gather(_get(client, f"{root}/llms.txt"), _get(client, f"{root}/llms-full.txt"),
                                                  _get(client, f"{root}/robots.txt"))

    def present(f: crawler.Fetched) -> dict:
        ok = f.status == 200 and not f.error and not f.is_html and bool(f.body.strip())
        body = f.text() if ok else ""
        return {"found": ok and _looks_like_llms(body), "status": f.status, "error": f.error,
                "served_but_invalid": ok and not _looks_like_llms(body), "preview": body[:1500],
                "issues": validate(body) if ok else []}

    robots_text = robots.text() if robots.status == 200 and not robots.error and not robots.is_html else None
    return {
        "checked_at": datetime.now(UTC).isoformat(), "site": root,
        "llms_txt": present(llms), "llms_full_txt": present(full),
        "robots": {"found": robots_text is not None, "status": robots.status, "error": robots.error},
        "bots": robots_status(robots_text, root),
    }
