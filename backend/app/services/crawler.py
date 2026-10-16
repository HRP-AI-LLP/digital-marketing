"""Polite website crawler for Company Intelligence.

- Reads robots.txt (urllib.robotparser) and never fetches disallowed URLs.
- Seeds from sitemap.xml (and sitemap indexes / robots.txt `Sitemap:` lines), then follows links
  within the same registrable domain (www.example.co.in and blog.example.co.in count as one site).
- 4 requests in flight at most, 15 s timeout, an honest User-Agent, binaries skipped.
- HTML → clean text with trafilatura (BeautifulSoup fallback) plus SEO facts: title, meta description,
  h1/h2, canonical, Open Graph, JSON-LD, links, social profiles, images without alt text, dates.
- Pages that come back almost empty (JavaScript apps) are rendered with Playwright when available.
- SSRF protection: only http(s) URLs whose host resolves to public IP addresses are fetched,
  re-checked on every redirect hop and for every request the headless browser makes.

One bad page never fails a crawl: errors are collected in `CrawlResult.errors`.
"""

import asyncio
import glob
import gzip
import ipaddress
import json
import logging
import re
import socket
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

USER_AGENT = "HRP-DigitalMarketing-Studio/1.0 (+contact site owner)"
TIMEOUT = httpx.Timeout(15.0)
CONCURRENCY = 4
DEFAULT_MAX_PAGES = 150
MAX_BYTES = 4_000_000
MAX_REDIRECTS = 5
MAX_RENDERS = 25
MIN_TEXT_FOR_NO_RENDER = 200
MAX_QUEUE = 5000

# Tests replace this with httpx.MockTransport.
TRANSPORT: httpx.AsyncBaseTransport | None = None

SKIP_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tif", ".tiff", ".avif",
    ".mp4", ".mov", ".avi", ".webm", ".mkv", ".mp3", ".wav", ".ogg", ".m4a",
    ".zip", ".rar", ".7z", ".gz", ".tar", ".tgz", ".exe", ".dmg", ".msi", ".apk", ".iso", ".bin",
    ".css", ".js", ".mjs", ".json", ".map", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".rss", ".atom", ".xml",
}
TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "igshid", "yclid", "ref_src"}

SOCIAL_HOSTS: dict[str, str] = {
    "linkedin.com": "linkedin", "facebook.com": "facebook", "fb.com": "facebook", "instagram.com": "instagram",
    "x.com": "x", "twitter.com": "x", "youtube.com": "youtube", "youtu.be": "youtube", "pinterest.com": "pinterest",
    "pinterest.co.uk": "pinterest", "pin.it": "pinterest", "in.pinterest.com": "pinterest",
}
_SHARE_HINTS = ("sharer", "/share", "intent/tweet", "sharearticle", "/pin/create", "dialog/feed", "share.php", "/hashtag/")

# Public suffixes with two labels that matter for the markets we serve (no network-loaded PSL).
_MULTI_SUFFIXES = {
    "co.in", "org.in", "net.in", "firm.in", "gen.in", "ind.in", "ac.in", "edu.in", "gov.in", "res.in", "nic.in",
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "co.za",
    "com.br", "com.sg", "edu.sg", "com.my", "com.mx", "com.cn", "com.hk", "com.tr", "co.kr", "com.ar", "com.ph",
    "com.pk", "com.bd", "com.ng", "co.id", "com.vn", "com.tw", "com.sa", "com.eg", "co.ke", "com.np", "com.lk",
    "co.th", "co.il", "com.co", "com.pe", "com.qa", "com.kw", "com.om", "com.bh", "ae.org", "us.com", "uk.com",
}


class UnsafeURLError(ValueError):
    """The URL isn't a public http(s) address."""


# ------------------------------------------------------------------ URL helpers
def registrable_domain(host: str) -> str:
    """'blog.www.example.co.in' -> 'example.co.in'. IP addresses are returned as-is."""
    host = (host or "").lower().strip(".")
    if host.startswith("[") or _is_ip(host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def domain_of(url: str) -> str:
    try:
        return registrable_domain(urlsplit(url).hostname or "")
    except ValueError:
        return ""


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Absolute, fragment-free URL with a lower-case host and no tracking parameters; None if unusable."""
    url = (url or "").strip()
    if not url:
        return None
    if base:
        url = urljoin(base, url)
    elif "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https") or not parts.hostname:
            return None
        host = parts.hostname.lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    host_part = f"[{host}]" if ":" in host else host
    default_port = port is None or (scheme, port) in (("http", 80), ("https", 443))
    netloc = host_part if default_port else f"{host_part}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ])
    return urlunsplit((scheme, netloc, path, query, ""))


def has_skipped_extension(url: str) -> bool:
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    return dot > path.rfind("/") and path[dot:] in SKIP_EXTENSIONS


def social_platform(url: str) -> str | None:
    """Platform name if the URL is a social profile (not a share button)."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    host = host.removeprefix("www.")
    host = host.removeprefix("m.")
    platform = SOCIAL_HOSTS.get(host) or SOCIAL_HOSTS.get(registrable_domain(host))
    if not platform:
        return None
    low = url.lower()
    if any(h in low for h in _SHARE_HINTS):
        return None
    if urlsplit(url).path.strip("/") == "":
        return None
    return platform


# ------------------------------------------------------------------ SSRF protection
async def resolve_host(host: str) -> list[str]:
    """IP addresses for a host name. Tests monkeypatch this."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list({info[4][0] for info in infos})


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified or not addr.is_global)


async def check_public_url(url: str) -> str:
    """Return the normalized URL, or raise UnsafeURLError when it isn't a public http(s) address."""
    norm = normalize_url(url)
    if not norm:
        raise UnsafeURLError("Enter a full web address that starts with http:// or https://.")
    raw = (url or "").strip()
    if "@" in urlsplit(raw if "://" in raw else f"https://{raw}").netloc:
        raise UnsafeURLError("Web addresses with a user name or password aren't allowed.")
    parts = urlsplit(norm)
    if parts.port not in (None, 80, 443, 8080, 8443):
        raise UnsafeURLError("Only standard web ports (80, 443, 8080, 8443) can be crawled.")
    host = (parts.hostname or "").lower()
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        raise UnsafeURLError("Local and private network addresses can't be crawled.")
    if _is_ip(host):
        ips = [host.strip("[]")]
    else:
        try:
            ips = await resolve_host(host)
        except (OSError, UnicodeError):
            raise UnsafeURLError(f"The address {host} couldn't be found. Check the spelling.")
    if not ips or not all(_ip_is_public(ip) for ip in ips):
        raise UnsafeURLError("Local and private network addresses can't be crawled.")
    return norm


# ------------------------------------------------------------------ page model & HTML parsing
@dataclass
class Page:
    url: str
    status: int = 0
    title: str = ""
    description: str = ""
    text: str = ""
    h1: list[str] = field(default_factory=list)
    h2: list[str] = field(default_factory=list)
    canonical: str | None = None
    og: dict[str, str] = field(default_factory=dict)
    jsonld_types: list[str] = field(default_factory=list)
    jsonld_same_as: list[str] = field(default_factory=list)
    links_internal: list[str] = field(default_factory=list)
    links_external: list[str] = field(default_factory=list)
    social: dict[str, str] = field(default_factory=dict)
    images_total: int = 0
    images_missing_alt: int = 0
    dates: list[str] = field(default_factory=list)
    lang: str | None = None
    rendered: bool = False

    def seo_meta(self) -> dict:
        """Compact facts stored on KnowledgeDocument.meta (used by the presence audit)."""
        d = asdict(self)
        for k in ("text", "url", "links_internal", "links_external"):
            d.pop(k, None)
        d["internal_link_count"] = len(self.links_internal)
        d["external_link_count"] = len(self.links_external)
        d["text_chars"] = len(self.text)
        return d


def _clean_text(text: str) -> str:
    text = text.replace("\r", "").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_DATE = re.compile(r"(19|20)\d{2}-\d{2}-\d{2}")


def _walk_jsonld(obj, types: list[str], same_as: list[str], dates: list[str]) -> None:
    if isinstance(obj, list):
        for o in obj[:50]:
            _walk_jsonld(o, types, same_as, dates)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        for x in (t if isinstance(t, list) else [t]):
            if isinstance(x, str) and x not in types:
                types.append(x)
        sa = obj.get("sameAs")
        for x in (sa if isinstance(sa, list) else [sa]):
            if isinstance(x, str) and x.startswith("http"):
                same_as.append(x)
        for k in ("datePublished", "dateModified", "uploadDate"):
            v = obj.get(k)
            if isinstance(v, str) and (m := _DATE.search(v)):
                dates.append(m.group(0))
        for k in ("@graph", "mainEntity", "publisher", "author", "itemListElement"):
            if k in obj:
                _walk_jsonld(obj[k], types, same_as, dates)


def _fallback_text(soup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe", "form"]):
        tag.decompose()
    body = soup.find("main") or soup.find("article") or soup.body or soup
    return _clean_text(body.get_text("\n"))


def parse_html(html: str, url: str, site_domain: str | None = None) -> Page:
    """Extract text and SEO facts from one HTML document. Never raises on bad markup."""
    from bs4 import BeautifulSoup

    page = Page(url=url)
    site_domain = site_domain or domain_of(url)
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    def meta(attr: str, value: str) -> str:
        tag = soup.find("meta", attrs={attr: re.compile(f"^{re.escape(value)}$", re.IGNORECASE)})
        return (tag.get("content") or "").strip() if tag else ""

    page.title = _clean_text(soup.title.get_text(" ")) if soup.title else ""
    page.description = meta("name", "description")
    page.h1 = [t for t in (_clean_text(h.get_text(" ")) for h in soup.find_all("h1")) if t][:10]
    page.h2 = [t for t in (_clean_text(h.get_text(" ")) for h in soup.find_all("h2")) if t][:30]
    html_tag = soup.find("html")
    page.lang = (html_tag.get("lang") or None) if html_tag else None
    canon = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
    if canon and canon.get("href"):
        page.canonical = normalize_url(canon["href"], url)
    for tag in soup.find_all("meta", attrs={"property": re.compile(r"^og:", re.IGNORECASE)}):
        k, v = (tag.get("property") or "").lower(), (tag.get("content") or "").strip()
        if k and v and k not in page.og:
            page.og[k] = v[:500]

    dates: list[str] = []
    for attr, name in (("property", "article:published_time"), ("property", "article:modified_time"),
                       ("property", "og:updated_time"), ("itemprop", "datePublished"), ("itemprop", "dateModified"),
                       ("name", "date"), ("name", "publish_date")):
        if (v := meta(attr, name)) and (m := _DATE.search(v)):
            dates.append(m.group(0))
    for t in soup.find_all("time", attrs={"datetime": True})[:20]:
        if m := _DATE.search(t.get("datetime") or ""):
            dates.append(m.group(0))
    for s in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.IGNORECASE)}):
        try:
            data = json.loads(s.string or s.get_text() or "", strict=False)
        except (ValueError, TypeError):
            continue
        _walk_jsonld(data, page.jsonld_types, page.jsonld_same_as, dates)
    page.dates = sorted(set(dates), reverse=True)[:10]

    internal: dict[str, None] = {}
    external: dict[str, None] = {}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "sms:", "whatsapp:")):
            continue
        absu = normalize_url(href, url)
        if not absu:
            continue
        if (plat := social_platform(absu)) and plat not in page.social:
            page.social[plat] = absu
        if domain_of(absu) == site_domain:
            internal.setdefault(absu)
        else:
            external.setdefault(absu)
    for u in page.jsonld_same_as:
        if (plat := social_platform(u)) and plat not in page.social:
            page.social[plat] = u
    page.links_internal = list(internal)[:500]
    page.links_external = list(external)[:200]

    imgs = soup.find_all("img")
    page.images_total = len(imgs)
    page.images_missing_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())

    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True,
                                   favor_recall=True, deduplicate=True) or ""
    except Exception:
        text = ""
    fallback = _fallback_text(soup)
    # Trafilatura is tuned for articles; on landing pages it can drop most content.
    if len(text) < 500 and len(fallback) > 2 * max(len(text), 1):
        text = fallback
    page.text = _clean_text(text)[:200_000]
    return page


# ------------------------------------------------------------------ fetching
@dataclass
class Fetched:
    url: str
    final_url: str
    status: int
    content_type: str = ""
    body: bytes = b""
    error: str | None = None

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


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=False, transport=TRANSPORT,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                 "Accept-Language": "en;q=0.9"},
    )


class _SafeHosts:
    """Per-crawl cache of hosts already checked against the SSRF rules."""

    def __init__(self) -> None:
        self.ok: set[str] = set()
        self.bad: set[str] = set()

    async def check(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        if host in self.ok:
            return True
        if host in self.bad:
            return False
        try:
            await check_public_url(url)
            self.ok.add(host)
            return True
        except UnsafeURLError:
            self.bad.add(host)
            return False


async def fetch(client: httpx.AsyncClient, url: str, safe: _SafeHosts | None = None) -> Fetched:
    """GET with manual redirects (each hop SSRF-checked) and a size cap. Never raises."""
    safe = safe or _SafeHosts()
    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not await safe.check(current):
                return Fetched(url, current, 0, error="Blocked: not a public web address")
            async with client.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    nxt = normalize_url(r.headers["location"], current)
                    if not nxt:
                        return Fetched(url, current, r.status_code, error="Bad redirect")
                    current = nxt
                    continue
                ct = r.headers.get("content-type", "")
                body = b""
                if r.status_code < 400 and ("html" in ct.lower() or "xml" in ct.lower() or "text" in ct.lower() or not ct):
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in r.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > MAX_BYTES:
                            break
                    body = b"".join(chunks)[:MAX_BYTES]
                return Fetched(url, current, r.status_code, ct, body)
        return Fetched(url, current, 0, error="Too many redirects")
    except httpx.TimeoutException:
        return Fetched(url, current, 0, error="Timed out after 15 seconds")
    except httpx.HTTPError as e:
        return Fetched(url, current, 0, error=f"{type(e).__name__}: {e}"[:300])
    except Exception as e:
        return Fetched(url, current, 0, error=f"{type(e).__name__}: {e}"[:300])


# ------------------------------------------------------------------ JavaScript rendering (optional)
def chromium_path() -> str | None:
    configured = (get_settings().playwright_chromium_path or "").strip()
    if configured:
        return configured
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return found[-1] if found else None


class _Renderer:
    """Lazily started headless Chromium. Disables itself after a launch failure."""

    def __init__(self, safe: _SafeHosts) -> None:
        self.safe = safe
        self.disabled = False
        self.count = 0
        self._pw = None
        self._browser = None

    async def render(self, url: str) -> str | None:
        if self.disabled or self.count >= MAX_RENDERS:
            return None
        self.count += 1
        try:
            if self._browser is None:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
                path = chromium_path()
                self._browser = await self._pw.chromium.launch(
                    headless=True, executable_path=path or None, args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
        except Exception as e:
            log.info("JavaScript rendering unavailable: %s", e)
            self.disabled = True
            await self.close()
            return None
        page = None
        try:
            page = await self._browser.new_page(user_agent=USER_AGENT)

            async def guard(route):
                req = route.request
                if req.resource_type in ("image", "media", "font") or not await self.safe.check(req.url):
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", guard)
            await page.goto(url, wait_until="networkidle", timeout=20_000)
            return await page.content()
        except Exception as e:
            log.debug("render failed for %s: %s", url, e)
            return None
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception:
            pass
        self._browser = self._pw = None


# ------------------------------------------------------------------ crawl
@dataclass
class CrawlResult:
    start_url: str
    pages: list[Page] = field(default_factory=list)
    robots_found: bool = False
    sitemap_found: bool = False
    sitemap_url_count: int = 0
    https: bool = False
    broken_links: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    blocked_by_robots: int = 0
    truncated: bool = False

    def summary(self) -> dict:
        return {
            "start_url": self.start_url, "pages": len(self.pages), "robots_found": self.robots_found,
            "sitemap_found": self.sitemap_found, "sitemap_url_count": self.sitemap_url_count, "https": self.https,
            "broken_links": self.broken_links[:25], "errors": self.errors[:25],
            "blocked_by_robots": self.blocked_by_robots, "truncated": self.truncated,
        }


_KEY_PAGE = re.compile(r"about|product|service|solution|studio|platform|client|customer|case|industr|pricing|"
                       r"feature|use-case|usecase|team|career|contact|partner|why", re.IGNORECASE)


def _url_priority(url: str) -> tuple[int, int, int]:
    path = urlsplit(url).path.strip("/")
    depth = path.count("/") + (1 if path else 0)
    return (0 if _KEY_PAGE.search(path) else 1, depth, len(path))


def _parse_sitemap(data: bytes) -> tuple[list[str], list[str]]:
    """(page URLs, child sitemap URLs). Tolerates gzip and broken XML."""
    from lxml import etree

    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            return [], []
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
        root = etree.fromstring(data, parser=parser)
    except Exception:
        return [], []
    if root is None:
        return [], []
    pages: list[str] = []
    children: list[str] = []
    is_index = etree.QName(root).localname.lower() == "sitemapindex"
    for el in root.iter():
        if not isinstance(el.tag, str) or etree.QName(el).localname.lower() != "loc" or not el.text:
            continue
        parent = el.getparent()
        pname = etree.QName(parent).localname.lower() if parent is not None and isinstance(parent.tag, str) else ""
        (children if is_index or pname == "sitemap" else pages).append(el.text.strip())
    return pages, children


class _Crawl:
    def __init__(self, client: httpx.AsyncClient, start: str, max_pages: int, render_js: bool,
                 progress: Callable[[str], Awaitable[None]] | None) -> None:
        self.client = client
        self.start = start
        self.max_pages = max_pages
        self.progress = progress
        self.safe = _SafeHosts()
        self.renderer = _Renderer(self.safe) if render_js else None
        self.domain = domain_of(start)
        self.robots: dict[str, RobotFileParser] = {}
        self.delay = 0.25
        self.result = CrawlResult(start_url=start)
        self.referrer: dict[str, str] = {}

    def in_scope(self, url: str) -> bool:
        return domain_of(url) == self.domain and not has_skipped_extension(url)

    async def _fetch_robots(self, origin: str) -> tuple[RobotFileParser, bool]:
        """robots.txt for one host (scheme://host[:port]). Returns (parser, found)."""
        rp = RobotFileParser()
        f = await fetch(self.client, urljoin(origin, "/robots.txt"), self.safe)
        if f.status >= 500 or (f.status == 0 and f.error and "Blocked" not in f.error):
            # RFC 9309: an unreachable robots.txt means "assume complete disallow" for now.
            rp.disallow_all = True
            return rp, False
        if f.status >= 400 or f.status == 0 or not f.body:  # RFC 9309: 4xx means there are no rules
            rp.allow_all = True
            return rp, False
        rp.parse(f.text().splitlines())
        return rp, True

    async def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self.robots:  # robots.txt applies per host, so subdomains get their own
            self.robots[origin], _ = await self._fetch_robots(origin)
        try:
            return self.robots[origin].can_fetch(USER_AGENT, url)
        except Exception:  # noqa: BLE001
            return True

    async def load_robots(self, origin: str) -> list[str]:
        rp, found = await self._fetch_robots(origin)
        self.robots[origin.rstrip("/")] = rp
        self.result.robots_found = found
        if not found:
            return []
        try:
            cd = rp.crawl_delay(USER_AGENT)
            if cd:
                self.delay = min(float(cd), 5.0)
        except (TypeError, ValueError):
            pass
        return list(rp.site_maps() or [])

    async def load_sitemaps(self, origin: str, declared: list[str]) -> list[str]:
        todo = deque(dict.fromkeys(declared + [urljoin(origin, "/sitemap.xml"), urljoin(origin, "/sitemap_index.xml")]))
        seen: set[str] = set()
        urls: dict[str, None] = {}
        while todo and len(seen) < 15 and len(urls) < 3000:
            sm = normalize_url(todo.popleft())
            if not sm or sm in seen:
                continue
            seen.add(sm)
            f = await fetch(self.client, sm, self.safe)
            if f.status != 200 or not f.body:
                continue
            pages, children = _parse_sitemap(f.body)
            if pages or children:
                self.result.sitemap_found = True
            for c in children[:50]:
                todo.append(c)
            for p in pages:
                n = normalize_url(p)
                if n and self.in_scope(n):
                    urls.setdefault(n)
        self.result.sitemap_url_count = len(urls)
        return sorted(urls, key=_url_priority)

    async def process(self, url: str) -> tuple[Page | None, list[str]]:
        f = await fetch(self.client, url, self.safe)
        if f.error:
            self.result.errors.append({"url": url, "error": f.error})
            return None, []
        if f.status >= 400:
            self.result.broken_links.append({"url": url, "status": f.status, "from": self.referrer.get(url)})
            return None, []
        if not f.is_html or not f.body:
            return None, []
        final = normalize_url(f.final_url) or url
        if not self.in_scope(final):
            return None, []
        html = f.text()
        page = await asyncio.to_thread(parse_html, html, final, self.domain)
        if len(page.text) < MIN_TEXT_FOR_NO_RENDER and self.renderer is not None:
            rendered = await self.renderer.render(final)
            if rendered:
                rpage = await asyncio.to_thread(parse_html, rendered, final, self.domain)
                if len(rpage.text) > len(page.text):
                    rpage.rendered = True
                    page = rpage
        page.status = f.status
        return page, page.links_internal

    async def run(self) -> CrawlResult:
        # Resolve the start URL's redirects first (http→https, apex→www) so scope follows the real site.
        first = await fetch(self.client, self.start, self.safe)
        if first.error and first.status == 0:
            self.result.errors.append({"url": self.start, "error": first.error})
            return self.result
        if first.status >= 400:
            hint = " The site may block automated visitors." if first.status in (401, 403, 429) else ""
            self.result.errors.append({"url": self.start, "error": f"The site answered with HTTP {first.status}.{hint}"})
            return self.result
        final = normalize_url(first.final_url) or self.start
        self.domain = domain_of(final)
        self.result.https = final.startswith("https://")
        origin = urlunsplit((urlsplit(final).scheme, urlsplit(final).netloc, "/", "", ""))

        declared = await self.load_robots(origin)
        sitemap_urls = await self.load_sitemaps(origin, declared)

        queue: deque[str] = deque([final])
        seen: set[str] = {final, self.start}
        for u in sitemap_urls:
            if u not in seen:
                seen.add(u)
                queue.append(u)

        while queue and len(self.result.pages) < self.max_pages:
            batch: list[str] = []
            while queue and len(batch) < min(CONCURRENCY, self.max_pages - len(self.result.pages)):
                u = queue.popleft()
                if not await self.allowed(u):
                    self.result.blocked_by_robots += 1
                    continue
                batch.append(u)
            if not batch:
                continue
            outcomes = await asyncio.gather(*(self.process(u) for u in batch), return_exceptions=True)
            for u, out in zip(batch, outcomes, strict=True):
                if isinstance(out, BaseException):
                    self.result.errors.append({"url": u, "error": f"{type(out).__name__}: {out}"[:300]})
                    continue
                page, links = out
                if page is None:
                    continue
                if len(self.result.pages) >= self.max_pages:
                    break
                if any(p.url == page.url for p in self.result.pages[-50:]):
                    continue  # two URLs redirected to the same page
                self.result.pages.append(page)
                for link in links:
                    if link not in seen and len(seen) < MAX_QUEUE and self.in_scope(link):
                        seen.add(link)
                        self.referrer[link] = page.url
                        queue.append(link)
            if self.progress:
                try:
                    await self.progress(f"Crawled {len(self.result.pages)} pages")
                except Exception:
                    pass
            await asyncio.sleep(self.delay)
        self.result.truncated = bool(queue) and len(self.result.pages) >= self.max_pages
        return self.result


async def crawl(start_url: str, *, max_pages: int | None = None, render_js: bool = True,
                progress: Callable[[str], Awaitable[None]] | None = None) -> CrawlResult:
    """Crawl one website. Raises UnsafeURLError for non-public start URLs; otherwise never raises."""
    start = await check_public_url(start_url)
    limit = max_pages or int(getattr(get_settings(), "crawl_max_pages", DEFAULT_MAX_PAGES) or DEFAULT_MAX_PAGES)
    async with make_client() as client:
        job = _Crawl(client, start, max(1, min(limit, 1000)), render_js, progress)
        try:
            return await job.run()
        finally:
            if job.renderer is not None:
                await job.renderer.close()


async def fetch_page(url: str, *, render_js: bool = False) -> Page | None:
    """Fetch and parse one page (used for competitor candidates). None on any failure."""
    try:
        start = await check_public_url(url)
    except UnsafeURLError:
        return None
    async with make_client() as client:
        safe = _SafeHosts()
        f = await fetch(client, start, safe)
        if f.error or f.status >= 400 or not f.is_html or not f.body:
            return None
        final = normalize_url(f.final_url) or start
        page = await asyncio.to_thread(parse_html, f.text(), final)
        if render_js and len(page.text) < MIN_TEXT_FOR_NO_RENDER:
            r = _Renderer(safe)
            try:
                html = await r.render(final)
            finally:
                await r.close()
            if html:
                rp = await asyncio.to_thread(parse_html, html, final)
                if len(rp.text) > len(page.text):
                    rp.rendered = True
                    page = rp
        page.status = f.status
        return page


_ABOUT = re.compile(r"/(about|about-us|company|who-we-are|our-story)(/|$)", re.IGNORECASE)


def find_about_link(page: Page) -> str | None:
    for link in page.links_internal:
        if _ABOUT.search(urlsplit(link).path):
            return link
    return None
