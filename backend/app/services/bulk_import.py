"""Bulk import of posts from a CSV file.

`preview(session, org, data)` reads the file and checks every row (account exists, time in the future, length within
the platform limit, post type supported, media exists) without writing anything. `commit(...)` checks again, downloads
images given by URL into the media library and creates all posts in the caller's transaction.

Approval follows the same rules as generated posts (app.services.content.route): "draft" rows stay drafts, "review"
rows wait for approval, "schedule" rows wait for approval unless auto-approval is on (Settings → Organization), and a
row that fails the quality checks is always held for a person.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentPillar, MediaAsset, Organization, Post, SocialAccount
from app.services import content as svc
from app.services.platforms import PLATFORMS, POST_TYPES, supported_types

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_PER_COMMIT = 60
MAX_REDIRECTS = 3

COLUMNS = ["account", "date", "time", "text", "hashtags", "link", "image_url", "media_id", "post_type", "pillar",
           "status", "title", "first_comment"]
ALIASES = {
    "account": "account", "account_id": "account", "account name": "account", "handle": "account", "page": "account",
    "date": "date", "day": "date", "time": "time", "text": "text", "caption": "text", "post": "text", "body": "text",
    "hashtags": "hashtags", "tags": "hashtags", "link": "link", "link_url": "link", "url": "link",
    "image_url": "image_url", "image": "image_url", "image url": "image_url", "media_url": "image_url",
    "media_id": "media_id", "media": "media_id", "media library id": "media_id", "media_ids": "media_id",
    "post_type": "post_type", "type": "post_type", "post type": "post_type", "pillar": "pillar",
    "status": "status", "title": "title", "first_comment": "first_comment", "first comment": "first_comment",
}
STATUS_ALIASES = {"": "draft", "draft": "draft", "review": "review", "in_review": "review", "approval": "review",
                  "for review": "review", "schedule": "schedule", "scheduled": "schedule", "publish": "schedule"}
TYPE_ALIASES = {"single image": "image", "photo": "image", "picture": "image", "pdf": "document", "slides": "carousel"}

TEMPLATE_ROWS = [
    ["Your account name", "2030-01-15", "10:00", "A short post written by you. Line breaks are kept.",
     "#YourTag #AnotherTag", "", "", "", "text", "", "draft", "", ""],
    ["linkedin_org", "2030-01-16", "17:30", "A post with an image downloaded from a public web address.",
     "YourTag", "https://example.com/page", "https://example.com/picture.jpg", "", "image", "", "review", "", ""],
]


class ImportError_(Exception):  # noqa: N801 - "ImportError" is a builtin
    """The file can't be read at all (message shown to the user)."""


@dataclass
class Row:
    n: int  # row number in the file (header = 1)
    raw: dict[str, str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    account: SocialAccount | None = None
    scheduled_at: datetime | None = None
    status: str = "draft"
    post_type: str = "text"
    text: str = ""
    hashtags: list[str] = field(default_factory=list)
    link: str | None = None
    title: str | None = None
    first_comment: str | None = None
    image_urls: list[str] = field(default_factory=list)
    media_ids: list[str] = field(default_factory=list)
    pillar_id: uuid.UUID | None = None
    outcome: str = ""  # what will happen on import

    def out(self, tz) -> dict:
        caption = svc.full_caption(self.text, self.hashtags)
        plat = svc.platform_of(self.account.platform) if self.account else None
        return {
            "row": self.n, "ok": not self.errors, "errors": self.errors, "warnings": self.warnings,
            "account": ({"id": str(self.account.id), "name": self.account.name, "platform": self.account.platform,
                         "platform_label": plat.label} if self.account else None),
            "account_input": self.raw.get("account", ""),
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "local_time": self.scheduled_at.astimezone(tz).strftime("%d %b %Y, %H:%M") if self.scheduled_at else None,
            "status": self.status, "post_type": self.post_type, "post_type_label": POST_TYPES.get(self.post_type, self.post_type),
            "text": self.text[:400], "hashtags": self.hashtags, "link": self.link, "images": len(self.image_urls) + len(self.media_ids),
            "char_count": len(caption), "max_chars": plat.max_chars if plat else None, "outcome": self.outcome,
        }


# ------------------------------------------------------------------ reading the file
def read_csv(data: bytes) -> list[dict[str, str]]:
    if not data or not data.strip():
        raise ImportError_("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ImportError_(f"The file is too large (limit {MAX_FILE_BYTES // 1048576} MB). Split it into smaller files.")
    if data[:2] == b"PK":
        raise ImportError_("This looks like an Excel file. In Excel choose File → Save As → CSV UTF-8, then upload that file.")
    text = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ImportError_("The file couldn't be read. Save it as CSV UTF-8 and try again.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader]
    while rows and not any(c.strip() for c in rows[0]):
        rows.pop(0)
    if not rows:
        raise ImportError_("The file is empty.")
    header = [ALIASES.get(h.strip().lower().replace("-", "_"), ALIASES.get(h.strip().lower(), "")) for h in rows[0]]
    if "account" not in header or "text" not in header:
        raise ImportError_("The first row must have column names, including at least “account” and “text”. "
                           "Download the template to see all the columns.")
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            out.append({})  # keep numbering; skipped later
            continue
        d: dict[str, str] = {}
        for i, key in enumerate(header):
            if key and i < len(r) and r[i].strip() and key not in d:
                d[key] = r[i].strip()
        out.append(d)
    if sum(1 for d in out if d) > MAX_ROWS:
        raise ImportError_(f"Import up to {MAX_ROWS} posts at a time. Split the file and import it in parts.")
    if not any(out):
        raise ImportError_("The file has column names but no posts.")
    return out


def template_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in TEMPLATE_ROWS:
        w.writerow(r)
    return buf.getvalue()


# ------------------------------------------------------------------ parsing values
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d")
_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H.%M")


def parse_date(v: str) -> date | None:
    v = v.strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(v, f).date()
        except ValueError:
            continue
    return None


def parse_time(v: str) -> time | None:
    v = v.strip().upper().replace(".", ":") if ":" in v or "." in v else v.strip().upper()
    for f in _TIME_FORMATS:
        try:
            return datetime.strptime(v, f).time()
        except ValueError:
            continue
    return None


def parse_tags(v: str) -> list[str]:
    out, seen = [], set()
    for t in re.split(r"[\s,;]+", v or ""):
        c = svc._clean_tag(t)
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out[:30]


def _split_list(v: str) -> list[str]:
    return [x for x in re.split(r"[\s|,]+", v or "") if x]


def _is_url(v: str) -> bool:
    try:
        p = urlsplit(v)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc) and " " not in v


# ------------------------------------------------------------------ lookups
@dataclass
class Lookups:
    accounts: list[SocialAccount]
    pillars: list[ContentPillar]
    media: set[str]
    tz: Any
    auto_approve: bool

    def account(self, v: str) -> tuple[SocialAccount | None, str | None]:
        v = (v or "").strip()
        if not v:
            return None, "Add the account (its name, handle or platform)."
        uid = svc.as_uuid(v)
        if uid:
            a = next((a for a in self.accounts if a.id == uid), None)
            return (a, None) if a else (None, "No account has this id.")
        low = v.lower()
        plat, _, rest = low.partition(":")
        pool = self.accounts
        if rest and plat in PLATFORMS:
            pool, low = [a for a in self.accounts if a.platform == plat], rest.strip()
        for test in (lambda a: a.name.lower() == low,
                     lambda a: (a.handle or "").lower().lstrip("@") == low.lstrip("@")):
            hits = [a for a in pool if test(a)]
            if len(hits) == 1:
                return hits[0], None
            if len(hits) > 1:
                return None, f"More than one account matches “{v}”. Use “platform:name” (for example linkedin_org:{v})."
        label_hits = [a for a in pool if low in (a.platform, PLATFORMS[a.platform].label.lower() if a.platform in PLATFORMS else "")]
        if len(label_hits) == 1:
            return label_hits[0], None
        if len(label_hits) > 1:
            names = ", ".join(a.name for a in label_hits[:4])
            return None, f"You have several {PLATFORMS[label_hits[0].platform].label} accounts ({names}). Use the account name."
        return None, f"No connected account matches “{v}”. Use the account name shown in Social accounts."

    def pillar(self, v: str) -> ContentPillar | None:
        uid = svc.as_uuid(v)
        for p in self.pillars:
            if (uid and p.id == uid) or p.name.lower() == v.strip().lower():
                return p
        return None


async def load_lookups(session: AsyncSession, org: Organization | None) -> Lookups:
    accounts = list((await session.scalars(
        select(SocialAccount).where(SocialAccount.status != "removed", SocialAccount.platform.in_(list(PLATFORMS)))
    )).all())
    pillars = list((await session.scalars(select(ContentPillar))).all())
    return Lookups(accounts, pillars, set(), svc.org_tz(org), svc.auto_approve_posts(org))


# ------------------------------------------------------------------ checking rows
def check_row(n: int, raw: dict[str, str], lk: Lookups, now: datetime) -> Row:
    row = Row(n=n, raw=raw)
    a, err = lk.account(raw.get("account", ""))
    row.account = a
    if err:
        row.errors.append(err)

    st = raw.get("status", "").strip().lower()
    if st not in STATUS_ALIASES:
        row.errors.append(f"Status “{raw.get('status')}” isn't one of draft, review or schedule.")
    row.status = STATUS_ALIASES.get(st, "draft")

    # time
    d_raw, t_raw = raw.get("date", ""), raw.get("time", "")
    if d_raw or t_raw:
        d = parse_date(d_raw) if d_raw else None
        t = parse_time(t_raw) if t_raw else None
        if d_raw and d is None:
            row.errors.append(f"Date “{d_raw}” isn't a date. Use YYYY-MM-DD (for example 2030-01-15).")
        elif not d_raw:
            row.errors.append("Add a date for this time.")
        if t_raw and t is None:
            row.errors.append(f"Time “{t_raw}” isn't a time. Use 24-hour HH:MM (for example 17:30).")
        if d and (t or not t_raw):
            if t is None:
                t = time(10, 0)
                row.warnings.append("No time given, so it's set to 10:00.")
            at = datetime.combine(d, t).replace(tzinfo=lk.tz)
            if at <= now:
                if row.status == "draft":
                    row.warnings.append("The time is in the past, so the draft is saved without a time.")
                else:
                    row.errors.append("The time is in the past. Pick a future date and time.")
            else:
                row.scheduled_at = at.astimezone(now.tzinfo)
    elif row.status in ("review", "schedule"):
        row.errors.append("Add a date and time, or set the status to draft.")

    # content
    row.text = (raw.get("text") or "").replace("\r\n", "\n").strip()[:20000]
    row.hashtags = parse_tags(raw.get("hashtags", ""))
    row.title = (raw.get("title") or "").strip()[:300] or None
    row.first_comment = (raw.get("first_comment") or "").strip()[:4000] or None
    link = (raw.get("link") or "").strip()
    if link:
        if _is_url(link):
            row.link = link
        else:
            row.errors.append("The link must be a full web address starting with https://")
    for u in _split_list(raw.get("image_url", "")):
        if _is_url(u):
            row.image_urls.append(u)
        else:
            row.errors.append(f"“{u[:80]}” isn't a web address. Image URLs must start with https://")
    for m in _split_list(raw.get("media_id", "")):
        uid = svc.as_uuid(m)
        if uid is None or str(uid) not in lk.media:
            row.errors.append(f"Media “{m[:40]}” isn't in the media library.")
        else:
            row.media_ids.append(str(uid))
    if raw.get("pillar"):
        p = lk.pillar(raw["pillar"])
        if p:
            row.pillar_id = p.id
        else:
            row.warnings.append(f"Pillar “{raw['pillar']}” doesn't exist, so the post has no pillar.")

    n_media = len(row.image_urls) + len(row.media_ids)
    if not row.text and not n_media:
        row.errors.append("Add the post text.")

    # post type
    t_in = (raw.get("post_type") or "").strip().lower().replace(" ", "_")
    t_in = TYPE_ALIASES.get(t_in.replace("_", " "), t_in)
    if a is not None:
        plat = svc.platform_of(a.platform)
        supported = supported_types(a.platform)
        if t_in:
            if t_in not in POST_TYPES:
                row.errors.append(f"Post type “{raw.get('post_type')}” isn't known. Use one of: {', '.join(POST_TYPES)}.")
            elif t_in not in supported:
                row.errors.append(f"{plat.label} doesn't support {POST_TYPES[t_in].lower()} posts "
                                  f"(it supports {', '.join(POST_TYPES[x].lower() for x in supported)}).")
            row.post_type = t_in
        else:
            guess = ("carousel" if n_media > 1 else "image") if n_media else ("link" if row.link and "link" in supported and not row.text else "text")
            if guess not in supported:
                guess = "image" if n_media and "image" in supported else supported[0]
            row.post_type = guess
        if row.post_type in svc.NEEDS_MEDIA and not n_media:
            msg = f"A {POST_TYPES.get(row.post_type, row.post_type).lower()} post needs media: add an image_url or media_id."
            (row.warnings if row.status == "draft" else row.errors).append(msg)
        if n_media > plat.max_images:
            row.errors.append(f"{plat.label} allows at most {plat.max_images} image(s) per post.")
        if row.post_type in ("video", "animation", "gif", "document") and row.image_urls:
            row.errors.append("Image URLs can only be used for image and carousel posts. Upload videos and PDFs to "
                              "the media library and use their media_id.")
        if len(row.hashtags) > plat.max_hashtags:
            row.warnings.append(f"{plat.label} allows {plat.max_hashtags} hashtag(s); only the first {plat.max_hashtags} are kept.")
            row.hashtags = row.hashtags[: plat.max_hashtags]
        caption = svc.full_caption(row.text, row.hashtags)
        if len(caption) > plat.max_chars:
            row.errors.append(f"Too long for {plat.label}: {len(caption)} characters (the limit is {plat.max_chars}, "
                              "hashtags included).")
        if plat.title_chars:
            if not row.title:
                row.warnings.append(f"{plat.label} needs a title (add a “title” column) before it can be published.")
            elif len(row.title) > plat.title_chars:
                row.errors.append(f"The title is too long for {plat.label} (limit {plat.title_chars} characters).")
        if a.status in ("expired", "error"):
            row.warnings.append(f"{a.name} needs reconnecting in Social accounts before posts can go out.")

    row.outcome = outcome(row, lk.auto_approve)
    return row


def outcome(row: Row, auto_approve: bool) -> str:
    if row.status == "draft":
        return "Saved as a draft"
    if row.status == "review":
        return "Waits for approval"
    return "Scheduled (auto-approval is on)" if auto_approve else "Waits for approval (auto-approval is off)"


def place(post: Post, want: str, auto_approve: bool, now: datetime) -> None:
    """Approval for a hand-written post (call after recheck_post), with the same rules as generated posts:
    draft → draft; review → waits for approval; schedule → waits for approval unless auto-approval is on.
    Posts failing the quality checks always wait for a person."""
    if want == "draft":
        post.status, post.mode, post.approval_deadline = "draft", "manual", None
        return
    post.mode = "approve_first"
    hold = svc.hold_reasons(post)
    svc.route(post, svc.GenSettings(mode="approve_first"), hold_reason="; ".join(hold) or None, now=now,
              auto_approve=auto_approve and want == "schedule")
    if post.status == "scheduled":
        post.approved_at = now


async def check_all(session: AsyncSession, org: Organization | None, data: bytes) -> tuple[list[Row], Lookups]:
    raws = read_csv(data)
    lk = await load_lookups(session, org)
    ids = {svc.as_uuid(m) for r in raws for m in _split_list(r.get("media_id", ""))} - {None}
    if ids:
        lk.media = {str(i) for i in (await session.scalars(select(MediaAsset.id).where(MediaAsset.id.in_(ids)))).all()}
    now = svc.now_utc()
    rows: list[Row] = []
    seen: dict[tuple, int] = {}
    for i, raw in enumerate(raws):
        if not raw:
            continue
        r = check_row(i + 2, raw, lk, now)
        key = (r.account.id if r.account else None, r.scheduled_at, r.text.strip().lower())
        if r.text and key in seen:
            r.warnings.append(f"Same account, time and text as row {seen[key]}.")
        else:
            seen[key] = r.n
        rows.append(r)
    # Clashes with posts already planned for the same account and minute.
    times = [(r.account.id, r.scheduled_at) for r in rows if r.account and r.scheduled_at]
    if times:
        existing = (await session.execute(select(Post.social_account_id, Post.scheduled_at).where(
            Post.social_account_id.in_({a for a, _ in times}), Post.scheduled_at.in_({t for _, t in times}),
            Post.status.notin_(["rejected", "skipped", "failed"])))).all()
        taken = {(a, t) for a, t in existing}
        for r in rows:
            if r.account and r.scheduled_at and (r.account.id, r.scheduled_at) in taken:
                r.warnings.append("Another post is already planned for this account at the same time.")
    return rows, lk


def summary(rows: list[Row], tz) -> dict:
    return {
        "rows": [r.out(tz) for r in rows], "total": len(rows), "ok": sum(1 for r in rows if not r.errors),
        "errors": sum(1 for r in rows if r.errors), "warnings": sum(1 for r in rows if r.warnings and not r.errors),
        "columns": COLUMNS,
    }


# ------------------------------------------------------------------ downloading images
class DownloadError(Exception):
    pass


def http_client() -> httpx.AsyncClient:
    """Tests monkeypatch this with a client on an httpx.MockTransport."""
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False,
                             headers={"User-Agent": "HRP-Digital-Marketing-Studio/1.0", "Accept": "image/*"})


async def download_image(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    """GET a public image (each redirect hop SSRF-checked, size capped). Returns (bytes, filename)."""
    from app.services.crawler import UnsafeURLError, check_public_url

    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            await check_public_url(current)
        except UnsafeURLError as e:
            raise DownloadError(str(e)) from None
        try:
            async with client.stream("GET", current) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = str(httpx.URL(current).join(r.headers["location"]))
                    continue
                if r.status_code >= 400:
                    raise DownloadError(f"the server answered {r.status_code}")
                cl = r.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > MAX_IMAGE_BYTES:
                    raise DownloadError(f"the image is larger than {MAX_IMAGE_BYTES // 1048576} MB")
                chunks, size = [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise DownloadError(f"the image is larger than {MAX_IMAGE_BYTES // 1048576} MB")
                    chunks.append(chunk)
                name = (urlsplit(current).path.rsplit("/", 1)[-1] or "image")[:120]
                return b"".join(chunks), name
        except httpx.TimeoutException:
            raise DownloadError("it took too long to download") from None
        except httpx.HTTPError as e:
            raise DownloadError(f"it couldn't be downloaded ({type(e).__name__})") from None
    raise DownloadError("too many redirects")


async def _store_image(session: AsyncSession, org_id: uuid.UUID, data: bytes, url: str, name: str) -> MediaAsset:
    from app.services import media

    real = media.sniff(data)
    if real not in ("png", "jpg", "webp", "gif"):
        raise DownloadError("it isn't a PNG, JPG, WebP or GIF image")
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name) or "image"
    try:
        asset = await media.save_upload(session, org_id, data, f"{base}.{real}")
    except media.MediaUnavailable as e:
        raise DownloadError(e.message) from None
    asset.source = "import:url"
    asset.meta = {**(asset.meta or {}), "source_url": url[:1000]}
    return asset


# ------------------------------------------------------------------ creating the posts
@dataclass
class CommitResult:
    created: list[Post]
    skipped: list[Row]
    waiting: int
    scheduled: int
    drafts: int


async def commit(session: AsyncSession, org: Organization, user, rows: list[Row], lk: Lookups, *,
                 skip_invalid: bool = False) -> CommitResult:
    """Create posts for the valid rows. Raises ImportError_ when a row has errors (unless skip_invalid) or an image
    can't be downloaded. The caller commits (or rolls back) the transaction."""
    bad = [r for r in rows if r.errors]
    if bad and not skip_invalid:
        raise ImportError_(f"{len(bad)} row(s) have problems. Fix them, or choose to import only the rows without problems.")
    good = [r for r in rows if not r.errors]
    if not good:
        raise ImportError_("There are no rows without problems to import.")
    urls = list(dict.fromkeys(u for r in good for u in r.image_urls))
    if len(urls) > MAX_IMAGES_PER_COMMIT:
        raise ImportError_(f"Import up to {MAX_IMAGES_PER_COMMIT} image URLs at a time. Split the file into smaller parts.")
    assets: dict[str, str] = {}
    if urls:
        failures = []
        async with http_client() as client:
            for u in urls:
                try:
                    data, name = await download_image(client, u)
                    assets[u] = str((await _store_image(session, org.id, data, u, name)).id)
                except DownloadError as e:
                    rows_using = ", ".join(str(r.n) for r in good if u in r.image_urls)
                    failures.append(f"Row {rows_using}: the image {u[:100]} couldn't be used: {e}.")
        if failures:
            raise ImportError_(" ".join(failures[:5]) + (" …" if len(failures) > 5 else ""))

    now = svc.now_utc()
    created: list[Post] = []
    counts = {"waiting": 0, "scheduled": 0, "drafts": 0}
    for r in good:
        a = r.account
        assert a is not None
        p = Post(
            organization_id=org.id, social_account_id=a.id, platform=a.platform, post_type=r.post_type, text=r.text,
            title=r.title, hashtags=r.hashtags, link_url=r.link, first_comment=r.first_comment,
            media_ids=[assets[u] for u in r.image_urls] + r.media_ids, pillar_id=r.pillar_id, scheduled_at=r.scheduled_at,
            status="draft", mode="manual" if r.status == "draft" else "approve_first", created_by=getattr(user, "id", None),
            meta={"source": "import", "import_row": r.n, "keep_media": bool(r.image_urls or r.media_ids)}, qa={},
        )
        session.add(p)
        await session.flush()
        await svc.recheck_post(session, p)
        place(p, r.status, lk.auto_approve, now)
        counts["drafts" if p.status == "draft" else "scheduled" if p.status == "scheduled" else "waiting"] += 1
        svc.add_activity(session, p, "created", f"Imported from a CSV file (row {r.n}) → {svc.STATUSES.get(p.status, p.status)}",
                         user=user)
        created.append(p)
    return CommitResult(created, bad, counts["waiting"], counts["scheduled"], counts["drafts"])


async def notify_imported(org_id: uuid.UUID, waiting: int) -> None:
    """One summary message instead of one per imported post. Call after commit."""
    if not waiting:
        return
    from app.services.notify import notify_org

    try:
        await notify_org(org_id, "post_review", f"{waiting} imported post{'s' if waiting != 1 else ''} waiting for approval",
                         "Posts imported from a CSV file are waiting for approval.", link="/approvals",
                         permission="content.approve")
    except Exception:  # noqa: BLE001
        log.exception("import notification failed")

