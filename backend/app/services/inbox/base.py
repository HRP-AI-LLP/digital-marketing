"""Shared inbox types, errors and platform capabilities."""

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.social.common import PublishError

SYNC_DAYS = 30  # comments are collected for posts published in the last 30 days
MAX_POSTS_PER_ACCOUNT = 25


class InboxPermissionError(PublishError):
    """The connected account lacks a permission (scope) the inbox needs. The user must add it and reconnect."""

    def __init__(self, platform: str, permission: str, what: str = ""):
        self.platform = platform
        self.permission = permission
        msg = f"{what + ': ' if what else ''}needs the {permission} permission. {permission_help(platform, permission)}"
        super().__init__(msg, retryable=False, reconnect=True)


@dataclass
class Incoming:
    """One item as a collector found it, before it is stored."""

    external_id: str
    kind: str = "comment"  # comment | reply | mention
    text: str | None = None
    published_at: datetime | None = None
    author_name: str | None = None
    author_handle: str | None = None
    author_avatar_url: str | None = None
    author_url: str | None = None
    author_external_id: str | None = None
    url: str | None = None
    parent_external_id: str | None = None
    thread_key: str | None = None
    external_post_id: str | None = None
    external_post_url: str | None = None
    post_snippet: str | None = None
    is_own: bool = False
    title: str | None = None
    language: str | None = None
    keyword: str | None = None
    engagement: int | None = None
    platform: str | None = None  # listening: web | news | bluesky | ...
    raw: dict = field(default_factory=dict)


@dataclass
class PostRef:
    """One of our published posts on the account (from the posts table)."""

    external_id: str
    url: str | None = None
    text: str | None = None


@dataclass
class CollectContext:
    since: datetime
    posts: dict[str, PostRef] = field(default_factory=dict)  # by external id
    state: dict = field(default_factory=dict)  # this account's sync state (mutable; saved after the run)
    first_run: bool = False
    notes: list[str] = field(default_factory=list)  # non-fatal problems to show in the UI


# ------------------------------------------------------------------ capabilities
@dataclass(frozen=True)
class Capability:
    label: str
    sync: bool
    reply: bool
    like: bool = False
    max_reply_chars: int = 2000
    read_permissions: tuple[str, ...] = ()
    reply_permissions: tuple[str, ...] = ()
    note: str = ""


CAPABILITIES: dict[str, Capability] = {
    "facebook_page": Capability(
        "Facebook page", True, True, like=True, max_reply_chars=8000,
        read_permissions=("pages_read_engagement", "pages_read_user_content"), reply_permissions=("pages_manage_engagement",),
        note="Comments on your Page posts from the last 30 days."),
    "instagram": Capability(
        "Instagram", True, True, max_reply_chars=2200,
        read_permissions=("instagram_manage_comments",), reply_permissions=("instagram_manage_comments",),
        note="Comments on your posts from the last 30 days and posts that tag or @mention you."),
    "threads": Capability(
        "Threads", True, True, max_reply_chars=500,
        read_permissions=("threads_read_replies",), reply_permissions=("threads_manage_replies",),
        note="Replies to your threads from the last 30 days."),
    "youtube": Capability(
        "YouTube", True, True, max_reply_chars=10000,
        read_permissions=("youtube.force-ssl",), reply_permissions=("youtube.force-ssl",),
        note="Comments on your channel's videos."),
    "bluesky": Capability("Bluesky", True, True, like=True, max_reply_chars=300,
                          note="Replies, mentions and quote posts."),
    "mastodon": Capability("Mastodon", True, True, like=True, max_reply_chars=500,
                           read_permissions=("read:notifications",), reply_permissions=("write:statuses",),
                           note="Replies and mentions."),
    "linkedin_org": Capability(
        "LinkedIn company page", False, False,
        note="LinkedIn only offers comment access to approved Community Management partners. Open each post on "
             "LinkedIn to read and answer comments."),
    "linkedin_person": Capability(
        "LinkedIn profile", False, False,
        note="LinkedIn doesn't offer comment access for personal profiles to apps like this one. Open each post on "
             "LinkedIn to read and answer comments."),
    "pinterest": Capability(
        "Pinterest", False, False,
        note="Pinterest's API has no access to comments. Open each pin on Pinterest to read and answer comments."),
    "gbp": Capability("Google Business Profile", False, False,
                      note="Reviews and Q&A aren't part of the inbox yet. Answer them in Google Business Profile."),
}
SYNC_PLATFORMS = [p for p, c in CAPABILITIES.items() if c.sync]
OPEN_ONLY_PLATFORMS = ["linkedin_org", "linkedin_person", "pinterest"]

LISTENING_SOURCES: dict[str, dict] = {
    "web": {"label": "Web", "interval_minutes": 360, "needs": None},
    "news": {"label": "News", "interval_minutes": 180, "needs": None},
    "bluesky": {"label": "Bluesky search", "interval_minutes": 60, "needs": "bluesky"},
    "mastodon": {"label": "Mastodon hashtags", "interval_minutes": 60, "needs": "mastodon"},
    "youtube": {"label": "YouTube videos", "interval_minutes": 720, "needs": "youtube"},
    "instagram": {"label": "Instagram hashtags", "interval_minutes": 360, "needs": "instagram"},
}
LISTEN_REPLY_PLATFORMS = {"bluesky", "mastodon"}

_META_PAGE_STEPS = ("In your Meta app open Use cases → Manage everything on your Page → Customize and press Add next "
                    "to {perm}. Then reconnect Facebook and Instagram in Settings → Social accounts.")
_META_IG_STEPS = ("In your Meta app open Use cases → Manage messaging and content on Instagram → Customize and press Add "
                  "next to {perm}. Then reconnect Facebook and Instagram (with Instagram ticked) in Settings → Social accounts.")
_THREADS_STEPS = ("In your Meta app open Use cases → Access the Threads API → Customize and press Add next to "
                  "threads_read_replies and threads_manage_replies. Then reconnect Threads in Settings → Social accounts.")
_YT_STEPS = ("In Google Cloud open Google Auth Platform → Data access → Add or remove scopes and add "
             "https://www.googleapis.com/auth/youtube.force-ssl. Then reconnect Google in Settings → Social accounts.")
_MASTODON_STEPS = ("On your Mastodon server open Preferences → Development → your app, tick {perm} (and write:favourites "
                   "to like posts), save, then add the account again with the new access token.")


def permission_help(platform: str, permission: str) -> str:
    if platform == "facebook_page":
        return _META_PAGE_STEPS.format(perm=permission)
    if platform == "instagram":
        return _META_IG_STEPS.format(perm=permission)
    if platform == "threads":
        return _THREADS_STEPS
    if platform == "youtube":
        return _YT_STEPS
    if platform == "mastodon":
        return _MASTODON_STEPS.format(perm=permission)
    return "Add the permission to the app and reconnect the account in Settings → Social accounts."


# ------------------------------------------------------------------ helpers
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<\s*(br|/p)\s*/?>", re.I)


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = _BR.sub("\n", s)
    return html.unescape(_TAG.sub("", s)).strip()


def parse_time(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, UTC)
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if re.match(r".*[+-]\d{4}$", s):  # Graph: 2026-10-01T10:00:00+0000
        s = s[:-2] + ":" + s[-2:]
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ref$|ref_src$|igshid$|si$)", re.I)


def normalize_url(url: str | None) -> str | None:
    """Stable form of a URL for de-duplication: lower-case host, no fragment, no tracking parameters, no trailing slash."""
    if not url:
        return None
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not p.scheme or not p.netloc:
        return url.strip()
    q = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)])
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/") or ""
    return urlunsplit((p.scheme.lower(), host, path, q, ""))


def clip(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
