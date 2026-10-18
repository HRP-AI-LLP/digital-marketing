"""Publish SEO articles to the company website.

Two kinds of website connection live in the integrations catalog (kind ``website``):

- ``wordpress``: the WordPress REST API with an Application Password (Basic auth). Creates or updates a post,
  uploads the featured image, resolves tags, writes Yoast / Rank Math meta when those plugins are found, and
  pushes slug-change redirects to the Redirection plugin when its REST API is available.
- ``website_webhook``: POSTs a signed JSON payload to an address the user gives (custom React/Next sites).
  Signature: ``X-HRP-Signature: sha256=<hex HMAC-SHA256(secret, "<X-HRP-Timestamp>.<raw body>")>``.

Every HTTP call goes through ``app.services.social.net.client`` so tests can mock it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import logging
import re
import secrets as pysecrets
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.crypto import decrypt_json, encrypt_json
from app.models import MediaAsset, Organization, ProviderConfig, SeoArticle, SitePublication, SiteRedirect
from app.services import seo
from app.services.social import net

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]] | None

TARGETS = {"wordpress": "wordpress", "website_webhook": "webhook"}
TARGET_LABEL = {"wordpress": "WordPress", "webhook": "your website"}

YOAST_KEYS = {"title": "_yoast_wpseo_title", "description": "_yoast_wpseo_metadesc",
              "canonical": "_yoast_wpseo_canonical", "focus": "_yoast_wpseo_focuskw"}
RANKMATH_KEYS = {"title": "rank_math_title", "description": "rank_math_description",
                 "canonical": "rank_math_canonical_url", "focus": "rank_math_focus_keyword"}

META_SNIPPET = """<?php
/**
 * Plugin Name: HRP Studio SEO fields
 * Description: Lets HRP Digital Marketing Studio write SEO titles and descriptions through the REST API.
 */
add_action('init', function () {
    $keys = ['_yoast_wpseo_title', '_yoast_wpseo_metadesc', '_yoast_wpseo_canonical', '_yoast_wpseo_focuskw',
             'rank_math_title', 'rank_math_description', 'rank_math_canonical_url', 'rank_math_focus_keyword'];
    foreach ($keys as $key) {
        register_post_meta('post', $key, [
            'show_in_rest' => true, 'single' => true, 'type' => 'string',
            'auth_callback' => function () { return current_user_can('edit_posts'); },
        ]);
    }
});
"""

META_HELP = (
    "WordPress didn't save the SEO title and description because these fields aren't open to the REST API yet. "
    "Fix it once: in WordPress, go to Plugins → Add New, install “Code Snippets”, add a new snippet with the code "
    "below and activate it (or save the code as wp-content/mu-plugins/hrp-seo-meta.php). Then press "
    "“Update on website”."
)
RANKMATH_HELP = " With Rank Math you can instead turn on Rank Math → General Settings → Others → Headless CMS Support."

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_PAGE_BYTES = 2_000_000


class SiteError(Exception):
    """A problem we can explain to a marketer in plain words."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


# ============================================================ connection helpers
def target_of(cfg: ProviderConfig) -> str | None:
    return TARGETS.get(cfg.provider)


def fields(cfg: ProviderConfig) -> dict:
    return decrypt_json(cfg.secrets_enc)


def normalize_site(url: str | None) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    return u.rstrip("/")


def site_of(cfg: ProviderConfig) -> str:
    f = fields(cfg)
    if cfg.provider == "wordpress":
        return normalize_site(f.get("site_url") or cfg.base_url)
    pattern = (f.get("article_url") or "").strip()
    if pattern:
        p = urlsplit(normalize_site(pattern))
        return f"{p.scheme}://{p.netloc}"
    return ""


def connection_out(cfg: ProviderConfig) -> dict:
    f = fields(cfg)
    return {
        "id": cfg.id, "name": cfg.name, "provider": cfg.provider, "target": target_of(cfg), "site": site_of(cfg),
        "enabled": cfg.enabled, "last_test_ok": cfg.last_test_ok, "last_test_message": cfg.last_test_message,
        "indexnow_key": f.get("indexnow_key") or None,
        "plugins": (cfg.settings or {}).get("plugins") or {},
    }


async def connections(session: AsyncSession) -> list[ProviderConfig]:
    rows = (await session.scalars(select(ProviderConfig).where(ProviderConfig.kind == "website")
                                  .order_by(ProviderConfig.created_at))).all()
    return [c for c in rows if target_of(c)]


def _clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(text or "")).strip()


# ============================================================ WordPress REST client
class WordPress:
    def __init__(self, site: str, username: str, app_password: str):
        if not site:
            raise SiteError("Add your WordPress site address in Settings → Integrations.")
        if not username or not app_password:
            raise SiteError("Add the WordPress username and application password in Settings → Integrations.")
        self.site = normalize_site(site)
        token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
        self.plain = False  # sites without pretty permalinks use ?rest_route=

    @classmethod
    def from_cfg(cls, cfg: ProviderConfig) -> WordPress:
        f = fields(cfg)
        wp = cls(f.get("site_url") or cfg.base_url or "", (f.get("username") or "").strip(),
                 (f.get("app_password") or "").strip())
        wp.plain = bool((cfg.settings or {}).get("rest_plain"))
        return wp

    def url(self, path: str, params: dict | None = None) -> str:
        if self.plain:
            q = {"rest_route": path, **(params or {})}
            return f"{self.site}/?{urlencode(q, doseq=True)}"
        return f"{self.site}/wp-json{path}" + (f"?{urlencode(params, doseq=True)}" if params else "")

    async def request(self, method: str, path: str, *, params: dict | None = None, json_body=None,
                      content: bytes | None = None, headers: dict | None = None, timeout: float = 60.0,
                      what: str = "do this") -> httpx.Response:
        h = {**self.headers, **(headers or {})}
        try:
            async with net.client(timeout) as c:
                r = await c.request(method, self.url(path, params), json=json_body, content=content, headers=h)
        except httpx.TimeoutException:
            raise SiteError(f"{self.site} took too long to answer. Try again in a minute.") from None
        except httpx.HTTPError as e:
            raise SiteError(f"Couldn't reach {self.site} ({type(e).__name__}). Check the site address.") from None
        if r.status_code in (301, 302, 307, 308):
            loc = r.headers.get("location", "")
            raise SiteError(f"WordPress redirected the request to {loc or 'another address'}. Use the exact site "
                            "address (with or without www, https) in Settings → Integrations.")
        if r.status_code >= 400:
            raise self.error(r, what)
        return r

    def error(self, r: httpx.Response, what: str) -> SiteError:
        try:
            body = r.json()
        except ValueError:
            body = {}
        code = body.get("code", "") if isinstance(body, dict) else ""
        msg = _clean(body.get("message", "") if isinstance(body, dict) else "")
        if r.status_code == 401 or code in ("incorrect_password", "invalid_username", "rest_not_logged_in",
                                            "invalid_email"):
            return SiteError(
                "WordPress didn't accept the username or application password. Create a new application password "
                "under Users → Profile → Application passwords and paste it in Settings → Integrations. If it's "
                "correct, your host may strip the login header: add the line "
                "SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1 to the site's .htaccess file.")
        if r.status_code == 403:
            return SiteError(f"This WordPress user isn't allowed to {what}. Use an Editor or Administrator account."
                             + (f" (WordPress said: {msg})" if msg else ""))
        if r.status_code == 404 and code in ("rest_no_route", ""):
            return SiteError(f"The WordPress REST API wasn't found at {self.site}. Check the site address and that no "
                             "security plugin blocks /wp-json.")
        return SiteError(f"WordPress couldn't {what}: {msg or f'error {r.status_code}'}".rstrip(".") + ".", code or None)

    async def discover(self) -> dict:
        """REST index: site name and which helper plugins are present."""
        try:
            r = await self.request("GET", "/", what="show its REST API")
        except SiteError:
            if self.plain:
                raise
            self.plain = True  # try ?rest_route=/ (plain permalinks)
            try:
                r = await self.request("GET", "/", what="show its REST API")
            except SiteError:
                self.plain = False
                raise
        try:
            data = r.json()
        except ValueError:
            raise SiteError(f"{self.site} didn't answer like a WordPress site. Check the address.") from None
        ns = set(data.get("namespaces") or []) if isinstance(data, dict) else set()
        if "wp/v2" not in ns:
            raise SiteError(f"{self.site} didn't answer like a WordPress site (no wp/v2 API). Check the address.")
        return {
            "name": _clean(data.get("name", "")), "plain": self.plain,
            "plugins": {"yoast": "yoast/v1" in ns, "rankmath": "rankmath/v1" in ns, "redirection": "redirection/v1" in ns},
        }

    async def me(self) -> dict:
        r = await self.request("GET", "/wp/v2/users/me", params={"context": "edit"}, what="sign in")
        return r.json()

    async def list_terms(self, kind: str) -> list[dict]:
        r = await self.request("GET", f"/wp/v2/{kind}", params={"per_page": 100, "orderby": "count", "order": "desc",
                                                                   "_fields": "id,name,slug,count"},
                               what=f"list {kind}")
        return [{"id": t.get("id"), "name": _clean(t.get("name", "")), "slug": t.get("slug")} for t in (r.json() or [])]

    async def list_authors(self) -> list[dict]:
        r = await self.request("GET", "/wp/v2/users", params={"per_page": 100, "who": "authors", "_fields": "id,name,slug"},
                               what="list authors")
        return [{"id": u.get("id"), "name": _clean(u.get("name", "")), "slug": u.get("slug")} for u in (r.json() or [])]

    async def tag_ids(self, names: list[str]) -> tuple[list[int], list[str]]:
        ids, problems = [], []
        for name in [n.strip() for n in names if n and n.strip()][:20]:
            try:
                r = await self.request("GET", "/wp/v2/tags", params={"search": name, "per_page": 20, "_fields": "id,name"},
                                       what="look up tags")
                match = next((t for t in r.json() or [] if _clean(t.get("name", "")).lower() == name.lower()), None)
                if match is None:
                    r = await self.request("POST", "/wp/v2/tags", json_body={"name": name}, what="create tags")
                    match = r.json()
                ids.append(int(match["id"]))
            except (SiteError, KeyError, TypeError, ValueError) as e:
                problems.append(f"{name}: {e}")
        return ids, problems

    async def upload_media(self, data: bytes, filename: str, mime: str, alt: str, title: str) -> dict:
        r = await self.request("POST", "/wp/v2/media", content=data, timeout=net.UPLOAD_TIMEOUT, what="upload images",
                               headers={"Content-Type": mime or "application/octet-stream",
                                        "Content-Disposition": f'attachment; filename="{filename}"'})
        media = r.json()
        try:
            await self.request("POST", f"/wp/v2/media/{media['id']}", json_body={"alt_text": alt, "title": title},
                               what="set the image's alt text")
        except SiteError:
            log.info("alt text update failed for media %s", media.get("id"))
        return media

    async def save_post(self, post_id: str | None, body: dict) -> tuple[dict, bool]:
        """Create or update; returns (post, created). A post deleted in WordPress is created again."""
        if post_id:
            try:
                r = await self.request("POST", f"/wp/v2/posts/{post_id}", json_body=body, what="update the post")
                return r.json(), False
            except SiteError as e:
                if e.code != "rest_post_invalid_id":
                    raise
        r = await self.request("POST", "/wp/v2/posts", json_body=body, what="create posts")
        return r.json(), True

    async def rankmath_meta(self, post_id: int, meta: dict) -> bool:
        try:
            await self.request("POST", "/rankmath/v1/updateMeta",
                               json_body={"objectType": "post", "objectID": post_id, "meta": meta}, what="save Rank Math fields")
            return True
        except SiteError:
            return False

    async def redirect_group(self) -> int | None:
        r = await self.request("GET", "/redirection/v1/group", params={"per_page": 50}, what="read redirect groups")
        items = (r.json() or {}).get("items") or []
        wp_groups = [g for g in items if str(g.get("module_id", "1")) == "1"] or items
        return int(wp_groups[0]["id"]) if wp_groups else None

    async def add_redirect(self, group_id: int, source: str, target: str) -> str | None:
        r = await self.request("POST", "/redirection/v1/redirect", what="add redirects", json_body={
            "url": source, "action_data": {"url": target}, "action_type": "url", "action_code": 301,
            "match_type": "url", "group_id": group_id, "regex": False,
        })
        try:
            data = r.json()
            items = data.get("items") if isinstance(data, dict) else None
            for it in items or []:
                if it.get("url") == source:
                    return str(it.get("id"))
        except ValueError:
            pass
        return None


# ============================================================ article → website content
def content_hash(article: SeoArticle) -> str:
    meta = seo.article_meta(article)
    parts = {
        "title": article.title, "body": article.body_md or "", "meta_title": article.meta_title or "",
        "meta_description": article.meta_description or "", "slug": article.slug, "faq": article.faq or [],
        "keyword": article.keyword, "canonical": meta.get("canonical_url") or "",
        "cover": str(article.cover_media_id or ""),
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def body_without_h1(md: str) -> str:
    """WordPress and most themes print the title themselves: drop a leading '# Title' line."""
    lines = (md or "").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if re.match(r"^#\s+", line.strip()):
            return "\n".join(lines[i + 1:]).lstrip("\n")
        break
    return md or ""


def faq_schema(schema: dict) -> dict | None:
    for node in schema.get("@graph", []):
        if node.get("@type") == "FAQPage" and node.get("mainEntity"):
            return {"@context": "https://schema.org", **node}
    return None


def schema_script(schema: dict | None) -> str:
    if not schema:
        return ""
    return ('\n<script type="application/ld+json">' + json.dumps(schema, ensure_ascii=False).replace("</", "<\\/")
            + "</script>")


def explicit_canonical(article: SeoArticle) -> str | None:
    return seo.article_meta(article).get("canonical_url") or None


async def cover_asset(session: AsyncSession, article: SeoArticle) -> MediaAsset | None:
    if not article.cover_media_id:
        return None
    return await session.scalar(select(MediaAsset).where(MediaAsset.id == article.cover_media_id))


def _cover_bytes(asset: MediaAsset) -> tuple[bytes, str, str] | None:
    try:
        p = storage.abs_path(asset.path)
    except ValueError:
        return None
    if not p.exists():
        return None
    return p.read_bytes(), asset.mime or "image/png", p.name


def _warn(warnings: list[dict], code: str, message: str, help_text: str | None = None, snippet: str | None = None):
    w = {"code": code, "message": message}
    if help_text:
        w["help"] = help_text
    if snippet:
        w["snippet"] = snippet
    warnings.append(w)


def wp_date_gmt(when: datetime) -> str:
    return when.astimezone(UTC).replace(tzinfo=None, microsecond=0).isoformat()


def parse_wp_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d


WP_STATUS = {"publish": "published", "future": "scheduled", "draft": "draft", "pending": "draft", "private": "published"}


# ============================================================ publishing (called from the job)
async def publish(session: AsyncSession, article: SeoArticle, cfg: ProviderConfig, pub: SitePublication,
                  options: dict, progress: Progress = None) -> dict:
    target = target_of(cfg)
    if not article.body_md:
        raise SiteError("Write the article before publishing it.")
    if not cfg.enabled:
        raise SiteError("This website connection is turned off in Settings → Integrations.")
    if target == "wordpress":
        return await _publish_wordpress(session, article, cfg, pub, options, progress)
    if target == "webhook":
        return await _publish_webhook(session, article, cfg, pub, options, progress)
    raise SiteError("Unknown website connection.")


async def _say(progress: Progress, msg: str) -> None:
    if progress:
        await progress(msg)


def _remember_live(article: SeoArticle, url: str | None) -> None:
    if url and seo.article_meta(article).get("live_url") != url:
        seo.set_article_meta(article, live_url=url)


async def _publish_wordpress(session, article, cfg, pub, options, progress) -> dict:
    org = await session.get(Organization, article.organization_id)
    wp = WordPress.from_cfg(cfg)
    warnings: list[dict] = []
    await _say(progress, "Connecting to WordPress")
    info = await wp.discover()
    plugins = info["plugins"]
    cfg.settings = {**(cfg.settings or {}), "plugins": plugins, "rest_plain": info["plain"]}

    # featured image
    featured_id, featured_url = (int(pub.media_remote_id) if pub.media_remote_id else None), (pub.data or {}).get("image_url")
    asset = await cover_asset(session, article)
    if asset and (str(pub.media_source_id or "") != str(asset.id) or not featured_id):
        await _say(progress, "Uploading the cover image")
        blob = _cover_bytes(asset)
        if blob is None:
            _warn(warnings, "image_missing", "The cover image file is missing, so no featured image was set.")
        else:
            data, mime, name = blob
            ext = name.rsplit(".", 1)[-1] if "." in name else "png"
            try:
                media = await wp.upload_media(data, f"{article.slug}.{ext}", mime, asset.alt_text or article.title,
                                              article.title)
                featured_id, featured_url = int(media["id"]), media.get("source_url")
                pub.media_remote_id, pub.media_source_id = str(featured_id), asset.id
            except (SiteError, KeyError, ValueError) as e:
                _warn(warnings, "image_failed", f"The cover image wasn't uploaded: {e}")
    elif not asset:
        featured_id = None

    tags, problems = [], []
    if options.get("tags"):
        await _say(progress, "Matching tags")
        tags, problems = await wp.tag_ids(options["tags"])
        if problems:
            _warn(warnings, "tags_failed", "Some tags couldn't be added: " + "; ".join(problems)[:400])

    seo_plugin = "yoast" if plugins["yoast"] else "rankmath" if plugins["rankmath"] else None
    schema = seo.build_schema(article, org, image=featured_url)
    # Yoast and Rank Math print their own Article schema; add only what they don't (the FAQ).
    embed = faq_schema(schema) if seo_plugin else schema
    canonical = explicit_canonical(article)
    meta: dict = {}
    keys = YOAST_KEYS if seo_plugin == "yoast" else RANKMATH_KEYS if seo_plugin == "rankmath" else {}
    if keys:
        meta = {keys["title"]: article.meta_title or article.title, keys["description"]: article.meta_description or "",
                keys["focus"]: article.keyword}
        if canonical:
            meta[keys["canonical"]] = canonical

    status = options.get("status") or "draft"
    body: dict = {
        "title": article.title, "slug": article.slug, "status": status,
        "excerpt": article.meta_description or "",
        "content": seo.md_to_html(body_without_h1(article.body_md)) + schema_script(embed),
    }
    if status == "future":
        when = options.get("publish_at")
        when = datetime.fromisoformat(when) if isinstance(when, str) else when
        if when is None:
            raise SiteError("Choose when the article should go live.")
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        body["date_gmt"] = wp_date_gmt(when)
    if options.get("categories"):
        body["categories"] = [int(c) for c in options["categories"]]
    if tags:
        body["tags"] = tags
    if options.get("author"):
        body["author"] = int(options["author"])
    if featured_id:
        body["featured_media"] = featured_id
    if meta:
        body["meta"] = meta

    await _say(progress, "Updating the post" if pub.remote_id else "Creating the post")
    post, created = await wp.save_post(pub.remote_id, body)
    post_id = post.get("id")
    link = post.get("link")

    if embed is schema and link and link != seo.canonical_url(article, org) and status != "draft":
        # the schema's URLs were guessed before WordPress gave the permalink; fix them
        _remember_live(article, link)
        schema = seo.build_schema(article, org, image=featured_url)
        try:
            post, _ = await wp.save_post(str(post_id), {"content": seo.md_to_html(body_without_h1(article.body_md))
                                                        + schema_script(schema)})
        except SiteError as e:
            _warn(warnings, "schema_fix_failed", f"The schema has a provisional address: {e}")

    # SEO plugin fields
    if seo_plugin:
        saved = post.get("meta") if isinstance(post.get("meta"), dict) else {}
        missing = [k for k in meta if k not in saved]
        if seo_plugin == "rankmath" and missing and await wp.rankmath_meta(int(post_id), meta):
            missing = []
        if missing:
            _warn(warnings, "seo_meta_not_registered",
                  f"{'Yoast SEO' if seo_plugin == 'yoast' else 'Rank Math'} title and description weren't saved.",
                  META_HELP + (RANKMATH_HELP if seo_plugin == "rankmath" else ""), META_SNIPPET)
    else:
        _warn(warnings, "no_seo_plugin",
              "No Yoast SEO or Rank Math plugin was found, so the meta description was saved as the post excerpt "
              "and the schema was added to the article. Optional: install the free Yoast SEO or Rank Math plugin "
              "for full control of the search title and description.")

    remote_slug = post.get("slug") or article.slug
    if status != "draft" and remote_slug and remote_slug != article.slug:
        _warn(warnings, "slug_changed", f"WordPress used the address “{remote_slug}” because “{article.slug}” "
                                        "was already taken by another post or page.")

    pub.remote_id, pub.remote_url = str(post_id), link
    pub.status = WP_STATUS.get(post.get("status") or status, "draft")
    pub.published_slug = remote_slug
    pub.scheduled_at = parse_wp_date(post.get("date_gmt")) if pub.status == "scheduled" else None
    if pub.status == "published" and not pub.published_at:
        pub.published_at = datetime.now(UTC)
    pub.content_hash = content_hash(article)
    pub.error = None
    pub.data = {**(pub.data or {}), "warnings": warnings, "seo_plugin": seo_plugin, "image_url": featured_url,
                "options": _public_options(options), "last_action": "created" if created else "updated",
                "remote_status": post.get("status")}
    if pub.status != "draft":
        _remember_live(article, link)

    pushed = await push_redirects(session, cfg, article_id=article.id, wp=wp, plugins=plugins)
    return {"remote_id": pub.remote_id, "url": link, "status": pub.status, "created": created,
            "warnings": len(warnings), "redirects": pushed}


def _public_options(options: dict) -> dict:
    keep = ("status", "publish_at", "categories", "tags", "author", "indexnow", "check")
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in options.items() if k in keep}


async def unpublish(session: AsyncSession, article: SeoArticle, cfg: ProviderConfig, pub: SitePublication,
                    progress: Progress = None) -> dict:
    if not pub.remote_id and target_of(cfg) == "wordpress":
        raise SiteError("This article isn't on WordPress yet.")
    if target_of(cfg) == "wordpress":
        wp = WordPress.from_cfg(cfg)
        await _say(progress, "Moving the post back to draft")
        try:
            r = await wp.request("POST", f"/wp/v2/posts/{pub.remote_id}", json_body={"status": "draft"},
                                 what="change the post")
        except SiteError as e:
            if e.code != "rest_post_invalid_id":
                raise
            pub.status, pub.remote_id, pub.remote_url, pub.scheduled_at, pub.error = "none", None, None, None, None
            if seo.article_meta(article).get("live_url"):
                seo.set_article_meta(article, live_url=None)
            return {"status": "none", "message": "The post was already deleted in WordPress."}
        post = r.json()
        pub.status, pub.scheduled_at = "draft", None
        pub.remote_url = post.get("link") or pub.remote_url
    else:
        await _say(progress, "Telling your website")
        await _send_webhook(cfg, "article.unpublished", {"article": {"id": str(article.id), "slug": article.slug,
                                                                      "remote_id": pub.remote_id}})
        pub.status, pub.scheduled_at = "unpublished", None
    pub.error = None
    meta = seo.article_meta(article)
    if meta.get("live_url"):
        seo.set_article_meta(article, live_url=None)
    return {"status": pub.status}


# ============================================================ webhook
def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """For receivers (and tests): constant-time check of X-HRP-Signature."""
    return hmac.compare_digest(sign(secret, timestamp, body), signature or "")


def webhook_article_url(cfg: ProviderConfig, article: SeoArticle, org: Organization | None) -> str:
    pattern = (fields(cfg).get("article_url") or "").strip()
    if pattern:
        return normalize_site(pattern.replace("{slug}", article.slug)) if "{slug}" in pattern \
            else normalize_site(pattern) + "/" + article.slug
    return seo.canonical_url(article, org)


async def _send_webhook(cfg: ProviderConfig, event: str, payload: dict) -> dict:
    f = fields(cfg)
    url = (f.get("webhook_url") or cfg.base_url or "").strip()
    secret = f.get("signing_secret") or ""
    if not url.startswith(("http://", "https://")):
        raise SiteError("Add the webhook address (starting with https://) in Settings → Integrations.")
    if not secret:
        raise SiteError("Add a signing secret for the webhook in Settings → Integrations.")
    body = json.dumps({"event": event, "sent_at": datetime.now(UTC).isoformat(), **payload},
                      ensure_ascii=False, default=str).encode()
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-HRP-Event": event, "X-HRP-Timestamp": ts,
               "X-HRP-Signature": sign(secret, ts, body), "X-HRP-Delivery": str(uuid.uuid4())}
    host = urlsplit(url).netloc
    try:
        async with net.client(60.0) as c:
            r = await c.post(url, content=body, headers=headers)
    except httpx.TimeoutException:
        raise SiteError(f"Your website ({host}) took too long to answer the webhook.") from None
    except httpx.HTTPError as e:
        raise SiteError(f"Couldn't reach the webhook at {host} ({type(e).__name__}). Check the address.") from None
    if r.status_code in (401, 403):
        raise SiteError(f"Your website ({host}) rejected the webhook ({r.status_code}). Check that the signing secret "
                        "is the same on both sides.")
    if r.status_code >= 300:
        detail = _clean(r.text)[:200]
        raise SiteError(f"Your website ({host}) answered with error {r.status_code}" + (f": {detail}" if detail else "."))
    try:
        data = r.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


async def webhook_payload(session: AsyncSession, article: SeoArticle, cfg: ProviderConfig, options: dict,
                          warnings: list[dict]) -> dict:
    org = await session.get(Organization, article.organization_id)
    url = webhook_article_url(cfg, article, org)
    image = None
    asset = await cover_asset(session, article)
    if asset:
        try:
            from app.services.media import ensure_public_url

            image = {"url": await ensure_public_url(session, asset), "alt": asset.alt_text or article.title,
                     "mime": asset.mime, "width": asset.width, "height": asset.height}
        except Exception as e:  # noqa: BLE001 - no media host, file missing…
            _warn(warnings, "image_no_public_url",
                  f"The cover image has no public link, so it wasn't sent ({e}). Set up Cloudinary or your own public "
                  "URL under Settings → Integrations → Search, hosting.")
    prev = await session.scalar(select(SitePublication).where(SitePublication.article_id == article.id,
                                                              SitePublication.provider_id == cfg.id))
    canon = explicit_canonical(article) or url
    # schema with the real address
    old_live = seo.article_meta(article).get("live_url")
    seo.set_article_meta(article, live_url=url)
    schema = seo.build_schema(article, org, image=image["url"] if image else None)
    if old_live != url:
        seo.set_article_meta(article, live_url=old_live)
    redirects = (await session.scalars(select(SiteRedirect).where(
        SiteRedirect.article_id == article.id, SiteRedirect.status.in_(["pending", "failed", "manual", "sent"]),
    ))).all()
    status = options.get("status") or "publish"
    publish_at = options.get("publish_at")
    head = {"title": article.meta_title or article.title, "description": article.meta_description or "",
            "canonical": canon, "og:title": article.meta_title or article.title,
            "og:description": article.meta_description or "", "og:url": url, "og:type": "article",
            "og:image": image["url"] if image else None, "keywords": [article.keyword, *(article.secondary_keywords or [])]}
    return {
        "site": {"name": org.name if org else "", "website": seo.website_base(org)},
        "article": {
            "id": str(article.id), "remote_id": prev.remote_id if prev else None, "title": article.title,
            "slug": article.slug, "previous_slug": prev.published_slug if prev and prev.published_slug != article.slug else None,
            "status": status, "publish_at": publish_at.isoformat() if isinstance(publish_at, datetime) else publish_at,
            "url": url, "excerpt": article.meta_description or "",
            "html": seo.md_to_html(body_without_h1(article.body_md or "")), "markdown": article.body_md or "",
            "meta": head, "head_html": head_html(article, org, canon, image["url"] if image else None, schema),
            "schema": schema, "faq": article.faq or [], "image": image,
            "keyword": article.keyword, "secondary_keywords": article.secondary_keywords or [],
            "tags": options.get("tags") or [], "word_count": article.word_count,
            "updated_at": article.updated_at.isoformat() if article.updated_at else None,
        },
        "redirects": [{"from": r.from_path, "to": r.to_path, "code": 301} for r in redirects],
    }


async def _publish_webhook(session, article, cfg, pub, options, progress) -> dict:
    warnings: list[dict] = []
    await _say(progress, "Preparing the article")
    payload = await webhook_payload(session, article, cfg, options, warnings)
    event = "article.updated" if pub.remote_id or pub.status in ("published", "scheduled", "draft") else "article.published"
    await _say(progress, "Sending it to your website")
    resp = await _send_webhook(cfg, event, payload)
    url = str(resp.get("url") or payload["article"]["url"])
    status = options.get("status") or "publish"
    pub.remote_id = str(resp.get("id")) if resp.get("id") is not None else pub.remote_id
    pub.remote_url = url
    pub.status = {"publish": "published", "future": "scheduled", "draft": "draft"}.get(status, "published")
    pa = options.get("publish_at")
    pub.scheduled_at = (datetime.fromisoformat(pa) if isinstance(pa, str) else pa) if pub.status == "scheduled" else None
    if pub.status == "published" and not pub.published_at:
        pub.published_at = datetime.now(UTC)
    pub.published_slug = article.slug
    pub.content_hash = content_hash(article)
    pub.error = None
    pub.data = {**(pub.data or {}), "warnings": warnings, "options": _public_options(options), "last_action": event}
    if pub.status != "draft":
        _remember_live(article, url)
    sent = 0
    for r in (await session.scalars(select(SiteRedirect).where(
            SiteRedirect.article_id == article.id, SiteRedirect.status.in_(["pending", "failed"])))).all():
        r.status, r.pushed_at, r.provider_id, r.error = "sent", datetime.now(UTC), cfg.id, None
        sent += 1
    return {"url": url, "status": pub.status, "warnings": len(warnings), "redirects": sent}


async def test_webhook(cfg: ProviderConfig) -> str:
    data = await _send_webhook(cfg, "ping", {"message": "Test from HRP Digital Marketing Studio"})
    return "Your website accepted a signed test message." + (" It answered: " + json.dumps(data)[:120] if data else "")


async def test_wordpress(cfg: ProviderConfig) -> str:
    wp = WordPress.from_cfg(cfg)
    info = await wp.discover()
    me = await wp.me()
    cfg.settings = {**(cfg.settings or {}), "plugins": info["plugins"], "rest_plain": info["plain"]}
    caps = me.get("capabilities") or {}
    found = [n for k, n in (("yoast", "Yoast SEO"), ("rankmath", "Rank Math"), ("redirection", "Redirection"))
             if info["plugins"][k]]
    msg = f"Connected to {info['name'] or wp.site} as {_clean(me.get('name', '')) or 'your user'}."
    if caps and not (caps.get("publish_posts") or caps.get("edit_posts")):
        msg += " This user can't publish posts; use an Editor or Administrator."
    msg += (" Found: " + ", ".join(found) + ".") if found else " No SEO or redirect plugin found."
    return msg


async def wp_options(cfg: ProviderConfig) -> dict:
    wp = WordPress.from_cfg(cfg)
    info = await wp.discover()
    cfg.settings = {**(cfg.settings or {}), "plugins": info["plugins"], "rest_plain": info["plain"]}
    out = {"site": wp.site, "name": info["name"], "plugins": info["plugins"], "categories": [], "tags": [],
           "authors": [], "errors": []}
    for key, fn in (("categories", lambda: wp.list_terms("categories")), ("tags", lambda: wp.list_terms("tags")),
                    ("authors", wp.list_authors)):
        try:
            out[key] = await fn()
        except SiteError as e:
            out["errors"].append(str(e))
    return out


# ============================================================ redirects
def _path(url: str | None) -> str:
    if not url:
        return ""
    p = urlsplit(url)
    return p.path or "/"


def _swap_slug(path: str, old: str, new: str) -> str | None:
    parts = path.split("/")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == old:
            parts[i] = new
            return "/".join(parts)
    return None


async def record_slug_change(session: AsyncSession, article: SeoArticle, old_slug: str, new_slug: str) -> list[SiteRedirect]:
    """Called when a slug changes: redirect the live address of every published copy."""
    if not old_slug or old_slug == new_slug:
        return []
    pubs = (await session.scalars(select(SitePublication).where(
        SitePublication.article_id == article.id, SitePublication.status.in_(["published", "scheduled"])))).all()
    made: list[SiteRedirect] = []
    for pub in pubs:
        old_path = _path(pub.remote_url)
        if not old_path or "?" in (pub.remote_url or "") and old_slug not in old_path:
            continue
        new_path = _swap_slug(old_path, pub.published_slug or old_slug, new_slug) or _swap_slug(old_path, old_slug, new_slug)
        if not new_path or new_path == old_path:
            continue
        existing = (await session.scalars(select(SiteRedirect).where(SiteRedirect.provider_id == pub.provider_id))).all()
        for r in existing:
            if r.from_path == new_path:  # going back to an earlier address: that redirect would loop
                await session.delete(r)
            elif r.to_path == old_path:  # A→B and now B→C: point A straight at C
                r.to_path = new_path
                if r.status in ("pushed", "sent", "done"):
                    r.status = "pending"
        if any(r.from_path == old_path for r in existing):
            row = next(r for r in existing if r.from_path == old_path)
            row.to_path, row.status = new_path, "pending"
        else:
            row = SiteRedirect(organization_id=article.organization_id, article_id=article.id,
                               provider_id=pub.provider_id, from_path=old_path, to_path=new_path, status="pending")
            session.add(row)
        made.append(row)
        if pub.remote_url:
            p = urlsplit(pub.remote_url)
            new_url = urlunsplit(p._replace(path=new_path))
            if seo.article_meta(article).get("live_url") == pub.remote_url:
                seo.set_article_meta(article, live_url=new_url)
    await session.flush()
    return made


async def push_redirects(session: AsyncSession, cfg: ProviderConfig, *, article_id: uuid.UUID | None = None,
                         wp: WordPress | None = None, plugins: dict | None = None,
                         include_manual: bool = False) -> dict:
    """Send waiting redirects to the WordPress Redirection plugin; without it they're listed for the user."""
    states = ["pending", "failed"] + (["manual"] if include_manual else [])
    q = select(SiteRedirect).where(SiteRedirect.provider_id == cfg.id, SiteRedirect.status.in_(states))
    if article_id:
        q = q.where(SiteRedirect.article_id == article_id)
    rows = (await session.scalars(q)).all()
    if not rows:
        return {"pushed": 0, "manual": 0, "failed": 0}
    wp = wp or WordPress.from_cfg(cfg)
    if plugins is None:
        plugins = (await wp.discover())["plugins"]
    out = {"pushed": 0, "manual": 0, "failed": 0}
    if not plugins.get("redirection"):
        for r in rows:
            r.status = "manual"
            r.error = None
            out["manual"] += 1
        return out
    try:
        group = await wp.redirect_group()
    except SiteError as e:
        group, err = None, str(e)
    else:
        err = "The Redirection plugin has no redirect group yet. Open Tools → Redirection once to finish its setup."
    for r in rows:
        if group is None:
            r.status, r.error = "failed", err[:500]
            out["failed"] += 1
            continue
        try:
            r.remote_id = await wp.add_redirect(group, r.from_path, r.to_path)
            r.status, r.pushed_at, r.error = "pushed", datetime.now(UTC), None
            out["pushed"] += 1
        except SiteError as e:
            r.status, r.error = "failed", str(e)[:500]
            out["failed"] += 1
    return out


REDIRECT_FORMATS = {
    "htaccess": (".htaccess", "text/plain"),
    "nginx": ("redirects.nginx.conf", "text/plain"),
    "netlify": ("_redirects", "text/plain"),
    "vercel": ("vercel.json", "application/json"),
    "csv": ("redirects.csv", "text/csv"),
}


def export_redirects(rows: list[SiteRedirect], fmt: str) -> tuple[str, str, str]:
    name, media = REDIRECT_FORMATS[fmt]
    pairs = [(r.from_path, r.to_path) for r in rows if r.from_path and r.to_path and r.from_path != r.to_path]

    def q(p: str) -> str:  # paths with spaces must be quoted in Apache / nginx
        return f'"{p}"' if re.search(r"\s", p) else p

    if fmt == "htaccess":
        text = "# Add to your site's .htaccess (Apache)\n" + "".join(f"Redirect 301 {q(a)} {q(b)}\n" for a, b in pairs)
    elif fmt == "nginx":
        text = "# Add inside your nginx server { } block\n" + "".join(
            f"location = {q(a)} {{ return 301 {q(b)}; }}\n" for a, b in pairs)
    elif fmt == "netlify":
        text = "".join(f"{a}  {b}  301\n" for a, b in pairs)
    elif fmt == "vercel":
        text = json.dumps({"redirects": [{"source": a, "destination": b, "permanent": True} for a, b in pairs]}, indent=2) + "\n"
    else:
        text = "source,target,code\n" + "".join(f"{a},{b},301\n" for a, b in pairs)
    return name, media, text


# ============================================================ live page check
class _HeadParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.canonical, self.description, self.robots = None, None, None, None
        self.schema = 0
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title" and self.title is None:
            self._in_title, self._buf = True, []
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = a.get("href")
        elif tag == "meta":
            name = (a.get("name") or "").lower()
            if name == "description" and self.description is None:
                self.description = a.get("content")
            elif name == "robots":
                self.robots = a.get("content")
        elif tag == "script" and "ld+json" in a.get("type", "").lower():
            self.schema += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._buf).split())

    def handle_data(self, data):
        if self._in_title:
            self._buf.append(data)


def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    p = urlsplit(u.strip())
    host = (p.hostname or "").lower()
    return f"{host}{p.path.rstrip('/') or '/'}"


def _norm_text(t: str | None) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


async def check_page(url: str, expected_title: str | None, expected_canonical: str | None) -> dict:
    out: dict = {"url": url, "checked_at": datetime.now(UTC).isoformat(), "ok": False, "status_code": None,
                 "final_url": url, "title": None, "canonical": None, "title_ok": None, "canonical_ok": None,
                 "noindex": False, "schema_blocks": 0, "message": ""}
    current = url
    try:
        async with net.client(20.0) as c:
            for _ in range(6):
                r = await c.get(current, headers={"Accept": "text/html"})
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = str(httpx.URL(current).join(r.headers["location"]))
                    continue
                break
    except httpx.HTTPError as e:
        out["message"] = f"Couldn't open the page ({type(e).__name__})."
        return out
    out["status_code"], out["final_url"] = r.status_code, current
    if r.status_code != 200:
        out["message"] = (f"The page answered {r.status_code}"
                          + (" (not found). If it's scheduled or a draft, it isn't public yet." if r.status_code == 404 else ".")
                          )
        return out
    p = _HeadParser()
    try:
        p.feed(r.text[:MAX_PAGE_BYTES])
    except Exception:  # noqa: BLE001 - malformed HTML still gives partial results
        pass
    out.update(title=p.title, canonical=p.canonical, schema_blocks=p.schema,
               noindex="noindex" in (p.robots or "").lower())
    if expected_title:
        et, got = _norm_text(expected_title), _norm_text(p.title)
        out["title_ok"] = bool(got) and (et in got or got in et)
    if expected_canonical:
        out["canonical_ok"] = _norm_url(p.canonical) == _norm_url(expected_canonical)
    problems = []
    if out["title_ok"] is False:
        problems.append(f"the page title is “{p.title or 'missing'}”, not the meta title")
    if out["canonical_ok"] is False:
        problems.append(f"the canonical link is {p.canonical or 'missing'} instead of {expected_canonical}")
    if out["noindex"]:
        problems.append("the page tells search engines not to index it (noindex)")
    out["ok"] = not problems
    out["message"] = ("The page is live and shows the expected title and canonical link." if not problems
                      else "The page is live, but " + "; ".join(problems) + ".")
    if current != url:
        out["message"] += f" (It redirected to {current}.)"
    return out


# ============================================================ IndexNow & Search Console
def new_indexnow_key() -> str:
    return pysecrets.token_hex(16)


def ensure_indexnow_key(cfg: ProviderConfig) -> str:
    f = fields(cfg)
    if not f.get("indexnow_key"):
        f["indexnow_key"] = new_indexnow_key()
        cfg.secrets_enc = encrypt_json(f)
    return f["indexnow_key"]


def indexnow_instructions(cfg: ProviderConfig, key: str) -> dict:
    site = site_of(cfg)
    return {
        "key": key, "file_name": f"{key}.txt", "file_url": f"{site}/{key}.txt" if site else f"/{key}.txt",
        "file_content": key,
        "steps": [
            f"Create a text file named {key}.txt that contains only: {key}",
            f"Upload it to the top folder of your website so it opens at {site or 'https://your-site'}/{key}.txt "
            "(for a React/Next site put it in the public/ folder; for WordPress use your host's file manager).",
            "Come back and press “Notify search engines” after publishing.",
        ],
    }


async def ping_indexnow(url: str, key: str) -> dict:
    p = urlsplit(url)
    host = p.hostname or ""
    body = {"host": host, "key": key, "keyLocation": f"{p.scheme}://{p.netloc}/{key}.txt", "urlList": [url]}
    try:
        async with net.client(20.0) as c:
            r = await c.post(INDEXNOW_ENDPOINT, json=body, headers={"Content-Type": "application/json; charset=utf-8"})
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"Couldn't reach IndexNow ({type(e).__name__}). Try again later.",
                "at": datetime.now(UTC).isoformat()}
    messages = {
        200: "Sent to IndexNow (Bing, Yandex, Seznam, Naver). Google doesn't use IndexNow; use Request indexing for Google.",
        202: "IndexNow accepted the address and will check your key file. Google doesn't use IndexNow; "
             "use Request indexing for Google.",
        400: "IndexNow said the request was invalid.",
        403: f"IndexNow couldn't confirm your key. Check that {body['keyLocation']} opens and contains only the key.",
        422: "IndexNow says this address doesn't belong to the site that holds the key.",
        429: "IndexNow is receiving too many requests from this site. Try again later.",
    }
    ok = r.status_code in (200, 202)
    return {"ok": ok, "status_code": r.status_code, "message": messages.get(r.status_code, f"IndexNow answered {r.status_code}."),
            "at": datetime.now(UTC).isoformat()}


def search_console_url(url: str, gsc_site: str | None = None) -> str:
    p = urlsplit(url)
    prop = gsc_site or f"{p.scheme}://{p.netloc}/"
    return f"https://search.google.com/search-console/inspect?resource_id={quote(prop, safe='')}&id={quote(url, safe='')}"


# ============================================================ status for the UI
def effective_status(pub: SitePublication) -> str:
    if pub.status == "scheduled" and pub.scheduled_at and pub.scheduled_at <= datetime.now(UTC):
        return "published"
    return pub.status


def publication_out(pub: SitePublication, article: SeoArticle, cfg: ProviderConfig | None, gsc_site: str | None = None) -> dict:
    status = effective_status(pub)
    live = status in ("published", "scheduled", "draft")
    d = pub.data or {}
    return {
        "id": pub.id, "provider_id": pub.provider_id, "target": pub.target,
        "connection": cfg.name if cfg else None, "status": status,
        "update_available": live and bool(pub.content_hash) and pub.content_hash != content_hash(article),
        "remote_id": pub.remote_id, "remote_url": pub.remote_url, "published_slug": pub.published_slug,
        "scheduled_at": pub.scheduled_at, "published_at": pub.published_at, "updated_at": pub.updated_at,
        "job_id": pub.job_id, "error": pub.error, "warnings": d.get("warnings") or [], "seo_plugin": d.get("seo_plugin"),
        "options": d.get("options") or {}, "check": d.get("check"), "indexnow": d.get("indexnow"),
        "search_console_url": search_console_url(pub.remote_url, gsc_site) if pub.remote_url and status == "published" else None,
        "can_unpublish": status in ("published", "scheduled") and (pub.target == "wordpress" or pub.target == "webhook"),
    }


def redirect_out(r: SiteRedirect) -> dict:
    return {"id": r.id, "article_id": r.article_id, "provider_id": r.provider_id, "from_path": r.from_path,
            "to_path": r.to_path, "status": r.status, "error": r.error, "pushed_at": r.pushed_at, "created_at": r.created_at}


# ============================================================ "Download for website" bundle
def head_html(article: SeoArticle, org: Organization | None, canon: str, image_url: str | None, schema: dict) -> str:
    e = html.escape
    title = article.meta_title or article.title
    tags = [
        f"<title>{e(title)}</title>",
        f'<meta name="description" content="{e(article.meta_description or "")}">',
        f'<link rel="canonical" href="{e(canon)}">',
        '<meta property="og:type" content="article">',
        f'<meta property="og:title" content="{e(title)}">',
        f'<meta property="og:description" content="{e(article.meta_description or "")}">',
    ]
    if image_url:
        tags.append(f'<meta property="og:image" content="{e(image_url)}">')
        tags.append(f'<meta property="og:image:alt" content="{e(article.title)}">')
    tags.append(seo.head_extras(article, org, canon, image_url))
    tags.append('<script type="application/ld+json">' + json.dumps(schema, ensure_ascii=False).replace("</", "<\\/")
                + "</script>")
    return "\n".join(tags)


async def build_bundle(session: AsyncSession, article: SeoArticle) -> tuple[str, bytes]:
    """A zip for static / Git-based sites: index.html with every head tag, the cover image, MDX and meta.json."""
    org = await session.get(Organization, article.organization_id)
    folder = await seo.export_article(session, article)  # reuse the export (mdx, html, meta.json, cover)
    canon = seo.canonical_url(article, org)
    cover = next((p for p in folder.iterdir() if p.name.startswith("cover.")), None)
    image_url = f"{canon.rstrip('/')}/{cover.name}" if cover and canon.startswith("http") else (f"./{cover.name}" if cover else None)
    schema = seo.build_schema(article, org, image=image_url)
    page = seo.HTML_TEMPLATE.format(
        title=html.escape(article.meta_title or article.title), description=html.escape(article.meta_description or ""),
        canonical=html.escape(canon), schema=json.dumps(schema, ensure_ascii=False).replace("</", "<\\/"),
        og_image=(f'<meta property="og:image" content="{html.escape(image_url)}">\n'
                  f'<meta property="og:image:alt" content="{html.escape(article.title)}">') if image_url else "",
        extra_head=seo.head_extras(article, org, canon, image_url),
        cover=(f'<figure class="cover"><img src="./{html.escape(cover.name)}" alt="{html.escape(article.title)}"></figure>'
               if cover else ""),
        org=html.escape(org.name if org else ""), date=(article.updated_at or datetime.now(UTC)).date().isoformat(),
        body=seo.md_to_html(article.body_md or ""),
    )
    redirects = (await session.scalars(select(SiteRedirect).where(SiteRedirect.article_id == article.id))).all()
    readme = (
        f"{article.title}\n\n"
        f"Put this folder on your website so index.html opens at {canon}\n"
        f"(for example public/blog/{article.slug}/ in a React/Vite site, or app/blog/{article.slug}/ assets in Next.js).\n"
        "index.html has the title, description, canonical link, Open Graph / Twitter tags and JSON-LD schema.\n"
        "article.mdx has front matter for MDX/Markdown-based sites; meta.json has every field and the schema.\n"
        + ("redirects/ has the old addresses to forward after a slug change, in several formats.\n" if redirects else "")
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{article.slug}/index.html", page)
        for p in folder.iterdir():
            if p.name in ("article.mdx", "meta.json") or p == cover:
                z.write(p, f"{article.slug}/{p.name}")
        z.writestr(f"{article.slug}/schema.json", json.dumps(schema, indent=2, ensure_ascii=False))
        z.writestr(f"{article.slug}/README.txt", readme)
        if redirects:
            for fmt in REDIRECT_FORMATS:
                name, _, text = export_redirects(list(redirects), fmt)
                z.writestr(f"{article.slug}/redirects/{name}", text)
    return f"{article.slug}-website.zip", buf.getvalue()
