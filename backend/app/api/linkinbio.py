"""Link-in-bio: the editor API (/api/v1/linkinbio, permission linkinbio.manage) and the public, unauthenticated
page served by the backend at /l/<slug> with its click-redirect (/l/<slug>/go/<id>) and image (/l/<slug>/img/<id>)
endpoints. Only these GET paths are allowed through the public tunnel (see main.tunnel_guard)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db import SessionLocal, platform_session, set_tenant
from app.core.deps import Context, require
from app.models import LinkPage, MediaAsset, Organization, Post
from app.services import audit
from app.services import linkinbio as svc

router = APIRouter(prefix="/linkinbio", tags=["link-in-bio"])
public = APIRouter(tags=["link-in-bio public"], include_in_schema=False)

PERM = "linkinbio.manage"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data: https:; style-src 'unsafe-inline'; "
                               "base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


class LinkIn(BaseModel):
    id: str | None = None
    title: str = Field(max_length=100)
    url: str = Field(max_length=1000)
    icon: str | None = Field(None, max_length=4)
    thumbnail_asset_id: uuid.UUID | None = None
    highlight: bool = False
    enabled: bool = True


class PageIn(BaseModel):
    slug: str = Field(min_length=1, max_length=60)
    published: bool = False
    title: str | None = Field(None, max_length=120)
    tagline: str | None = Field(None, max_length=300)
    show_logo: bool = True
    show_socials: bool = True
    show_posts: bool = False
    posts_count: int = Field(6, ge=3, le=12)
    links: list[LinkIn] = Field(default_factory=list, max_length=svc.MAX_LINKS)
    theme: dict = Field(default_factory=dict)


async def _page(ctx: Context) -> LinkPage | None:
    return await ctx.session.scalar(select(LinkPage))


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


async def _validated(ctx: Context, body: PageIn) -> dict:
    slug = body.slug.strip().lower()
    if not svc.SLUG_RE.match(slug) or slug in svc.RESERVED:
        raise _bad("The page address can use lowercase letters, numbers and dashes (not at the start or end).")
    async with platform_session() as ps:  # slugs are unique across all organizations
        taken = await ps.scalar(select(LinkPage.organization_id).where(LinkPage.slug == slug))
    if taken and taken != ctx.org_id:
        raise _bad("That page address is already taken. Try another one.", status.HTTP_409_CONFLICT)
    bad = [lk.title for lk in body.links if not svc.safe_url(lk.url)]
    if bad:
        raise _bad(f"The link for “{bad[0]}” must start with https://, http://, mailto: or tel:.")
    links = svc.clean_links([lk.model_dump(mode="json") for lk in body.links])
    for lk in links:
        if lk["thumbnail_asset_id"]:
            a = await ctx.session.get(MediaAsset, uuid.UUID(lk["thumbnail_asset_id"]))
            if a is None or not (a.kind in ("image", "gif") or (a.meta or {}).get("poster_path")):
                lk["thumbnail_asset_id"] = None
    return {"slug": slug, "published": body.published, "title": (body.title or "").strip() or None,
            "tagline": (body.tagline or "").strip() or None, "show_logo": body.show_logo,
            "show_socials": body.show_socials, "show_posts": body.show_posts, "posts_count": body.posts_count,
            "links": links, "theme": svc.clean_theme(body.theme)}


def _media_url(ctx: Context, aid) -> str | None:
    from app.core.signing import media_url

    return media_url(aid, ctx.org_id) if aid else None


async def _out(ctx: Context, data: dict, exists: bool) -> dict:
    org = ctx.org
    logo = await svc.org_logo_id(ctx.session, org)
    thumbs = {lk["thumbnail_asset_id"]: _media_url(ctx, lk["thumbnail_asset_id"])
              for lk in data["links"] if lk.get("thumbnail_asset_id")}
    return {**data, "exists": exists, **svc.page_urls(data["slug"]),
            "thumbnail_urls": thumbs,
            "organization": {"name": org.name, "logo_url": _media_url(ctx, logo),
                             "socials": svc.socials(org)}}


@router.get("")
async def get_page(ctx: Context = Depends(require(PERM))):
    p = await _page(ctx)
    return await _out(ctx, svc.page_dict(p) if p else svc.defaults(ctx.org), p is not None)


@router.put("")
async def save_page(body: PageIn, ctx: Context = Depends(require(PERM))):
    data = await _validated(ctx, body)
    p = await _page(ctx)
    created = p is None
    if p is None:
        p = LinkPage(organization_id=ctx.org_id)
        ctx.session.add(p)
    was_published = bool(p.published)
    for k, v in data.items():
        setattr(p, k, v)
    p.updated_by = ctx.user.id
    action = "published" if data["published"] and not was_published else "unpublished" \
        if was_published and not data["published"] else "updated"
    audit.record(ctx.session, action=f"linkinbio.{action}", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="link_page", entity_id=p.id, summary=f"Link-in-bio page {action} (/l/{p.slug})", ip=ctx.ip)
    await ctx.session.commit()
    return await _out(ctx, svc.page_dict(p), True) | {"created": created}


@router.post("/preview")
async def preview(body: PageIn, ctx: Context = Depends(require(PERM))):
    data = await _validated(ctx, body)
    return {"html": await svc.build_html(ctx.session, data, ctx.org, preview=True)}


@router.get("/stats")
async def page_stats(days: int = 30, ctx: Context = Depends(require(PERM))):
    p = await _page(ctx)
    if p is None:
        return {"days": days, "views": 0, "clicks": 0, "click_rate": None, "by_day": [], "by_target": []}
    return await svc.stats(ctx.session, p, ctx.org, days)


# ============================================================================ public page
_NOT_FOUND = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
              "content='width=device-width,initial-scale=1'><title>Page not found</title></head><body style="
              "'font-family:system-ui,sans-serif;text-align:center;padding:60px 16px;color:#334155'><h1>Page not found"
              "</h1><p>This link page doesn't exist or isn't published yet.</p></body></html>")


async def _public_page(slug: str) -> tuple[LinkPage, Organization] | None:
    slug = (slug or "").lower()
    if not svc.SLUG_RE.match(slug):
        return None
    async with platform_session() as ps:
        row = (await ps.execute(select(LinkPage, Organization).join(Organization, Organization.id == LinkPage.organization_id)
                                .where(LinkPage.slug == slug))).first()
    if row is None:
        return None
    page, org = row
    if not page.published or org.status != "active":
        return None
    return page, org


@public.get("/l/{slug}")
async def public_page(slug: str, request: Request):
    found = await _public_page(slug)
    if found is None:
        return HTMLResponse(_NOT_FOUND, status_code=404, headers=SECURITY_HEADERS)
    page, org = found
    async with SessionLocal() as s:
        set_tenant(s, org.id)
        html = await svc.build_html(s, svc.page_dict(page), org, preview=False)
        if svc.countable(request.headers):
            await svc.bump(s, page, org, "view")
            await s.commit()
    return HTMLResponse(html, headers={**SECURITY_HEADERS, "Cache-Control": "no-store"})


@public.get("/l/{slug}/go/{target}")
async def public_go(slug: str, target: str, request: Request):
    found = await _public_page(slug)
    if found is None:
        return HTMLResponse(_NOT_FOUND, status_code=404, headers=SECURITY_HEADERS)
    page, org = found
    url = None
    if target.startswith("s-"):
        social = {x["key"]: x["url"] for x in svc.socials(org)}
        url = social.get(target[2:]) if page.show_socials else None
    else:
        link = next((lk for lk in page.links or [] if lk.get("id") == target and lk.get("enabled", True)), None)
        if link:
            url = svc.safe_url(link.get("url"))
    async with SessionLocal() as s:
        set_tenant(s, org.id)
        if url is None and not target.startswith("s-") and page.show_posts:
            try:
                pid = uuid.UUID(target)
            except ValueError:
                pid = None
            post = await s.get(Post, pid) if pid else None
            if post is not None and post.status == "published":
                url = svc.safe_url(post.external_url)
        if url is None:
            return HTMLResponse(_NOT_FOUND, status_code=404, headers=SECURITY_HEADERS)
        if svc.countable(request.headers):
            await svc.bump(s, page, org, target)
            await s.commit()
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "strict-origin-when-cross-origin"})


@public.get("/l/{slug}/img/{asset_id}")
async def public_image(slug: str, asset_id: str):
    """Only images the page actually shows: the logo, button thumbnails and post-grid pictures."""
    from app.services import media as media_svc

    found = await _public_page(slug)
    try:
        aid = uuid.UUID(asset_id)
    except ValueError:
        aid = None
    if found is None or aid is None:
        return Response("Not found", status_code=404)
    page, org = found
    async with SessionLocal() as s:
        set_tenant(s, org.id)
        allowed = {str(x) for x in [await svc.org_logo_id(s, org)] if x}
        allowed |= {lk["thumbnail_asset_id"] for lk in page.links or [] if lk.get("thumbnail_asset_id")}
        if page.show_posts:
            allowed |= {p["asset_id"] for p in await svc.latest_posts(s, page.posts_count or 6)}
        if str(aid) not in allowed:
            return Response("Not found", status_code=404)
        asset = await s.get(MediaAsset, aid)
    if asset is None:
        return Response("Not found", status_code=404)
    path, mime = media_svc.asset_file(asset, None if asset.kind in ("image", "gif") else "poster")
    if path is None or not (mime or "").startswith("image/"):
        return Response("Not found", status_code=404)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "public, max-age=3600",
                                                        "X-Content-Type-Options": "nosniff"})
