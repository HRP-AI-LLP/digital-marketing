"""SEO articles: keyword ideas, the long-form writing pipeline, on-page checks, export and repurposing.

Articles live inside Digital Marketing only (PLAN D11): exports go to
`storage/seo/<org_slug>/<slug>/` as article.mdx, article.html, meta.json and the cover image.

Article-level settings that have no column (search intent, word target, tone, notes, canonical URL,
brief) are kept in the first `outline` item with `kind == "brief"`; see `article_meta`.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import shutil
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.models import (
    AnalyticsSnapshot,
    BrandProfile,
    Competitor,
    KnowledgeDocument,
    KnowledgeSource,
    MediaAsset,
    Opportunity,
    Organization,
    Post,
    PostActivity,
    SeoArticle,
    SocialAccount,
)
from app.services.llm import router as llm_router
from app.services.platforms import PLATFORMS

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]] | None

TASK_LONG = "longform"
TASK_WRITE = "write"
COVER_WAIT_SECONDS = 120  # how long seo.write waits for the media.generate job (tests set 0)
COVER_POLL_SECONDS = 3

INTENT_MIN_WORDS = {"informational": 1200, "commercial": 1000, "transactional": 600, "navigational": 300}
DEFAULT_MIN_WORDS = 1200
STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "how", "what", "is", "are", "vs", "by",
    "your", "you", "at", "from", "do", "does", "can", "why", "best",
}

RULES = (
    "Rules you must follow:\n"
    "- Never invent statistics, percentages, survey results, prices, dates, awards, client names or customer "
    "stories. If a number would help, make the point qualitatively instead (for example 'many teams find…').\n"
    "- Only state facts about the company that appear in the company facts or source excerpts given to you.\n"
    "- Write people-first content: clear, specific, practical, no fluff, no keyword stuffing.\n"
    "- Use plain English, short paragraphs and active voice."
)


# ============================================================ small helpers
def slugify(text: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rsplit("-", 1)[0]
    return s or "article"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _key_tokens(keyword: str) -> list[str]:
    toks = [t for t in _tokens(keyword) if t not in STOPWORDS]
    return toks or _tokens(keyword)


def _stem(t: str) -> str:
    for suf in ("ing", "es", "s", "ed"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def contains_keyword(text: str, keyword: str, variants: list[str] | None = None) -> bool:
    """Exact phrase, any listed variant, or every significant keyword token (loosely stemmed)."""
    t = _norm(text)
    if not keyword:
        return False
    if _norm(keyword) in t:
        return True
    for v in variants or []:
        if v and _norm(v) in t:
            return True
    have = {_stem(x) for x in _tokens(t)}
    need = {_stem(x) for x in _key_tokens(keyword)}
    return bool(need) and need <= have


def article_meta(article) -> dict:
    """Article-level settings stored in outline[0] (kind=brief)."""
    outline = _get(article, "outline") or []
    if outline and isinstance(outline[0], dict) and outline[0].get("kind") == "brief":
        return dict(outline[0])
    return {"kind": "brief"}


def set_article_meta(article: SeoArticle, **values) -> None:
    meta = article_meta(article)
    meta.update({k: v for k, v in values.items()})
    meta["kind"] = "brief"
    rest = [s for s in (article.outline or []) if not (isinstance(s, dict) and s.get("kind") == "brief")]
    article.outline = [meta, *rest]  # new list so SQLAlchemy sees the change


def outline_sections(article) -> list[dict]:
    return [s for s in (_get(article, "outline") or []) if isinstance(s, dict) and s.get("kind") != "brief"]


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def site_domain(url: str | None) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def website_base(org: Organization | None) -> str:
    w = (org.website or "").strip() if org else ""
    if w and "://" not in w:
        w = "https://" + w
    return w.rstrip("/")


def canonical_url(article, org: Organization | None) -> str:
    meta = article_meta(article)
    if meta.get("canonical_url"):
        return meta["canonical_url"]
    if meta.get("live_url"):  # where the article was last published (site_publish)
        return meta["live_url"]
    base = website_base(org)
    slug = _get(article, "slug") or "article"
    return f"{base}/blog/{slug}" if base else f"/blog/{slug}"


# ============================================================ markdown parsing
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def headings(md: str) -> list[tuple[int, str]]:
    out, fence = [], False
    for line in (md or "").splitlines():
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = _HEADING.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
    return out


def links(md: str) -> list[tuple[str, str, bool]]:
    """(text, url, is_image)"""
    return [(m.group(2), m.group(3), m.group(1) == "!") for m in _LINK.finditer(md or "")]


def plain_text(md: str) -> str:
    text = re.sub(r"```.*?```", " ", md or "", flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = _LINK.sub(lambda m: m.group(2), text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", " ", text, flags=re.M)
    text = text.replace("|", " ")
    text = re.sub(r"[*_`~]", "", text)
    return text


def word_count(md: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", plain_text(md)))


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    return [p for p in (x.strip() for x in parts) if len(re.findall(r"\w+", p)) >= 3]


def _syllables(word: str) -> int:
    w = word.lower()
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and n > 1 and not w.endswith(("le", "ee")):
        n -= 1
    return max(n, 1)


def readability(md: str) -> dict:
    body = "\n".join(line for line in (md or "").splitlines() if not line.lstrip().startswith("#"))
    text = plain_text(body)
    sents = _sentences(text)
    words = re.findall(r"[A-Za-z]+", text)
    if not sents or not words:
        return {"avg_sentence_words": 0.0, "flesch": 0.0, "sentences": 0}
    wps = len(words) / len(sents)
    spw = sum(_syllables(w) for w in words) / len(words)
    flesch = 206.835 - 1.015 * wps - 84.6 * spw
    return {"avg_sentence_words": round(wps, 1), "flesch": round(flesch, 1), "sentences": len(sents)}


def split_sections(md: str) -> list[dict]:
    """Top-level blocks: [{level, heading, start, end}] by line index (heading line .. before next same/higher)."""
    lines = (md or "").splitlines()
    heads, fence = [], False
    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            fence = not fence
        if fence:
            continue
        m = _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    out = []
    for idx, (i, lvl, text) in enumerate(heads):
        end = len(lines)
        for j, lvl2, _ in heads[idx + 1:]:
            if lvl2 <= lvl:
                end = j
                break
        out.append({"level": lvl, "heading": text, "start": i, "end": end})
    return out


# ============================================================ SEO checks
def seo_checks(article, site: str | None = None) -> tuple[int, list[dict]]:
    """Score 0–100 and the list of checks [{id, name, ok, detail, weight, severity}].

    `article` is a SeoArticle or a dict with the same field names. `site` is the org website (for
    telling internal links from external ones)."""
    keyword = (_get(article, "keyword") or "").strip()
    variants = list(_get(article, "secondary_keywords") or [])
    title = _get(article, "title") or ""
    body = _get(article, "body_md") or ""
    meta_title = _get(article, "meta_title") or ""
    meta_desc = _get(article, "meta_description") or ""
    slug = _get(article, "slug") or ""
    faq = _get(article, "faq") or []
    cover = _get(article, "cover_media_id")
    meta = article_meta(article)
    domain = site_domain(site)

    hs = headings(body)
    h1s = [t for lvl, t in hs if lvl == 1]
    h2s = [t for lvl, t in hs if lvl == 2]
    lines = body.splitlines()
    h1_idx = next((i for i, line in enumerate(lines) if line.startswith("# ")), -1)
    after_h1 = "\n".join(lines[h1_idx + 1:]) if h1_idx >= 0 else body
    first_words = " ".join(re.findall(r"\S+", plain_text(after_h1))[:100])
    words = word_count(body)
    intent = (meta.get("search_intent") or "informational").lower()
    min_words = int(meta.get("min_words") or INTENT_MIN_WORDS.get(intent, DEFAULT_MIN_WORDS))

    all_links = links(body)
    internal, external, images = [], [], []
    for text, url, is_img in all_links:
        if is_img:
            images.append((text, url))
            continue
        host = site_domain(url) if url.startswith(("http://", "https://")) else None
        if url.startswith("/") or (host and domain and (host == domain or host.endswith("." + domain))):
            internal.append(url)
        elif host:
            external.append(url)

    plain = _norm(plain_text(body))
    occurrences = plain.count(_norm(keyword)) if keyword else 0
    density = (occurrences * 100 / words) if words else 0.0
    read = readability(body)

    # heading hierarchy: one H1, no skipped levels
    skipped = [f"H{a}→H{b}" for (a, _), (b, _) in zip(hs, hs[1:]) if b > a + 1]
    hier_ok = len(h1s) == 1 and not skipped
    if len(h1s) != 1:
        hier_detail = f"Use exactly one H1 (found {len(h1s)})."
    elif skipped:
        hier_detail = "Heading levels skip: " + ", ".join(skipped[:3])
    else:
        hier_detail = "One H1 and headings in order."

    dup_h2 = [h for h, n in Counter(_norm(h) for h in h2s).items() if n > 1]
    key_slug = all(t in slug.split("-") for t in _key_tokens(keyword)) if keyword else False
    h2_kw = any(contains_keyword(h, keyword, variants) for h in h2s)
    mt_len, md_len = len(meta_title), len(meta_desc)
    alt_missing = [u for alt, u in images if not alt.strip()]

    checks: list[dict] = []

    def add(cid, name, ok, detail, weight, severity="error"):
        checks.append({"id": cid, "name": name, "ok": bool(ok), "detail": detail, "weight": weight,
                       "severity": "ok" if ok else severity})

    add("kw_title", "Keyword in title", contains_keyword(title, keyword),
        "Found in the title." if contains_keyword(title, keyword) else f"Add “{keyword}” to the title.", 8)
    h1_ok = bool(h1s) and contains_keyword(h1s[0], keyword)
    add("kw_h1", "Keyword in H1", h1_ok, "Found in the H1." if h1_ok else "Put the keyword in the H1 heading.", 6)
    intro_ok = contains_keyword(first_words, keyword)
    add("kw_intro", "Keyword in the first 100 words", intro_ok,
        "Found early in the article." if intro_ok else "Mention the keyword in the opening paragraph.", 8)
    md_ok = contains_keyword(meta_desc, keyword, variants)
    add("kw_meta", "Keyword in meta description", md_ok,
        "Found in the meta description." if md_ok else "Use the keyword in the meta description.", 6)
    add("kw_slug", "Keyword in URL slug", key_slug,
        f"/{slug}" if key_slug else f"Include the keyword words in the slug (now “{slug or '—'}”).", 5)
    add("kw_h2", "Keyword in a subheading", h2_kw,
        "At least one H2 uses the keyword or a variant." if h2_kw else "Use the keyword or a close variant in one H2.", 6)
    add("meta_title_len", "Meta title length", 30 <= mt_len <= 60,
        f"{mt_len} characters (aim for 30–60).", 6)
    add("meta_desc_len", "Meta description length", 150 <= md_len <= 160,
        f"{md_len} characters (aim for 150–160).", 6)
    add("word_count", "Article length", words >= min_words,
        f"{words} words (at least {min_words} for {intent} intent).", 10)
    add("headings", "Heading structure", hier_ok, hier_detail, 5)
    add("internal_links", "Internal links", len(internal) >= 2,
        f"{len(internal)} link(s) to your own pages (aim for 2 or more).", 7)
    add("external_links", "Authoritative external link", len(external) >= 1,
        f"{len(external)} external link(s)." if external else "Consider citing one authoritative source.", 3, "warn")
    read_ok = 0 < read["avg_sentence_words"] <= 22 and read["flesch"] >= 45
    add("readability", "Readability", read_ok,
        f"Average sentence {read['avg_sentence_words']} words, reading ease {read['flesch']} "
        "(aim for ≤ 22 words and ≥ 45).", 6)
    dens_ok = 0.5 <= density <= 2.5
    add("density", "Keyword density", dens_ok,
        f"{density:.2f}% ({occurrences} uses; aim for 0.5–2.5%).", 6)
    if cover or images:
        alt_ok = not alt_missing
        add("image_alt", "Image alt text", alt_ok,
            "Images have alt text." if alt_ok else f"{len(alt_missing)} image(s) without alt text.", 3)
    else:
        add("image_alt", "Image alt text", True, "No images yet. A cover image helps social sharing.", 3)
    add("faq", "FAQ section", len(faq) >= 1, f"{len(faq)} question(s)." if faq else "Add 4–6 FAQs.", 5)
    add("dup_h2", "Unique subheadings", not dup_h2,
        "All H2s are unique." if not dup_h2 else "Duplicate H2: " + ", ".join(dup_h2[:3]), 4)

    total = sum(c["weight"] for c in checks)
    got = sum(c["weight"] for c in checks if c["ok"])
    return round(got * 100 / total) if total else 0, checks


def recompute(article: SeoArticle, org: Organization | None) -> None:
    article.word_count = word_count(article.body_md or "")
    article.schema_json = build_schema(article, org)
    score, checks = seo_checks(article, org.website if org else None)
    article.seo_score, article.seo_checks = score, checks


# ============================================================ JSON-LD
def build_schema(article, org: Organization | None, image: str | None = None) -> dict:
    base = website_base(org)
    canon = canonical_url(article, org)
    org_id = f"{base or ''}#organization"
    created = _get(article, "created_at") or datetime.now(UTC)
    updated = _get(article, "updated_at") or created
    org_node = {"@type": "Organization", "@id": org_id, "name": org.name if org else ""}
    if base:
        org_node["url"] = base
    art = {
        "@type": "Article", "@id": f"{canon}#article",
        "headline": (_get(article, "meta_title") or _get(article, "title") or "")[:110],
        "description": _get(article, "meta_description") or "",
        "keywords": ", ".join([_get(article, "keyword") or "", *(_get(article, "secondary_keywords") or [])]).strip(", "),
        "datePublished": created.date().isoformat() if hasattr(created, "date") else str(created),
        "dateModified": updated.date().isoformat() if hasattr(updated, "date") else str(updated),
        "author": {"@id": org_id}, "publisher": {"@id": org_id},
        "mainEntityOfPage": {"@type": "WebPage", "@id": canon}, "url": canon,
        "wordCount": _get(article, "word_count") or 0, "inLanguage": "en",
    }
    if image:
        art["image"] = image
    graph = [art]
    faq = _get(article, "faq") or []
    if faq:
        graph.append({"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f.get("q", ""), "acceptedAnswer": {"@type": "Answer", "text": f.get("a", "")}}
            for f in faq if f.get("q")
        ]})
    graph.append(org_node)
    return {"@context": "https://schema.org", "@graph": graph}


# ============================================================ meta fitting
def fit_meta_title(text: str, fallback: str) -> str:
    t = re.sub(r"\s+", " ", (text or fallback or "").strip().strip('"'))
    if len(t) <= 60:
        return t
    cut = t[:60].rsplit(" ", 1)[0].rstrip(" -–:|,")
    return cut


def fit_meta_description(text: str, filler: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip().strip('"'))
    if len(t) < 150:
        extra = re.sub(r"\s+", " ", plain_text(filler or "")).strip()
        for w in extra.split(" "):
            if len(t) >= 150:
                break
            if len(t) + 1 + len(w) > 160:
                break
            t = f"{t} {w}".strip()
    if len(t) > 160:
        t = t[:159].rsplit(" ", 1)[0].rstrip(" ,;:-") + "."
    return t


# ============================================================ context gathering
async def _company_context(session: AsyncSession, max_chars: int = 3500) -> str:
    try:
        from app.services import knowledge
    except ImportError:
        return ""
    try:
        return await knowledge.company_context(session, max_chars=max_chars) or ""
    except Exception:  # noqa: BLE001
        log.exception("company_context failed")
        return ""


async def _retrieve(session: AsyncSession, query: str, k: int = 5) -> list[dict]:
    try:
        from app.services import knowledge
    except ImportError:
        return []
    try:
        return list(await knowledge.retrieve(session, query, k=k) or [])
    except Exception:  # noqa: BLE001
        log.exception("retrieve failed")
        return []


async def _brand_notes(session: AsyncSession) -> str:
    b = await session.scalar(select(BrandProfile))
    if not b:
        return ""
    parts = []
    if b.voice:
        parts.append(f"Brand voice: {b.voice}")
    if b.dont_list:
        parts.append("Avoid: " + "; ".join(map(str, b.dont_list[:10])))
    if b.banned_words:
        parts.append("Never use these words: " + ", ".join(map(str, b.banned_words[:30])))
    return "\n".join(parts)


async def internal_link_candidates(session: AsyncSession, org: Organization | None, query: str, n: int = 8) -> list[dict]:
    """Pages from the organization's own website (crawled KnowledgeDocuments), ranked by overlap with query."""
    domain = site_domain(org.website if org else None)
    rows = (await session.execute(
        select(KnowledgeDocument.url, KnowledgeDocument.title)
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .where(KnowledgeSource.kind == "website", KnowledgeDocument.url.is_not(None))
        .limit(400)
    )).all()
    q = {_stem(t) for t in _key_tokens(query)}
    scored, seen = [], set()
    for url, title in rows:
        host = site_domain(url)
        if domain and host and not (host == domain or host.endswith("." + domain)):
            continue
        if url in seen:
            continue
        seen.add(url)
        toks = {_stem(t) for t in _tokens(f"{title or ''} {url}")}
        scored.append((len(q & toks), -len(url), {"url": url, "title": (title or url)[:120]}))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [s[2] for s in scored[:n]]


def _grounding(snippets: list[dict], max_chars: int = 3000) -> str:
    out, used = [], 0
    for s in snippets:
        t = (s.get("text") or "").strip().replace("\n", " ")
        if not t:
            continue
        piece = f"- ({s.get('title') or s.get('url') or 'source'}) {t[:700]}"
        if used + len(piece) > max_chars:
            break
        out.append(piece)
        used += len(piece)
    return "\n".join(out) or "(no excerpts found)"


def _system(org_name: str, brand: str) -> str:
    return (f"You are a senior SEO content writer for {org_name}. {RULES}" + (f"\n{brand}" if brand else ""))


def _clean_section(text: str, heading: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^```(?:markdown|md)?\s*(.*?)```\s*$", t, re.S)
    if m:
        t = m.group(1).strip()
    lines = t.splitlines()
    while lines:
        first = lines[0].strip()
        m = _HEADING.match(first)
        if not first or first.startswith("# ") or (
            m and len(m.group(1)) <= 2 and (not heading or _norm(m.group(2)) == _norm(heading))
        ):
            lines = lines[1:]
            continue
        break
    # demote stray H1/H2 inside a section body to H3
    fixed = [re.sub(r"^#{1,2}\s+", "### ", line) if re.match(r"^#{1,2}\s", line) else line for line in lines]
    return "\n".join(fixed).strip()


# ============================================================ keyword ideas
def gsc_opportunities(snapshot_data: dict | None, limit: int = 15) -> list[dict]:
    rows = (snapshot_data or {}).get("queries") or []
    picked = []
    for r in rows:
        pos, imp, ctr = float(r.get("position") or 0), int(r.get("impressions") or 0), float(r.get("ctr") or 0)
        if (5 <= pos <= 20 and imp >= 5) or (imp >= 50 and ctr < 0.02):
            picked.append(r)
    picked.sort(key=lambda r: (int(r.get("impressions") or 0), -float(r.get("ctr") or 0)), reverse=True)
    return picked[:limit]


def _competitor_themes(c: Competitor) -> list[str]:
    data = c.data or {}
    themes: list[str] = []
    for key in ("themes", "topics", "content_themes", "keywords", "services", "offerings", "products"):
        v = data.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    themes.append(x)
                elif isinstance(x, dict):
                    name = x.get("name") or x.get("title") or x.get("topic")
                    if name:
                        themes.append(str(name))
    return themes[:12]


async def keyword_ideas(session: AsyncSession, seed: str | None) -> list[dict]:
    company = await _company_context(session, 2500)
    comps = (await session.scalars(select(Competitor).where(Competitor.status == "confirmed").limit(10))).all()
    comp_lines = [f"- {c.name}: {', '.join(_competitor_themes(c)) or (c.reason or '')[:160]}" for c in comps]
    opps = (await session.scalars(
        select(Opportunity).where(Opportunity.status == "new").order_by(Opportunity.score.desc()).limit(15)
    )).all()
    gsc = await session.scalar(
        select(AnalyticsSnapshot).where(AnalyticsSnapshot.source == "gsc").order_by(AnalyticsSnapshot.created_at.desc())
    )
    gsc_rows = gsc_opportunities(gsc.data if gsc else None)
    gsc_lines = [f"- “{r.get('query')}”: position {float(r.get('position') or 0):.1f}, {r.get('impressions')} impressions, "
                 f"CTR {float(r.get('ctr') or 0) * 100:.1f}%" for r in gsc_rows]

    prompt = (
        "Suggest 12 SEO article keywords for this company's blog.\n"
        f"Seed topic: {seed or '(none — use the company focus)'}\n\n"
        f"Company facts:\n{company or '(not available)'}\n\n"
        f"Confirmed competitors and their themes:\n{chr(10).join(comp_lines) or '(none)'}\n\n"
        f"Content opportunities already found:\n{chr(10).join('- ' + o.title for o in opps) or '(none)'}\n\n"
        "Search Console queries where the site ranks on page 1–2 with room to grow (prefer these):\n"
        f"{chr(10).join(gsc_lines) or '(no Search Console data)'}\n\n"
        "Prefer specific long-tail keywords the company can credibly rank for. Mix informational and commercial intent.\n"
        'Return JSON: {"ideas": [{"keyword": "...", "intent": "informational|commercial|transactional|navigational", '
        '"difficulty_guess": "low|medium|high", "rationale": "one sentence", '
        '"source": "search_console|competitor|opportunity|company|seed"}]}'
    )
    ideas: list[dict] = []
    try:
        data = await llm_router.complete_json(session, TASK_LONG, prompt, temperature=0.6)
        raw = data.get("ideas") if isinstance(data, dict) else data
        for i in raw or []:
            if isinstance(i, dict) and str(i.get("keyword") or "").strip():
                ideas.append({
                    "keyword": str(i["keyword"]).strip()[:200],
                    "intent": str(i.get("intent") or "informational").lower(),
                    "difficulty_guess": str(i.get("difficulty_guess") or "medium").lower(),
                    "rationale": str(i.get("rationale") or "")[:400],
                    "source": str(i.get("source") or "company"),
                })
    except llm_router.NoProviderError:
        if not gsc_rows:
            raise
    have = {_norm(i["keyword"]) for i in ideas}
    for r in gsc_rows[:6]:
        q = str(r.get("query") or "").strip()
        if q and _norm(q) not in have:
            ideas.append({
                "keyword": q, "intent": "informational", "difficulty_guess": "low",
                "rationale": f"Already ranks around position {float(r.get('position') or 0):.0f} with "
                             f"{r.get('impressions')} impressions; a focused article can lift clicks.",
                "source": "search_console",
            })
    return ideas


# ============================================================ writing pipeline
async def _json(session, prompt, system, **kw):
    return await llm_router.complete_json(session, TASK_LONG, prompt, system=system, **kw)


def _norm_outline(data, keyword: str, word_target: int) -> list[dict]:
    secs = data.get("sections") if isinstance(data, dict) else data
    out, seen = [], set()
    for s in secs or []:
        if not isinstance(s, dict):
            continue
        h = str(s.get("heading") or "").strip().lstrip("#").strip()
        if not h or _norm(h) in seen or _norm(h) in {"introduction", "conclusion", "faq", "frequently asked questions"}:
            continue
        seen.add(_norm(h))
        subs = []
        for sub in s.get("subsections") or s.get("h3") or []:
            sh = sub.get("heading") if isinstance(sub, dict) else sub
            if sh:
                subs.append({"heading": str(sh).strip().lstrip("#").strip(),
                             "target_words": int((sub.get("target_words") if isinstance(sub, dict) else 0) or 0)})
        try:
            tw = int(s.get("target_words") or 0)
        except (TypeError, ValueError):
            tw = 0
        out.append({"heading": h, "level": 2, "intent": str(s.get("intent") or ""), "target_words": tw,
                    "subsections": subs})
    if not out:
        out = [{"heading": f"What is {keyword}?", "level": 2, "intent": "explain", "target_words": 0, "subsections": []},
               {"heading": f"Why {keyword} matters", "level": 2, "intent": "explain", "target_words": 0, "subsections": []},
               {"heading": f"How to get started with {keyword}", "level": 2, "intent": "how-to", "target_words": 0,
                "subsections": []}]
    out = out[:8]
    body_budget = max(int(word_target * 0.8), 400)
    for s in out:
        if s["target_words"] <= 0:
            s["target_words"] = max(body_budget // len(out), 150)
    return out


async def write_article(session: AsyncSession, article: SeoArticle, progress: Progress = None) -> SeoArticle:
    """Brief → outline → sections → intro/conclusion → FAQ → meta → slug → JSON-LD. Updates `article`."""
    async def step(msg: str):
        if progress:
            await progress(msg)

    org = await session.get(Organization, article.organization_id)
    org_name = org.name if org else "the company"
    meta = article_meta(article)
    word_target = int(meta.get("word_target") or 1500)
    keyword = article.keyword
    secondary = list(article.secondary_keywords or [])
    audience = article.audience or ""
    system = _system(org_name, await _brand_notes(session))
    company = await _company_context(session, 3500)
    candidates = await internal_link_candidates(session, org, " ".join([keyword, *secondary]))
    link_list = "\n".join(f"- {c['title']}: {c['url']}" for c in candidates) or "(none)"
    tone = meta.get("tone") or ""
    notes = meta.get("notes") or ""

    # 1. brief
    await step("Writing the brief")
    top = await _retrieve(session, keyword, k=6)
    brief = await _json(session, (
        f"Create a content brief for an SEO article.\nPrimary keyword: {keyword}\n"
        f"Secondary keywords: {', '.join(secondary) or '(none)'}\nAudience: {audience or 'decision makers'}\n"
        f"Target length: about {word_target} words\nTone: {tone or 'expert, friendly'}\nEditor notes: {notes or '(none)'}\n\n"
        f"Company facts:\n{company or '(not available)'}\n\nRelevant excerpts from the company's site:\n{_grounding(top)}\n\n"
        'Return JSON: {"title": "compelling title containing the keyword", "search_intent": '
        '"informational|commercial|transactional|navigational", "angle": "...", "reader_problem": "...", '
        '"key_points": ["..."], "cta": "the call to action that fits this company"}'
    ), system)
    brief = brief if isinstance(brief, dict) else {}
    intent = str(meta.get("search_intent") or brief.get("search_intent") or "informational").lower()
    if intent not in INTENT_MIN_WORDS:
        intent = "informational"
    title = str(brief.get("title") or "").strip() or article.title or keyword.capitalize()
    if not contains_keyword(title, keyword):
        title = f"{keyword[:1].upper()}{keyword[1:]}: {title}"

    # 2. outline
    await step("Planning the outline")
    outline_data = await _json(session, (
        f"Plan the outline for the article “{title}”.\nPrimary keyword: {keyword}\nSecondary: {', '.join(secondary) or '-'}\n"
        f"Search intent: {intent}\nAngle: {brief.get('angle', '')}\nKey points: {json.dumps(brief.get('key_points') or [])}\n"
        f"Total length: about {word_target} words.\n"
        "Give 4–7 H2 sections (no introduction, conclusion or FAQ — those are added separately). At least one H2 must "
        "contain the primary keyword or a close variant. H2 headings must be unique. Each H2 may have 0–3 H3s.\n"
        'Return JSON: {"sections": [{"heading": "...", "intent": "what this section achieves", "target_words": 250, '
        '"subsections": [{"heading": "...", "target_words": 120}]}]}'
    ), system)
    sections = _norm_outline(outline_data, keyword, word_target)
    if not any(contains_keyword(s["heading"], keyword, secondary) for s in sections):
        sections[0]["heading"] = f"{keyword[:1].upper()}{keyword[1:]}: {sections[0]['heading']}"

    # 3. sections
    written: list[tuple[str, str]] = []
    for i, s in enumerate(sections, 1):
        await step(f"Writing section {i} of {len(sections)}: {s['heading']}")
        snippets = await _retrieve(session, f"{s['heading']} {keyword}", k=5)
        subs = "\n".join(f"  - ### {x['heading']} (~{x['target_words'] or 120} words)" for x in s["subsections"])
        r = await llm_router.complete(session, TASK_LONG, (
            f"Write the section “{s['heading']}” of the article “{title}”.\n"
            f"Primary keyword: {keyword}. Purpose of this section: {s['intent'] or 'inform'}.\n"
            f"Length: about {s['target_words']} words.\n"
            + (f"Use these H3 subheadings, in order:\n{subs}\n" if subs else "")
            + f"Audience: {audience or 'decision makers'}. Tone: {tone or 'expert, friendly'}.\n\n"
            f"Company facts:\n{company or '(not available)'}\n\nSource excerpts (ground your claims in these):\n"
            f"{_grounding(snippets)}\n\n"
            f"The company's own pages you may link to (use at most one, only if relevant, as a Markdown link "
            f"with descriptive anchor text):\n{link_list}\n\n"
            "Only link to an external page if you are certain the URL exists (an official standard, regulator or "
            "documentation homepage). Use the keyword naturally at most once or twice.\n"
            "Return only the Markdown body of this section. Do not repeat the H2 heading. No introduction to the "
            "article, no conclusion."
        ), system=system, temperature=0.7, max_tokens=min(3000, int(s["target_words"] * 2.2) + 400))
        written.append((s["heading"], _clean_section(r.text, s["heading"])))

    # 4. intro + conclusion
    await step("Writing the introduction and conclusion")
    summary = "\n".join(f"- {h}" for h, _ in written)
    ic = await _json(session, (
        f"Write the introduction and conclusion for the article “{title}” (primary keyword: {keyword}).\n"
        f"Sections:\n{summary}\nReader problem: {brief.get('reader_problem', '')}\n"
        f"Company facts:\n{company or '(not available)'}\n"
        f"Call to action to end with: {brief.get('cta') or f'talk to {org_name}'}\n"
        "The introduction is 80–150 words and must use the exact primary keyword in its first two sentences. "
        "The conclusion is 80–150 words, summarises the key takeaway and ends with the call to action"
        + (f" linking to {website_base(org)}/contact" if website_base(org) else "")
        + ".\nReturn JSON: {\"intro\": \"markdown\", \"conclusion\": \"markdown\", \"conclusion_heading\": \"...\"}"
    ), system)
    ic = ic if isinstance(ic, dict) else {}
    intro = _clean_section(str(ic.get("intro") or ""), "")
    if not contains_keyword(" ".join(intro.split()[:100]), keyword):
        intro = f"This guide explains {keyword} for {audience or 'teams like yours'}.\n\n{intro}".strip()
    conclusion = _clean_section(str(ic.get("conclusion") or ""), "")
    conclusion_heading = str(ic.get("conclusion_heading") or "Key takeaways").strip().lstrip("#").strip()

    # 5. FAQ
    await step("Writing the FAQ")
    faq_data = await _json(session, (
        f"Write 5 frequently asked questions with answers for the article “{title}” (keyword: {keyword}).\n"
        f"Company facts:\n{company or '(not available)'}\n"
        "Questions should match what people search for. Answers are 40–80 words, factual, no invented numbers.\n"
        'Return JSON: {"faq": [{"q": "...", "a": "..."}]}'
    ), system)
    raw_faq = faq_data.get("faq") if isinstance(faq_data, dict) else faq_data
    faq = [{"q": str(f.get("q") or f.get("question") or "").strip(), "a": str(f.get("a") or f.get("answer") or "").strip()}
           for f in (raw_faq or []) if isinstance(f, dict)]
    faq = [f for f in faq if f["q"] and f["a"]][:6]

    # 6. meta
    await step("Writing the meta title and description")
    m = await _json(session, (
        f"Write the SEO meta title and meta description for the article “{title}”.\nPrimary keyword: {keyword}\n"
        "Meta title: at most 60 characters, keyword near the start. Meta description: 150–160 characters, "
        "includes the keyword, ends with a reason to click.\n"
        'Return JSON: {"meta_title": "...", "meta_description": "..."}'
    ), system, temperature=0.4)
    m = m if isinstance(m, dict) else {}
    meta_title = fit_meta_title(str(m.get("meta_title") or ""), title)
    meta_description = fit_meta_description(str(m.get("meta_description") or ""), intro)

    # 7. assemble
    await step("Adding links, schema and the SEO score")
    parts = [f"# {title}", intro]
    for h, body in written:
        parts += [f"## {h}", body]
    parts += [f"## {conclusion_heading}", conclusion]
    body_md = "\n\n".join(p for p in parts if p)
    internal_now = [u for _, u, img in links(body_md) if not img and (u.startswith("/") or (
        site_domain(u) and site_domain(org.website if org else None) == site_domain(u)))]
    extra = [c for c in candidates if c["url"] not in internal_now][: max(0, 2 - len(internal_now))]
    if extra:
        body_md += "\n\nRelated reading: " + ", ".join(f"[{c['title']}]({c['url']})" for c in extra) + "."
    if faq:
        body_md += "\n\n## Frequently asked questions\n\n" + "\n\n".join(f"### {f['q']}\n\n{f['a']}" for f in faq)

    article.title = title[:300]
    article.body_md = body_md
    article.faq = faq
    article.meta_title = meta_title
    article.meta_description = meta_description
    if not article.slug or article.slug == slugify(article.keyword) or article.status in ("queued", "writing"):
        article.slug = await unique_slug(session, slugify(keyword), exclude=article.id)
    set_article_meta(article, search_intent=intent, brief={k: brief.get(k) for k in ("angle", "reader_problem", "key_points", "cta")})
    article.outline = [article.outline[0], *sections]
    article.error = None
    recompute(article, org)
    await session.flush()
    return article


async def unique_slug(session: AsyncSession, base: str, exclude: uuid.UUID | None = None) -> str:
    base = slugify(base)
    q = select(SeoArticle.slug).where(SeoArticle.slug.like(f"{base}%"))
    if exclude:
        q = q.where(SeoArticle.id != exclude)
    taken = set((await session.scalars(q)).all())
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


async def rewrite_section(session: AsyncSession, article: SeoArticle, heading: str, instruction: str) -> SeoArticle:
    md = article.body_md or ""
    secs = [s for s in split_sections(md) if s["level"] >= 2 and _norm(s["heading"]) == _norm(heading)]
    if not secs:
        raise ValueError("That heading isn't in the article. Pick a heading from the article body.")
    s = secs[0]
    lines = md.splitlines()
    current = "\n".join(lines[s["start"] + 1:s["end"]]).strip()
    org = await session.get(Organization, article.organization_id)
    snippets = await _retrieve(session, f"{heading} {article.keyword}", k=5)
    company = await _company_context(session, 2500)
    r = await llm_router.complete(session, TASK_LONG, (
        f"Rewrite the section “{heading}” of the article “{article.title}” (keyword: {article.keyword}).\n"
        f"Instruction from the editor: {instruction or 'Improve clarity and depth.'}\n\n"
        f"Current section:\n{current}\n\nCompany facts:\n{company or '(not available)'}\n\n"
        f"Source excerpts:\n{_grounding(snippets)}\n\n"
        "Keep existing links unless the instruction says otherwise. Keep any H3 subheadings at level ###. "
        "Return only the new Markdown body of the section without the heading."
    ), system=_system(org.name if org else "the company", await _brand_notes(session)), temperature=0.6, max_tokens=2500)
    new_body = _clean_section(r.text, heading)
    new_lines = lines[: s["start"] + 1] + ["", *new_body.splitlines(), ""] + lines[s["end"]:]
    article.body_md = re.sub(r"\n{3,}", "\n\n", "\n".join(new_lines)).strip() + "\n"
    recompute(article, org)
    await session.flush()
    return article


# ============================================================ cover image
def _asset_id_from_result(result: dict | None) -> str | None:
    if not result:
        return None
    for key in ("asset_id", "media_id", "id"):
        if result.get(key):
            return str(result[key])
    for key in ("asset_ids", "media_ids", "assets"):
        v = result.get(key)
        if isinstance(v, list) and v:
            first = v[0]
            return str(first.get("id")) if isinstance(first, dict) else str(first)
    return None


async def request_cover(article: SeoArticle) -> uuid.UUID | None:
    """Enqueue media.generate for a cover image and wait up to COVER_WAIT_SECONDS for it."""
    from app.core.db import platform_session
    from app.models import Job
    from app.services.jobs import enqueue_now

    if COVER_WAIT_SECONDS <= 0:
        return None
    payload = {"kind": "image", "platform": "linkedin_org",
               "fields": {"headline": article.title, "body": article.meta_description or ""},
               "seo_article_id": str(article.id)}
    job_id = await enqueue_now("media.generate", article.organization_id, payload)
    waited = 0.0
    while waited < COVER_WAIT_SECONDS:
        await asyncio.sleep(COVER_POLL_SECONDS)
        waited += COVER_POLL_SECONDS
        async with platform_session() as s:
            job = await s.get(Job, job_id)
            if job is None or job.status in ("failed", "cancelled"):
                return None
            if job.status == "done":
                aid = _asset_id_from_result(job.result)
                try:
                    return uuid.UUID(aid) if aid else None
                except ValueError:
                    return None
    return None


# ============================================================ markdown → HTML (safe, small)
_SAFE_URL = re.compile(r"^(https?://|mailto:|/|#|\./|[a-z0-9_-]+\.(png|jpe?g|webp|gif|svg)$)", re.I)


def _inline(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append(f"<code>{html.escape(p[1:-1])}</code>")
            continue
        s = html.escape(p, quote=False)

        def link(m):
            label, url = m.group(2), html.unescape(m.group(3))
            if not _SAFE_URL.match(url):
                return label
            u = html.escape(url, quote=True)
            if m.group(1) == "!":
                return f'<img src="{u}" alt="{html.escape(html.unescape(label), quote=True)}" loading="lazy">'
            ext = ' rel="noopener" target="_blank"' if url.startswith("http") else ""
            return f'<a href="{u}"{ext}>{label}</a>'

        s = re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)", link, s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
        s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", s)
        out.append(s)
    return "".join(out)


def _slug_id(text: str) -> str:
    return slugify(text, 60)


def md_to_html(md: str) -> str:
    lines = (md or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        st = line.strip()
        if not st:
            i += 1
            continue
        if st.startswith("```"):
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(buf))}</code></pre>")
            i += 1
            continue
        m = _HEADING.match(st)
        if m:
            lvl, text = len(m.group(1)), m.group(2)
            out.append(f'<h{lvl} id="{_slug_id(text)}">{_inline(text)}</h{lvl}>')
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,})$", st):
            out.append("<hr>")
            i += 1
            continue
        if st.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{md_to_html(chr(10).join(buf))}</blockquote>")
            continue
        if "|" in st and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            def cells(row):
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(st)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i]:
                rows.append(cells(lines[i]))
                i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in head)
            tb = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
            continue
        lm = re.match(r"^\s*([-*+]|\d+\.)\s+", line)
        if lm:
            ordered = lm.group(1)[0].isdigit()
            items = []
            while i < len(lines) and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not _HEADING.match(lines[i].strip()) \
                and not lines[i].strip().startswith(("```", ">")) and not re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")
    return "\n".join(out)


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
{og_image}
{extra_head}
<script type="application/ld+json">{schema}</script>
<style>
  :root {{ --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --brand:#075492; --brand-600:#086fb8; --accent:#06a6c7; --tint:#f0f7ff; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#f8fafc; color:var(--ink); font:17px/1.7 Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .bar {{ height:4px; background:linear-gradient(90deg,#1d4ed8,#0891b2); }}
  main {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; background:#fff; }}
  h1 {{ font-size:2.1rem; line-height:1.2; letter-spacing:-0.02em; margin:0 0 .6em; color:#062f59; }}
  h2 {{ font-size:1.5rem; line-height:1.3; margin:1.8em 0 .5em; color:#062f59; letter-spacing:-0.01em; }}
  h3 {{ font-size:1.15rem; margin:1.4em 0 .4em; }}
  a {{ color:var(--brand-600); text-underline-offset:2px; }}
  a:hover {{ color:var(--brand); }}
  img {{ max-width:100%; height:auto; border-radius:12px; }}
  .cover {{ margin:0 0 28px; }}
  blockquote {{ margin:1.2em 0; padding:.6em 1em; border-left:4px solid var(--accent); background:var(--tint); }}
  code {{ background:#f1f5f9; padding:.1em .35em; border-radius:4px; font-size:.92em; }}
  pre {{ background:#0f172a; color:#e2e8f0; padding:14px; border-radius:10px; overflow:auto; }}
  pre code {{ background:none; padding:0; color:inherit; }}
  table {{ border-collapse:collapse; width:100%; margin:1em 0; font-size:.95em; }}
  th, td {{ border:1px solid var(--line); padding:8px 10px; text-align:left; }}
  th {{ background:var(--tint); }}
  .meta {{ color:var(--muted); font-size:.9rem; margin-bottom:24px; }}
</style>
</head>
<body>
<div class="bar"></div>
<main>
<article>
{cover}
<div class="meta">{org} · {date}</div>
{body}
</article>
</main>
</body>
</html>
"""


def head_extras(article, org: Organization | None, canon: str, image_url: str | None = None) -> str:
    """og:url / site name / article dates / Twitter card tags shared by the export and the website bundle."""
    e = html.escape
    created = _get(article, "created_at") or datetime.now(UTC)
    updated = _get(article, "updated_at") or created
    title = _get(article, "meta_title") or _get(article, "title") or ""
    desc = _get(article, "meta_description") or ""
    tags = [
        f'<meta property="og:url" content="{e(canon)}">',
        '<meta name="robots" content="index, follow, max-image-preview:large">',
        f'<meta property="article:published_time" content="{e(created.isoformat())}">',
        f'<meta property="article:modified_time" content="{e(updated.isoformat())}">',
        f'<meta name="twitter:card" content="{"summary_large_image" if image_url else "summary"}">',
        f'<meta name="twitter:title" content="{e(title)}">',
        f'<meta name="twitter:description" content="{e(desc)}">',
    ]
    if org and org.name:
        tags.insert(1, f'<meta property="og:site_name" content="{e(org.name)}">')
    if image_url:
        tags.append(f'<meta name="twitter:image" content="{e(image_url)}">')
    return "\n".join(tags)


# ============================================================ export
def export_dir(org: Organization, slug: str) -> Path:
    p = storage.root() / "seo" / slugify(org.slug or str(org.id)) / slugify(slug)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def export_article(session: AsyncSession, article: SeoArticle) -> Path:
    org = await session.get(Organization, article.organization_id)
    if not article.body_md:
        raise ValueError("The article has no text yet.")
    folder = export_dir(org, article.slug)
    cover_name = None
    if article.cover_media_id:
        asset = await session.scalar(select(MediaAsset).where(MediaAsset.id == article.cover_media_id))
        if asset:
            try:
                src = storage.abs_path(asset.path)
                if src.exists():
                    cover_name = "cover" + (src.suffix or ".png")
                    shutil.copyfile(src, folder / cover_name)
            except (ValueError, OSError):
                log.warning("cover copy failed for article %s", article.id)
    canon = canonical_url(article, org)
    recompute(article, org)
    schema = build_schema(article, org, image=cover_name)
    article.schema_json = schema
    date = (article.updated_at or datetime.now(UTC)).date().isoformat()
    keywords = [article.keyword, *(article.secondary_keywords or [])]

    def yq(v: str) -> str:
        return json.dumps(v or "", ensure_ascii=False)

    front = [
        "---",
        f"title: {yq(article.meta_title or article.title)}",
        f"description: {yq(article.meta_description or '')}",
        f"date: {date}",
        f"slug: {yq(article.slug)}",
        "keywords: [" + ", ".join(yq(k) for k in keywords if k) + "]",
        f"canonical: {yq(canon)}",  # placeholder until the website integration decides the final URL
        f"ogImage: {yq('./' + cover_name if cover_name else '')}",
        "---",
        "",
    ]
    body = article.body_md
    (folder / "article.mdx").write_text("\n".join(front) + body.strip() + "\n", encoding="utf-8")

    cover_html = (f'<figure class="cover"><img src="./{html.escape(cover_name)}" alt="{html.escape(article.title)}"></figure>'
                  if cover_name else "")
    page = HTML_TEMPLATE.format(
        title=html.escape(article.meta_title or article.title), description=html.escape(article.meta_description or ""),
        canonical=html.escape(canon), schema=json.dumps(schema, ensure_ascii=False).replace("</", "<\\/"),
        og_image=f'<meta property="og:image" content="./{html.escape(cover_name)}">' if cover_name else "",
        extra_head=head_extras(article, org, canon),
        cover=cover_html, org=html.escape(org.name if org else ""), date=date, body=md_to_html(body),
    )
    (folder / "article.html").write_text(page, encoding="utf-8")

    meta_json = {
        "id": str(article.id), "title": article.title, "slug": article.slug, "keyword": article.keyword,
        "secondary_keywords": article.secondary_keywords or [], "audience": article.audience,
        "meta_title": article.meta_title, "meta_description": article.meta_description, "canonical": canon,
        "date": date, "word_count": article.word_count, "seo_score": article.seo_score,
        "seo_checks": article.seo_checks, "faq": article.faq or [], "outline": outline_sections(article),
        "settings": {k: v for k, v in article_meta(article).items() if k != "kind"},
        "cover_image": cover_name, "schema": schema,
    }
    (folder / "meta.json").write_text(json.dumps(meta_json, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    article.export_path = storage.rel_path(folder)
    article.status = "exported"
    await session.flush()
    return folder


def export_file(article: SeoArticle, fmt: str) -> Path | None:
    if not article.export_path:
        return None
    name = {"mdx": "article.mdx", "html": "article.html", "json": "meta.json"}.get(fmt)
    if not name:
        return None
    try:
        p = storage.abs_path(article.export_path) / name
    except ValueError:
        return None
    return p if p.exists() else None


def remove_export(article: SeoArticle) -> None:
    if not article.export_path:
        return
    try:
        p = storage.abs_path(article.export_path)
        if p.is_dir() and (storage.root().resolve() / "seo") in p.parents:
            shutil.rmtree(p, ignore_errors=True)
    except ValueError:
        pass


# ============================================================ repurpose into social posts
def _choose_post_type(platform: str, has_cover: bool) -> str:
    spec = PLATFORMS.get(platform)
    types = spec.post_types if spec else ("text",)
    if "link" in types:
        return "link"
    if "image" in types:
        return "image"
    return types[0]


async def repurpose(session: AsyncSession, article: SeoArticle, account_ids: list[uuid.UUID], user) -> list[Post]:
    if not article.body_md:
        raise ValueError("Write the article before sharing it.")
    org = await session.get(Organization, article.organization_id)
    url = canonical_url(article, org)
    accounts = (await session.scalars(select(SocialAccount).where(SocialAccount.id.in_(account_ids)))).all()
    if not accounts:
        raise ValueError("Pick at least one connected social account.")
    brand = await _brand_notes(session)
    excerpt = plain_text(article.body_md)[:2500]
    posts: list[Post] = []
    for acc in accounts:
        spec = PLATFORMS.get(acc.platform) or PLATFORMS["manual"]
        ptype = _choose_post_type(acc.platform, bool(article.cover_media_id))
        link_in_text = ptype != "link"
        budget = spec.max_chars - (len(url) + 2 if link_in_text else 0)
        data = await llm_router.complete_json(session, TASK_WRITE, (
            f"Write a {spec.label} post that promotes our new article and makes people want to read it.\n"
            f"Article title: {article.title}\nKeyword: {article.keyword}\nSummary source:\n{excerpt}\n\n"
            f"Platform style: {spec.style or 'clear and friendly'}\n"
            f"Maximum {budget} characters for the text. At most {spec.max_hashtags} hashtags (return them separately, "
            "without them in the text). Do not include the URL; it is added automatically. "
            "Never invent statistics or client names.\n"
            + (f"Also write a title of at most {spec.title_chars} characters.\n" if spec.title_chars else "")
            + 'Return JSON: {"text": "...", "hashtags": ["#tag"], "title": "..."}'
        ), system=f"You write social posts for {org.name if org else 'the company'}. {RULES}\n{brand}".strip(),
            temperature=0.7, max_tokens=900)
        data = data if isinstance(data, dict) else {}
        tags = [("#" + str(t).lstrip("#").replace(" ", "")) for t in (data.get("hashtags") or []) if str(t).strip()]
        tags = tags[: spec.max_hashtags]
        text = str(data.get("text") or article.meta_description or article.title).strip()
        tag_len = (sum(len(t) + 1 for t in tags) + 2) if tags else 0
        room = max(budget - tag_len, 40)
        if len(text) > room:
            text = text[: room - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        if link_in_text:
            text = f"{text}\n\n{url}"
        post = Post(
            organization_id=article.organization_id, social_account_id=acc.id, platform=acc.platform,
            seo_article_id=article.id, topic=article.title, post_type=ptype, status="in_review", mode="approve_first",
            scheduled_at=None, title=(str(data.get("title") or article.title)[: spec.title_chars] if spec.title_chars else None),
            text=text, hashtags=tags, link_url=url,
            media_ids=[str(article.cover_media_id)] if (article.cover_media_id and ptype != "link") else [],
            meta={"source": "seo_article", "article_slug": article.slug},
            created_by=getattr(user, "id", None),
        )
        session.add(post)
        await session.flush()
        session.add(PostActivity(organization_id=article.organization_id, post_id=post.id, user_id=getattr(user, "id", None),
                                 actor=getattr(user, "email", None), action="created",
                                 note=f"From SEO article “{article.title[:120]}”"))
        posts.append(post)
    await session.flush()
    return posts
