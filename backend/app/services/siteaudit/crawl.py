"""Site audit crawler.

Builds on app/services/crawler.py (URL normalisation, SSRF protection, registrable domains) but records
much more per URL: the whole redirect chain, status, timings, size, headers that matter for indexing,
and a detailed parse of every HTML page (titles, metas, headings, links with anchor text, images,
structured data, social tags, hreflang, forms, mixed content, content hash ...).

Site-wide files are read too: robots.txt (with AI-crawler rules), XML sitemaps, llms.txt, favicon,
the http:// and www/non-www variants of the home page. External links and images are checked with
HEAD requests (GET when HEAD isn't supported), within fixed caps.

Polite: robots.txt is obeyed for the configured user agent, Crawl-delay is honoured (max 5 s),
5 requests in flight, no binaries downloaded. One bad URL never fails the audit.
"""

from __future__ import annotations

import asyncio
import fnmatch
import gzip
import hashlib
import json
import logging
import re
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.services import crawler
from app.services.crawler import _SafeHosts, domain_of, has_skipped_extension, normalize_url

log = logging.getLogger(__name__)

# Tests replace this (or crawler.TRANSPORT) with httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None

CONCURRENCY = 5
BASE_DELAY = 0.1
MAX_REDIRECTS = 10
MAX_BYTES = 6_000_000
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
CHECK_TIMEOUT = httpx.Timeout(10.0, connect=6.0)
EXTERNAL_LIMIT = 250
IMAGE_LIMIT = 200
RESOURCE_LIMIT = 100
SITEMAP_LIMIT = 20
SITEMAP_URL_LIMIT = 10_000
MAX_QUEUE = 20_000
STORED_LINKS = 250

AI_BOTS: list[tuple[str, str, str]] = [
    # user-agent token, owner, purpose ("search" = answers/citations in AI search, "training" = model training)
    ("GPTBot", "OpenAI", "training"),
    ("OAI-SearchBot", "OpenAI", "search"),
    ("ChatGPT-User", "OpenAI", "search"),
    ("ClaudeBot", "Anthropic", "training"),
    ("Claude-SearchBot", "Anthropic", "search"),
    ("Claude-User", "Anthropic", "search"),
    ("PerplexityBot", "Perplexity", "search"),
    ("Perplexity-User", "Perplexity", "search"),
    ("Google-Extended", "Google", "training"),
    ("Applebot-Extended", "Apple", "training"),
    ("CCBot", "Common Crawl", "training"),
    ("Bytespider", "ByteDance", "training"),
    ("meta-externalagent", "Meta", "training"),
    ("Amazonbot", "Amazon", "search"),
]
SEARCH_BOTS = ["Googlebot", "Bingbot"]

ROBOTS_DIRECTIVES = re.compile(
    r"^\s*(user-agent|disallow|allow|sitemap|crawl-delay|host|clean-param|noindex|request-rate|visit-time|"
    r"content-signal)\s*:", re.IGNORECASE)


# ------------------------------------------------------------------ settings
@dataclass
class AuditConfig:
    start_url: str
    max_pages: int = 500
    max_depth: int = 10
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    user_agent: str = crawler.USER_AGENT
    check_external: bool = True


def _match(pattern: str, url: str) -> bool:
    """'*' wildcards match the path + query; plain text matches anywhere in the path."""
    p = urlsplit(url)
    target = p.path + (f"?{p.query}" if p.query else "")
    pattern = pattern.strip()
    if not pattern:
        return False
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatchcase(target, pattern) or fnmatch.fnmatchcase(url, pattern)
    return pattern in target


# ------------------------------------------------------------------ fetching
@dataclass
class Resp:
    url: str
    final_url: str
    status: int = 0
    chain: list[dict] = field(default_factory=list)  # [{url, status}] for every redirect hop
    loop: bool = False
    error: str | None = None
    content_type: str = ""
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    size: int | None = None
    ttfb_ms: int | None = None
    total_ms: int | None = None

    @property
    def is_html(self) -> bool:
        ct = self.content_type.lower()
        return "html" in ct or (not ct and self.body.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")))

    def text(self) -> str:
        m = re.search(r"charset=([\w-]+)", self.content_type, re.IGNORECASE)
        for enc in ([m.group(1)] if m else []) + ["utf-8"]:
            try:
                return self.body.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.body.decode("utf-8", errors="replace")


KEEP_HEADERS = ("x-robots-tag", "content-encoding", "strict-transport-security", "link", "cache-control",
                "content-length", "last-modified", "server", "content-security-policy", "x-content-type-options")


def make_client(user_agent: str, timeout: httpx.Timeout = TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, transport=TRANSPORT or crawler.TRANSPORT,
        headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                 "Accept-Language": "en;q=0.9"},
    )


def _error_text(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "Timed out"
    msg = f"{type(e).__name__}: {e}"
    low = msg.lower()
    if "certificate" in low or "ssl" in low:
        return "SSL certificate problem: " + str(e)[:200]
    if "name or service not known" in low or "getaddrinfo" in low or "nodename" in low:
        return "Domain not found"
    return msg[:300]


async def fetch(client: httpx.AsyncClient, url: str, safe: _SafeHosts, *, method: str = "GET",
                read_body: bool = True) -> Resp:
    """Request with manual redirects (every hop SSRF-checked). Records the chain and timings. Never raises."""
    out = Resp(url=url, final_url=url)
    current = url
    seen = {url}
    t0 = time.perf_counter()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not await safe.check(current):
                out.error = "Blocked: not a public web address"
                out.final_url = current
                return out
            hop_start = time.perf_counter()
            async with client.stream(method, current) as r:
                ttfb = int((time.perf_counter() - hop_start) * 1000)
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    nxt = normalize_url(r.headers["location"], current)
                    out.chain.append({"url": current, "status": r.status_code})
                    if not nxt:
                        out.status, out.final_url, out.error = r.status_code, current, "Redirect to an invalid address"
                        return out
                    if nxt in seen:
                        out.loop, out.status, out.final_url = True, r.status_code, nxt
                        out.error = "Redirect loop"
                        return out
                    seen.add(nxt)
                    current = nxt
                    continue
                out.status, out.final_url, out.ttfb_ms = r.status_code, current, ttfb
                out.content_type = r.headers.get("content-type", "")[:120]
                out.headers = {k: r.headers[k][:500] for k in KEEP_HEADERS if k in r.headers}
                cl = r.headers.get("content-length")
                if cl and cl.isdigit():
                    out.size = int(cl)
                ct = out.content_type.lower()
                textual = "html" in ct or "xml" in ct or "text" in ct or not ct
                if read_body and method == "GET" and textual and r.status_code < 400:
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in r.aiter_bytes():  # decompressed (gzip/deflate) by httpx
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > MAX_BYTES:
                            break
                    out.body = b"".join(chunks)[:MAX_BYTES]
                    out.size = out.size or size
                out.total_ms = int((time.perf_counter() - t0) * 1000)
                return out
        out.error, out.final_url = "Too many redirects", current
        return out
    except Exception as e:  # noqa: BLE001
        out.error, out.final_url = _error_text(e), current
        return out


async def check_url(client: httpx.AsyncClient, url: str, safe: _SafeHosts) -> dict:
    """Status of a link or image target: HEAD first, GET when HEAD isn't supported."""
    r = await fetch(client, url, safe, method="HEAD", read_body=False)
    if r.error is None and r.status in (400, 403, 405, 406, 429, 500, 501, 503):
        g = await fetch(client, url, safe, method="GET", read_body=False)
        if g.error is None or r.status == 0:
            r = g
    return {"status": r.status, "error": r.error, "final_url": r.final_url if r.chain else None,
            "redirects": len(r.chain), "size": r.size, "content_type": r.content_type}


# ------------------------------------------------------------------ HTML parsing
JSONLD_REQUIRED: dict[str, list[str | tuple[str, ...]]] = {
    "Article": ["headline"], "NewsArticle": ["headline"], "BlogPosting": ["headline"],
    "Product": ["name", ("offers", "review", "aggregateRating")],
    "BreadcrumbList": ["itemListElement"], "FAQPage": ["mainEntity"], "Organization": ["name"],
    "LocalBusiness": ["name", "address"], "Event": ["name", "startDate", "location"], "JobPosting":
    ["title", "description", "datePosted", "hiringOrganization"], "Recipe": ["name", "image"],
    "VideoObject": ["name", "thumbnailUrl", "uploadDate"], "Review": ["itemReviewed", "author"],
    "WebSite": ["name"], "Person": ["name"], "SoftwareApplication": ["name"], "Course": ["name", "description"],
    "HowTo": ["name", "step"],
}
GENERIC_ANCHORS = {"click here", "here", "read more", "more", "link", "this", "learn more", "this page", "go", "click",
                   "details", "continue", "more info", "website"}
_LANG = re.compile(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$")
_HREFLANG = re.compile(r"^([a-zA-Z]{2,3}(-([a-zA-Z]{4}|[a-zA-Z]{2}|\d{3}))?|x-default)$")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def _walk_types(obj, out: list[str], missing: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(obj, list):
        for o in obj[:50]:
            _walk_types(o, out, missing, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    t = obj.get("@type")
    for x in (t if isinstance(t, list) else [t]):
        if isinstance(x, str):
            if x not in out:
                out.append(x)
            for req in JSONLD_REQUIRED.get(x, []):
                names = req if isinstance(req, tuple) else (req,)
                if not any(obj.get(n) not in (None, "", [], {}) for n in names):
                    missing.append(f"{x}: {' or '.join(names)}")
    for k in ("@graph", "mainEntity", "itemListElement", "publisher", "author", "offers", "hasPart"):
        if k in obj and isinstance(obj[k], (list, dict)):
            _walk_types(obj[k], out, missing, depth + 1)


def parse_page(html: str, url: str, site_domain: str) -> dict:
    """Every on-page fact the checks need. Never raises on bad markup."""
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        soup = BeautifulSoup(html, "html.parser")
    https = url.startswith("https://")
    f: dict = {}

    titles = [_clean(t.get_text(" ")) for t in soup.find_all("title") if not t.find_parent("svg")]
    f["title"] = titles[0] if titles else ""
    f["title_count"] = len(titles)

    metas = soup.find_all("meta")

    def metas_named(*names: str) -> list[str]:
        return [(m.get("content") or "").strip() for m in metas if (m.get("name") or "").strip().lower() in names]

    descs = metas_named("description")
    f["description"] = descs[0] if descs else ""
    f["description_count"] = len(descs)
    robots = ",".join(metas_named("robots", "googlebot")).lower()
    f["meta_robots"] = robots
    f["viewport"] = (metas_named("viewport") or [None])[0]
    f["charset"] = bool(soup.find("meta", charset=True) or any(
        (m.get("http-equiv") or "").lower() == "content-type" and "charset" in (m.get("content") or "").lower() for m in metas))
    refresh = next((m.get("content") or "" for m in metas if (m.get("http-equiv") or "").lower() == "refresh"), None)
    f["meta_refresh"] = refresh
    f["doctype"] = html.lstrip()[:15].lower().startswith("<!doctype")
    html_tag = soup.find("html")
    f["lang"] = ((html_tag.get("lang") or "").strip() or None) if html_tag else None

    def rel_has(tag, name: str) -> bool:
        rel = tag.get("rel") or []
        rel = rel if isinstance(rel, list) else [rel]
        return name in [r.lower() for r in rel]

    links_head = soup.find_all("link")
    canon_raw = [(lk.get("href") or "").strip() for lk in links_head if rel_has(lk, "canonical") and lk.get("href")]
    f["canonical_raw"] = canon_raw[:5]
    f["canonicals"] = list(dict.fromkeys(c for c in (normalize_url(h, url) for h in canon_raw) if c))[:5]
    f["canonical"] = f["canonicals"][0] if f["canonicals"] else None
    f["hreflang"] = [{"lang": (lk.get("hreflang") or "").strip(), "href": normalize_url(lk.get("href") or "", url)}
                     for lk in links_head if rel_has(lk, "alternate") and lk.get("hreflang")][:60]
    f["favicon"] = any(rel_has(lk, "icon") or rel_has(lk, "shortcut") for lk in links_head)
    f["stylesheets"] = sum(1 for lk in links_head if rel_has(lk, "stylesheet"))

    og: dict[str, str] = {}
    tw: dict[str, str] = {}
    for m in metas:
        prop = (m.get("property") or m.get("name") or "").strip().lower()
        val = (m.get("content") or "").strip()
        if prop.startswith("og:") and val and prop not in og:
            og[prop] = val[:500]
        if prop.startswith("twitter:") and val and prop not in tw:
            tw[prop] = val[:500]
    f["og"], f["twitter"] = og, tw

    headings: dict[str, list[str]] = {}
    order: list[int] = []
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        lvl = int(h.name[1])
        headings.setdefault(h.name, []).append(_clean(h.get_text(" "))[:200])
        if len(order) < 200:
            order.append(lvl)
    f["headings"] = {k: v[:20] for k, v in headings.items()}
    f["heading_counts"] = {k: len(v) for k, v in headings.items()}
    f["heading_order"] = order
    skips = [(a, b) for a, b in zip(order, order[1:], strict=False) if b > a + 1]
    f["heading_skip"] = f"H{skips[0][0]} → H{skips[0][1]}" if skips else None

    # structured data
    blocks: list[dict] = []
    types: list[str] = []
    missing: list[str] = []
    for s in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.IGNORECASE)}):
        raw = (s.string or s.get_text() or "").strip()
        if not raw:
            blocks.append({"error": "Empty JSON-LD block"})
            continue
        try:
            data = json.loads(raw, strict=False)
        except ValueError as e:
            blocks.append({"error": f"Invalid JSON: {str(e)[:120]}"})
            continue
        t: list[str] = []
        _walk_types(data, t, missing)
        top = data if isinstance(data, list) else [data]
        no_ctx = any(isinstance(o, dict) and "@context" not in o for o in top)
        no_type = not t
        blocks.append({"types": t, "no_context": no_ctx, "no_type": no_type})
        types += [x for x in t if x not in types]
    f["jsonld"] = blocks[:20]
    f["jsonld_types"] = types[:30]
    f["jsonld_missing_fields"] = list(dict.fromkeys(missing))[:20]
    f["microdata_types"] = list(dict.fromkeys(
        (e.get("itemtype") or "").rsplit("/", 1)[-1] for e in soup.find_all(attrs={"itemtype": True})))[:20]

    # links
    links: list[dict] = []
    seen_links: set[tuple[str, bool]] = set()
    empty_anchor = generic = js_links = 0
    internal_targets: list[str] = []
    external_targets: list[str] = []
    nofollow_internal: list[str] = []
    http_internal: list[str] = []
    http_external = 0
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if href.lower().startswith("javascript:"):
            js_links += 1
            continue
        if not href or href.startswith(("#", "mailto:", "tel:", "data:", "sms:", "whatsapp:", "skype:")):
            continue
        absu = normalize_url(href, url)
        if not absu:
            continue
        img = a.find("img")
        anchor = _clean(a.get_text(" ")) or _clean(a.get("aria-label") or "") or _clean(a.get("title") or "") or (
            _clean(img.get("alt") or "") if img else "")
        rel = a.get("rel") or []
        rel = [r.lower() for r in (rel if isinstance(rel, list) else str(rel).split())]
        nofollow = "nofollow" in rel or "ugc" in rel or "sponsored" in rel
        internal = domain_of(absu) == site_domain
        if not anchor:
            empty_anchor += 1
        elif anchor.lower().strip(" .!›»→") in GENERIC_ANCHORS:
            generic += 1
        if internal:
            internal_targets.append(absu)
            if nofollow:
                nofollow_internal.append(absu)
            if https and absu.startswith("http://"):
                http_internal.append(absu)
        else:
            external_targets.append(absu)
            if absu.startswith("http://"):
                http_external += 1
        key = (absu, internal)
        if key not in seen_links and len(links) < STORED_LINKS:
            seen_links.add(key)
            links.append({"href": absu, "anchor": anchor[:100], "nofollow": nofollow, "internal": internal})
    f["links"] = links
    f["internal_links"] = list(dict.fromkeys(internal_targets))[:1000]
    f["external_links"] = list(dict.fromkeys(external_targets))[:300]
    f["internal_link_count"] = len(internal_targets)
    f["external_link_count"] = len(external_targets)
    f["link_count"] = len(internal_targets) + len(external_targets)
    f["empty_anchor_links"] = empty_anchor
    f["generic_anchor_links"] = generic
    f["js_links"] = js_links
    f["nofollow_internal"] = list(dict.fromkeys(nofollow_internal))[:20]
    f["http_internal_links"] = list(dict.fromkeys(http_internal))[:20]
    f["http_external_links"] = http_external

    # images
    imgs: list[dict] = []
    for i in soup.find_all("img"):
        src = (i.get("src") or i.get("data-src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        absu = normalize_url(src, url)
        if not absu:
            continue
        alt = i.get("alt")
        imgs.append({"src": absu, "alt": None if alt is None else _clean(alt)[:300],
                     "dims": bool(i.get("width") and i.get("height")), "lazy": (i.get("loading") or "").lower() == "lazy"})
    f["images"] = imgs[:200]
    f["image_count"] = len(imgs)
    f["images_missing_alt"] = [x["src"] for x in imgs if x["alt"] is None][:50]
    f["images_long_alt"] = [x["src"] for x in imgs if x["alt"] and len(x["alt"]) > 125][:20]
    f["images_no_dims"] = sum(1 for x in imgs if not x["dims"])
    f["images_lazy"] = sum(1 for x in imgs if x["lazy"])
    f["legacy_images"] = [x["src"] for x in imgs if urlsplit(x["src"]).path.lower().endswith((".bmp", ".tif", ".tiff"))][:20]

    # forms, mixed content, resources
    forms = []
    for fm in soup.find_all("form"):
        action = normalize_url(fm.get("action") or "", url) or url
        forms.append({"action": action, "method": (fm.get("method") or "get").lower(),
                      "password": bool(fm.find("input", attrs={"type": re.compile("^password$", re.I)}))})
    f["forms"] = forms[:20]
    mixed: list[str] = []
    if https:
        for tag, attr in (("img", "src"), ("script", "src"), ("iframe", "src"), ("video", "src"), ("audio", "src"),
                          ("source", "src"), ("embed", "src"), ("object", "data")):
            for el in soup.find_all(tag, attrs={attr: True}):
                v = (el.get(attr) or "").strip()
                if v.lower().startswith("http://"):
                    mixed.append(v[:300])
        for lk in links_head:
            if rel_has(lk, "stylesheet") and (lk.get("href") or "").lower().startswith("http://"):
                mixed.append(lk["href"][:300])
    f["mixed_content"] = list(dict.fromkeys(mixed))[:20]
    f["scripts"] = len(soup.find_all("script", src=True))
    f["iframes"] = len(soup.find_all("iframe"))
    f["deprecated_tags"] = sorted({t.name for t in soup.find_all(["font", "center", "marquee", "blink", "frameset", "frame"])})
    f["plugins"] = bool(soup.find("applet") or any(
        ".swf" in (e.get("src") or e.get("data") or "").lower() or "flash" in (e.get("type") or "").lower()
        for e in soup.find_all(["embed", "object"])))
    f["dom_nodes"] = len(soup.find_all(True))
    f["js_app_shell"] = bool(soup.find(id=re.compile(r"^(root|app|__next|__nuxt|svelte)$")))

    # text
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    body = soup.body or soup
    text = _clean(body.get_text(" "))
    words = re.findall(r"\w+", text, re.UNICODE)
    f["word_count"] = len(words)
    f["text_chars"] = len(text)
    f["text_ratio"] = round(len(text) / max(1, len(html)), 4)
    f["content_hash"] = hashlib.sha1(" ".join(words).lower().encode()).hexdigest() if len(words) >= 30 else None
    f["lorem_ipsum"] = "lorem ipsum" in text.lower()
    f["excerpt"] = text[:300]
    return f


# ------------------------------------------------------------------ sitemaps & robots
def parse_sitemap(data: bytes) -> tuple[list[tuple[str, str | None]], list[str], str | None]:
    """([(loc, lastmod)], child sitemaps, error message or None)."""
    from lxml import etree

    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            return [], [], "The gzip file couldn't be decompressed"
    error = None
    root = None
    try:
        root = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
    except Exception as e:  # noqa: BLE001
        error = f"Not valid XML ({str(e)[:100]})"
        try:
            root = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=True))
        except Exception:  # noqa: BLE001
            root = None
    if root is None:
        return [], [], error or "Empty sitemap"
    name = etree.QName(root).localname.lower() if isinstance(root.tag, str) else ""
    if name not in ("urlset", "sitemapindex"):
        return [], [], error or "The file isn't a sitemap (no <urlset> or <sitemapindex>)"
    pages: list[tuple[str, str | None]] = []
    children: list[str] = []
    for el in root:
        if not isinstance(el.tag, str):
            continue
        loc = lastmod = None
        for c in el:
            if not isinstance(c.tag, str):
                continue
            ln = etree.QName(c).localname.lower()
            if ln == "loc" and c.text:
                loc = c.text.strip()
            elif ln == "lastmod" and c.text:
                lastmod = c.text.strip()
        if not loc:
            continue
        if name == "sitemapindex" or etree.QName(el).localname.lower() == "sitemap":
            children.append(loc)
        else:
            pages.append((loc, lastmod))
    return pages, children, error


def robots_groups(text: str) -> dict[str, list[tuple[str, str]]]:
    """{lower-case user agent: [(directive, value)]} for allow/disallow rules (groups share rules)."""
    groups: dict[str, list[tuple[str, str]]] = {}
    agents: list[str] = []
    in_rules = False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(v.lower())
            for a in agents:
                groups.setdefault(a, [])
        elif k in ("allow", "disallow"):
            in_rules = True
            for a in agents:
                groups.setdefault(a, []).append((k, v))
    return groups


def bot_access(text: str | None, found: bool, origin: str, agent: str) -> dict:
    """Whether `agent` may fetch the home page, and whether robots.txt names it."""
    if not found or text is None:
        return {"status": "allowed", "named": False, "rule": "No robots.txt rules"}
    rp = RobotFileParser()
    rp.parse(text.splitlines())
    groups = robots_groups(text)
    named = agent.lower() in groups
    allowed = rp.can_fetch(agent, origin.rstrip("/") + "/")
    rules = groups.get(agent.lower()) if named else groups.get("*", [])
    # "partial" only for rules aimed at this crawler; the shared "*" group's usual exclusions (admin, cart…) aren't AI-specific
    partial = allowed and named and any(k == "disallow" and v for k, v in (rules or []))
    status = "blocked" if not allowed else "partial" if partial else "allowed"
    rule = (f"User-agent: {agent}" if named else "User-agent: *" if "*" in groups else "No rule for this crawler")
    return {"status": status, "named": named, "rule": rule}


def robots_report(text: str | None, status: int, error: str | None, origin: str, ua: str) -> dict:
    out: dict = {"url": urljoin(origin, "/robots.txt"), "status": status, "error": error, "found": False}
    if text is None:
        return out
    out["found"] = True
    out["size"] = len(text.encode())
    out["text"] = text[:20_000]
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    out["invalid_lines"] = [ln.strip()[:120] for ln in lines if not ROBOTS_DIRECTIVES.match(ln)][:10]
    out["sitemaps"] = [ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sitemap:")][:20]
    rp = RobotFileParser()
    rp.parse(text.splitlines())
    try:
        cd = rp.crawl_delay(ua) or rp.crawl_delay("*")
        out["crawl_delay"] = float(cd) if cd else None
    except (TypeError, ValueError):
        out["crawl_delay"] = None
    out["search_engines"] = {b: bot_access(text, True, origin, b)["status"] for b in SEARCH_BOTS}
    return out


# ------------------------------------------------------------------ crawl
@dataclass
class CrawlData:
    start_url: str
    origin: str = ""
    domain: str = ""
    home: str = ""
    pages: dict[str, dict] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    robots: dict = field(default_factory=dict)
    sitemaps: list[dict] = field(default_factory=list)
    sitemap_urls: dict[str, dict] = field(default_factory=dict)
    llms: dict = field(default_factory=dict)
    favicon: bool = False
    http_variant: dict = field(default_factory=dict)
    host_variant: dict = field(default_factory=dict)
    external: dict[str, dict] = field(default_factory=dict)
    images: dict[str, dict] = field(default_factory=dict)
    resources: dict[str, dict] = field(default_factory=dict)
    refs: dict[str, dict] = field(default_factory=dict)  # canonical / hreflang targets that weren't crawled
    blocked: dict[str, str] = field(default_factory=dict)  # internal URL blocked by robots.txt -> linking page
    ai_access: list[dict] = field(default_factory=list)
    truncated: bool = False
    fatal: str | None = None
    stats: dict = field(default_factory=dict)


ProgressFn = Callable[[str], Awaitable[None]]


class SiteCrawler:
    def __init__(self, cfg: AuditConfig, progress: ProgressFn | None = None) -> None:
        self.cfg = cfg
        self.progress = progress
        self.safe = _SafeHosts()
        self.data = CrawlData(start_url=cfg.start_url)
        self.rp: RobotFileParser | None = None
        self.delay = BASE_DELAY

    async def _say(self, msg: str) -> None:
        if self.progress:
            try:
                await self.progress(msg)
            except Exception:  # noqa: BLE001
                pass

    def in_scope(self, url: str) -> bool:
        return domain_of(url) == self.data.domain

    def wanted(self, url: str) -> bool:
        if url == self.data.home:
            return True
        if self.cfg.include and not any(_match(p, url) for p in self.cfg.include):
            return False
        return not any(_match(p, url) for p in self.cfg.exclude)

    def allowed(self, url: str) -> bool:
        if self.rp is None:
            return True
        try:
            return self.rp.can_fetch(self.cfg.user_agent, url)
        except Exception:  # noqa: BLE001
            return True

    # -------------------------------------------------------------- site-wide files
    async def _robots(self, client: httpx.AsyncClient) -> None:
        d = self.data
        r = await fetch(client, urljoin(d.origin, "/robots.txt"), self.safe)
        text = None
        if r.error is None and r.status == 200 and r.body:
            text = r.text()
        d.robots = robots_report(text, r.status, r.error, d.origin, self.cfg.user_agent)
        rp = RobotFileParser()
        if r.status >= 500 or (r.status == 0 and r.error):
            rp.disallow_all = True  # RFC 9309: unreachable robots.txt = assume full disallow
            d.robots["unreachable"] = True
            self.rp = None  # but an audit of your own site still reads pages so you can fix them
        elif text is None:
            rp.allow_all = True
            self.rp = rp
        else:
            rp.parse(text.splitlines())
            self.rp = rp
            cd = d.robots.get("crawl_delay")
            if cd:
                self.delay = max(BASE_DELAY, min(float(cd), 5.0))
        d.ai_access = []
        for token, owner, purpose in AI_BOTS:
            acc = bot_access(text, text is not None, d.origin, token)
            d.ai_access.append({"bot": token, "owner": owner, "purpose": purpose, **acc})

    async def _sitemaps(self, client: httpx.AsyncClient) -> None:
        d = self.data
        declared = [normalize_url(u) for u in d.robots.get("sitemaps", [])]
        declared = [u for u in declared if u]
        defaults = [urljoin(d.origin, "/sitemap.xml"), urljoin(d.origin, "/sitemap_index.xml")]
        todo = deque([(u, True) for u in declared] + [(u, False) for u in defaults if u not in declared])
        seen: set[str] = set()
        while todo and len(seen) < SITEMAP_LIMIT:
            sm, is_declared = todo.popleft()
            if sm in seen:
                continue
            seen.add(sm)
            r = await fetch(client, sm, self.safe)
            rec = {"url": sm, "declared": is_declared, "status": r.status, "error": r.error, "urls": 0, "children": 0,
                   "redirected": bool(r.chain)}
            if r.error or r.status != 200 or not r.body:
                if is_declared:
                    rec["error"] = r.error or f"HTTP {r.status}"
                    d.sitemaps.append(rec)
                continue
            if r.is_html and not r.body.lstrip().startswith(b"<?xml"):
                if is_declared:
                    rec["error"] = "Returns a web page instead of an XML sitemap"
                    d.sitemaps.append(rec)
                continue
            pages, children, err = parse_sitemap(r.body)
            rec.update(urls=len(pages), children=len(children), error=err, bytes=len(r.body))
            future = 0
            soon = datetime.now(UTC) + timedelta(days=2)
            off_site = 0
            for loc, lastmod in pages:
                n = normalize_url(loc)
                if not n:
                    continue
                if not self.in_scope(n) or urlsplit(n).scheme != urlsplit(d.home).scheme:
                    off_site += 1
                if lastmod:
                    try:
                        lm = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
                        if (lm if lm.tzinfo else lm.replace(tzinfo=UTC)) > soon:
                            future += 1
                    except ValueError:
                        pass
                if len(d.sitemap_urls) < SITEMAP_URL_LIMIT:
                    d.sitemap_urls.setdefault(n, {"sitemap": sm, "lastmod": lastmod})
            rec["future_lastmod"] = future
            rec["off_site"] = off_site
            d.sitemaps.append(rec)
            for c in children[:50]:
                n = normalize_url(c)
                if n:
                    todo.append((n, True))

    async def _extras(self, client: httpx.AsyncClient) -> None:
        d = self.data
        r = await fetch(client, urljoin(d.origin, "/llms.txt"), self.safe)
        body = r.text() if r.status == 200 and r.body else ""
        d.llms = {"url": urljoin(d.origin, "/llms.txt"), "status": r.status, "found": r.status == 200 and bool(body.strip()),
                  "content_type": r.content_type, "size": len(body.encode()),
                  "has_title": bool(re.search(r"^# \S", body, re.MULTILINE)), "links": len(re.findall(r"\]\(", body)),
                  "html": r.is_html and "<html" in body[:500].lower()}
        f = await fetch(client, urljoin(d.origin, "/favicon.ico"), self.safe, method="HEAD", read_body=False)
        d.favicon = f.status == 200
        # http:// → https://
        p = urlsplit(d.home)
        if p.scheme == "https":
            hv = await fetch(client, urlunsplit(("http", p.netloc, "/", "", "")), self.safe, read_body=False)
            target = hv.final_url if hv.chain else None
            d.http_variant = {"url": urlunsplit(("http", p.netloc, "/", "", "")), "status": hv.status, "error": hv.error,
                              "redirects_to": target, "first_status": hv.chain[0]["status"] if hv.chain else hv.status,
                              "ok": bool(target and target.startswith("https://") and self.in_scope(target))}
        # www ↔ apex
        host = p.hostname or ""
        alt = host[4:] if host.startswith("www.") else f"www.{host}"
        if host.count(".") >= 1 and not crawler._is_ip(host):
            av = await fetch(client, urlunsplit((p.scheme, alt, "/", "", "")), self.safe, read_body=False)
            d.host_variant = {"url": urlunsplit((p.scheme, alt, "/", "", "")), "status": av.status, "error": av.error,
                              "redirects_to": av.final_url if av.chain else None,
                              "duplicate": av.error is None and av.status == 200 and not av.chain}

    # -------------------------------------------------------------- pages
    def _record(self, url: str, r: Resp, depth: int | None, source: str | None) -> dict:
        rec = {"url": url, "final_url": r.final_url, "status": r.chain[0]["status"] if r.chain else r.status,
               "final_status": r.status, "chain": r.chain, "loop": r.loop, "error": r.error,
               "content_type": r.content_type, "ttfb_ms": r.ttfb_ms, "total_ms": r.total_ms, "size": r.size,
               "headers": r.headers, "depth": depth, "found_from": source, "is_html": False}
        return rec

    async def _page(self, client: httpx.AsyncClient, url: str, depth: int | None, source: str | None) -> list[dict]:
        r = await fetch(client, url, self.safe)
        recs = [self._record(url, r, depth, source)]
        target = recs[0]
        if r.chain and not r.loop and r.error is None:
            final = r.final_url
            if self.in_scope(final) and final not in self.data.pages and final != url:
                fin = self._record(final, Resp(url=final, final_url=final, status=r.status, content_type=r.content_type,
                                               headers=r.headers, body=r.body, size=r.size, ttfb_ms=r.ttfb_ms,
                                               total_ms=r.total_ms), depth, url)
                recs.append(fin)
                target = fin
            else:
                target = None
        if target is not None and r.status == 200 and r.is_html and r.body:
            html = r.text()
            facts = await asyncio.to_thread(parse_page, html, target["url"], self.data.domain)
            target["is_html"] = True
            target["facts"] = facts
            target["html_bytes"] = len(r.body)
        return recs

    async def run(self) -> CrawlData:
        d = self.data
        async with make_client(self.cfg.user_agent) as client:
            first = await fetch(client, self.cfg.start_url, self.safe, read_body=False)
            if first.error and first.status == 0:
                d.fatal = f"The website couldn't be reached: {first.error}"
                return d
            if first.status >= 400:
                hint = " The site may block automated visitors." if first.status in (401, 403, 429) else ""
                d.fatal = f"The home page answered with HTTP {first.status}.{hint}"
                return d
            home = normalize_url(first.final_url) or self.cfg.start_url
            p = urlsplit(home)
            d.home, d.domain = home, domain_of(home)
            d.origin = urlunsplit((p.scheme, p.netloc, "/", "", ""))
            await self._say("Reading robots.txt and sitemaps")
            await self._robots(client)
            await self._sitemaps(client)
            await self._extras(client)

            queue: deque[tuple[str, int | None, str | None]] = deque()
            seen: set[str] = set()

            def push(u: str, depth: int | None, src: str | None) -> None:
                if u in seen or len(seen) >= MAX_QUEUE:
                    return
                seen.add(u)
                queue.append((u, depth, src))

            push(self.cfg.start_url if self.cfg.start_url == home else home, 0, None)
            if self.cfg.start_url != home:
                push(self.cfg.start_url, 0, None)
            sitemap_queue = [u for u in d.sitemap_urls if self.in_scope(u) and not has_skipped_extension(u)]
            sitemap_added = False
            fetched = 0
            while (queue or not sitemap_added) and fetched < self.cfg.max_pages:
                if not queue:
                    sitemap_added = True
                    for u in sitemap_queue:
                        if self.wanted(u) and self.allowed(u):
                            push(u, None, "sitemap")
                    continue
                batch: list[tuple[str, int | None, str | None]] = []
                while queue and len(batch) < min(CONCURRENCY, self.cfg.max_pages - fetched):
                    u, depth, src = queue.popleft()
                    if u in d.pages:
                        continue
                    if not self.allowed(u):
                        d.blocked.setdefault(u, src or "")
                        continue
                    batch.append((u, depth, src))
                if not batch:
                    continue
                results = await asyncio.gather(*(self._page(client, u, dep, src) for u, dep, src in batch),
                                               return_exceptions=True)
                for (u, dep, src), out in zip(batch, results, strict=True):
                    fetched += 1
                    if isinstance(out, BaseException):
                        d.pages[u] = {"url": u, "status": 0, "error": f"{type(out).__name__}", "depth": dep, "chain": [],
                                      "is_html": False, "found_from": src}
                        d.order.append(u)
                        continue
                    for rec in out:
                        if rec["url"] in d.pages:
                            continue
                        d.pages[rec["url"]] = rec
                        d.order.append(rec["url"])
                        seen.add(rec["url"])
                        facts = rec.get("facts")
                        if not facts:
                            continue
                        nd = (rec["depth"] + 1) if rec.get("depth") is not None else None
                        if self._noindex_nofollow(rec):
                            continue
                        for link in facts["internal_links"]:
                            if not self.in_scope(link) or link in seen:
                                continue
                            if has_skipped_extension(link):
                                if len(d.resources) < RESOURCE_LIMIT:
                                    d.resources.setdefault(link, {"from": rec["url"]})
                                continue
                            if not self.wanted(link):
                                continue
                            if nd is not None and nd > self.cfg.max_depth:
                                continue
                            if not self.allowed(link):
                                d.blocked.setdefault(link, rec["url"])
                                seen.add(link)
                                continue
                            push(link, nd, rec["url"])
                await self._say(f"Crawled {fetched} of up to {self.cfg.max_pages} pages")
                if self.delay:
                    await asyncio.sleep(self.delay)
            d.truncated = bool(queue) or (not sitemap_added and any(u not in d.pages for u in sitemap_queue))
            d.stats["fetched"] = fetched
            d.stats["sitemap_blocked"] = [u for u in sitemap_queue if not self.allowed(u)][:200]

        await self._check_targets()
        return d

    @staticmethod
    def _noindex_nofollow(rec: dict) -> bool:
        robots = (rec.get("facts") or {}).get("meta_robots", "") + "," + (rec.get("headers") or {}).get("x-robots-tag", "").lower()
        return "nofollow" in robots or "none" in [x.strip() for x in robots.split(",")]

    async def _check_targets(self) -> None:
        """HEAD-check external links, images, og:images and linked files, within caps."""
        d = self.data
        ext = Counter()
        img = Counter()
        for rec in d.pages.values():
            f = rec.get("facts") or {}
            for u in f.get("external_links", []):
                ext[u] += 1
            for i in f.get("images", []):
                img[i["src"]] += 1
            if og := (f.get("og") or {}).get("og:image"):
                if n := normalize_url(og, rec["url"]):
                    img[n] += 1
        refs: dict[str, None] = {}
        for rec in d.pages.values():
            f = rec.get("facts") or {}
            for u in (f.get("canonicals") or []) + [h["href"] for h in f.get("hreflang") or [] if h.get("href")]:
                if u not in d.pages and u != rec["url"]:
                    refs.setdefault(u)
        targets: list[tuple[str, str]] = []
        if self.cfg.check_external:
            targets += [("external", u) for u, _ in ext.most_common(EXTERNAL_LIMIT)]
        targets += [("image", u) for u, _ in img.most_common(IMAGE_LIMIT) if u not in d.pages]
        targets += [("resource", u) for u in list(d.resources)[:RESOURCE_LIMIT]]
        targets += [("ref", u) for u in list(refs)[:RESOURCE_LIMIT]]
        if not targets:
            return
        await self._say(f"Checking {len(targets)} links, images and files")
        sem = asyncio.Semaphore(8)
        async with make_client(self.cfg.user_agent, CHECK_TIMEOUT) as client:
            async def one(kind: str, u: str) -> None:
                async with sem:
                    res = await check_url(client, u, self.safe)
                if kind == "external":
                    d.external[u] = res
                elif kind == "image":
                    d.images[u] = res
                elif kind == "ref":
                    d.refs[u] = res
                else:
                    d.resources[u] = {**d.resources.get(u, {}), **res}

            await asyncio.gather(*(one(k, u) for k, u in targets), return_exceptions=True)


async def crawl_site(cfg: AuditConfig, progress: ProgressFn | None = None) -> CrawlData:
    """Raises crawler.UnsafeURLError for a non-public start URL; otherwise never raises."""
    cfg.start_url = await crawler.check_public_url(cfg.start_url)
    return await SiteCrawler(cfg, progress).run()
