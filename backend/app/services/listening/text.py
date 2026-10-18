"""Matching, normalisation and term statistics for listening. Pure functions (no I/O)."""

import hashlib
import html
import re
from collections import Counter
from urllib.parse import parse_qsl, urlsplit

from app.services.inbox.base import normalize_url

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", s))).strip()


def clean_terms(values, limit: int = 20, max_len: int = 80) -> list[str]:
    out: list[str] = []
    for v in values or []:
        t = _WS.sub(" ", str(v or "")).strip().strip('"').strip()
        if t and len(t) <= max_len and t.lower() not in {x.lower() for x in out}:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def _pattern(term: str) -> re.Pattern:
    t = term.lower().strip()
    if t.startswith("#"):
        t = t[1:]
    body = re.escape(t).replace(r"\ ", r"[\s\-_]*")
    # word boundaries on both sides so "HRP" doesn't match "sharp"; hashtags and @handles also match
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.I)


def find_terms(text: str, terms: list[str]) -> list[str]:
    """The terms that occur in the text (case-insensitive, whole words; spaces also match hyphens or nothing
    so "HRP AI" matches "hrp-ai" and "HRPAI")."""
    if not text:
        return []
    return [t for t in terms if t and _pattern(t).search(text)]


def prefilter(text: str, terms: list[str], context_terms: list[str], exclude: list[str]) -> tuple[bool, list[str], str | None]:
    """(keep, matched terms, reason when dropped)."""
    matched = find_terms(text, terms)
    if not matched:
        return False, [], "no_term"
    if context_terms and not find_terms(text, context_terms):
        return False, matched, "no_context"
    if exclude and find_terms(text, exclude):
        return False, matched, "excluded"
    return True, matched, None


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    return host.removeprefix("www.").removeprefix("m.").removeprefix("old.") or None


def canonical_url(url: str | None) -> str | None:
    """normalize_url plus a few listening-specific rules (Reddit hosts, YouTube watch ids, Google redirectors)."""
    if not url:
        return None
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (p.hostname or "").lower()
    if host in ("www.google.com", "google.com") and p.path == "/url":
        q = dict(parse_qsl(p.query))
        if q.get("q") or q.get("url"):
            return canonical_url(q.get("q") or q.get("url"))
    if host in ("youtu.be",):
        return f"https://youtube.com/watch?v={p.path.strip('/')}"
    if host.endswith("youtube.com") and p.path == "/watch":
        v = dict(parse_qsl(p.query)).get("v")
        if v:
            return f"https://youtube.com/watch?v={v}"
    n = normalize_url(url) or url
    for prefix in ("https://old.reddit.com", "https://m.reddit.com", "https://np.reddit.com"):
        if n.startswith(prefix):
            n = "https://reddit.com" + n[len(prefix):]
    if n.startswith("http://"):
        n = "https://" + n[7:]
    return n


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


def content_hash(title: str | None, text: str | None, publisher: str | None = None) -> str | None:
    """Hash of the normalized headline (or the first 300 characters of the text). Used to drop the same story
    found under different URLs (Google News redirect links, syndicated copies). None for very short texts."""
    base = (title or "").strip()
    if publisher and base.lower().endswith(f" - {publisher.lower()}"):
        base = base[: -(len(publisher) + 3)]
    base = re.sub(r"\s+[-|–]\s+[^-|–]{2,60}$", "", base) if base else base  # trailing " - Publisher"
    if len(base) < 25:
        base = f"{base} {text or ''}".strip()
    norm = re.sub(r"[^\w]+", " ", base.lower()).strip()[:300]
    return sha1(norm) if len(norm) >= 25 else None


def dedupe_key(source: str, url: str | None, external_id: str | None) -> str:
    canon = canonical_url(url)
    if canon:
        return "u:" + sha1(canon)
    return "x:" + sha1(f"{source}:{external_id or ''}")


def as_hashtag(term: str) -> str | None:
    t = re.sub(r"[\s\-_.]+", "", term.strip().lstrip("#"))
    return t.lower() if re.fullmatch(r"\w{2,60}", t or "") else None


def quote(term: str) -> str:
    t = term.replace('"', "").strip()
    return f'"{t}"' if re.search(r"[\s\-.]", t) else t


def or_query(terms: list[str], *, quote_all: bool = False) -> str:
    parts = [f'"{t.replace(chr(34), "")}"' if quote_all else quote(t) for t in terms if t]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return "(" + " OR ".join(parts) + ")"


# ------------------------------------------------------------------ top terms ("word cloud")
STOP = set("""
a about above after again against all also am an and any are aren't as at be because been before being below between
both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from
further get got had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself
his how how's i i'd i'll i'm i've if in into is isn't it it's its itself just let's like me more most mustn't my myself
new no nor not now of off on once one only or other ought our ours ourselves out over own per really same say says said
shan't she she'd she'll she's should shouldn't so some such than that that's the their theirs them themselves then there
there's these they they'd they'll they're they've this those through to too under until up us use used using very via
was wasn't we we'd we'll we're we've were weren't what what's when when's where where's which while who who's whom why
why's will with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves http https www com
amp quot rt via yet still even much many well way make made know think going want need see look one two three also
today year years time week day days news read full story according told may might must shall new latest top best
""".split())


def top_terms(texts: list[str], *, exclude: set[str] | None = None, n: int = 40) -> list[dict]:
    """Most frequent words and two-word phrases across the texts, ignoring stop words and the tracked terms.
    Each document counts a term once, so one long post can't dominate."""
    ex = {e.lower() for e in (exclude or set())}
    ex_words = {w for e in ex for w in re.findall(r"\w+", e)}
    counts: Counter = Counter()
    for t in texts:
        words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9+#'\-]{2,}", (t or "").lower())]
        words = [w.strip("'-") for w in words]
        seen: set[str] = set()
        for i, w in enumerate(words):
            if w in STOP or w in ex_words or len(w) < 3:
                continue
            seen.add(w)
            if i + 1 < len(words):
                w2 = words[i + 1]
                if w2 not in STOP and w2 not in ex_words and len(w2) >= 3:
                    seen.add(f"{w} {w2}")
        counts.update(seen)
    rows = [(k, c) for k, c in counts.items() if c >= 2 and k not in ex]
    # prefer a phrase over its single words when it is nearly as common
    phrases = {k: c for k, c in rows if " " in k}
    out = []
    for k, c in sorted(rows, key=lambda x: (-x[1], x[0])):
        if " " not in k and any(k in p.split() and pc >= c * 0.8 for p, pc in phrases.items()):
            continue
        out.append({"term": k, "count": c})
        if len(out) >= n:
            break
    return out
