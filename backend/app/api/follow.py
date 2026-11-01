"""Follow kit API (/api/v1/follow, permissions links.view / links.manage) and the public "follow us" page (/f/<slug>).

Every code and image encodes the profile's permanent short link, so clicks are counted and the destination can be
changed later without reprinting. Only GET /f/<slug> is reachable through the public tunnel (see main.tunnel_guard).
"""

from __future__ import annotations

import base64
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db import SessionLocal, platform_session, set_tenant
from app.core.deps import Context, require
from app.models import Organization
from app.services import audit
from app.services import follow_kit as svc

router = APIRouter(prefix="/follow", tags=["follow kit"])
public = APIRouter(tags=["follow kit public"], include_in_schema=False)
VIEW, MANAGE = "links.view", "links.manage"
_PAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; "
                               "form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "strict-origin-when-cross-origin", "Cache-Control": "no-store",
}


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


async def _kit(ctx: Context) -> tuple[dict, Organization]:
    org = await ctx.session.get(Organization, ctx.org_id)
    kit = await svc.build(ctx.session, org)
    await ctx.session.commit()
    return kit, org


async def _brand(session, org_id):
    from app.services.media import brand_style, load_brand

    return await brand_style(session, await load_brand(session), org_id)


def _logo_png(brand) -> bytes | None:
    uri = getattr(brand, "logo_uri", None) or ""
    if not uri.startswith("data:image/") or ";base64," not in uri:
        return None
    try:
        return base64.b64decode(uri.split(",", 1)[1])
    except ValueError:
        return None


def _item(kit: dict, key: str) -> dict:
    it = svc.find(kit, key)
    if it is None:
        raise _bad("That profile isn't in your follow kit.", status.HTTP_404_NOT_FOUND)
    if not it.get("short_url"):
        raise _bad("Add the profile's public address first.")
    return it


def _file(data: bytes | str, mime: str, name: str, download: bool) -> Response:
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return Response(data, media_type=mime, headers=headers)


# ------------------------------------------------------------------ kit
@router.get("")
async def get_kit(ctx: Context = Depends(require(VIEW))):
    kit, _ = await _kit(ctx)
    return kit


class ItemIn(BaseModel):
    key: str = Field(max_length=80)
    url: str | None = Field(None, max_length=1000)
    include: bool | None = None


class KitIn(BaseModel):
    title: str | None = Field(None, max_length=80)
    message: str | None = Field(None, max_length=280)
    slug: str | None = Field(None, max_length=42)
    theme: Literal["dark", "light"] | None = None
    order: list[str] | None = None
    items: list[ItemIn] = []


@router.put("")
async def save_kit(body: KitIn, ctx: Context = Depends(require(MANAGE))):
    org = await ctx.session.get(Organization, ctx.org_id)
    data = body.model_dump(exclude_unset=True)
    data["items"] = [i.model_dump(exclude_unset=True) for i in body.items]
    if body.slug and body.slug != svc.settings_of(org)["slug"]:
        rows = (await _all_orgs())
        if any(o.id != org.id and svc.settings_of(o)["slug"] == body.slug.lower() for o in rows):
            raise _bad("That page address is taken. Try another.")
    try:
        svc.update_settings(org, data)
    except ValueError as e:
        raise _bad(str(e)) from e
    audit.record(ctx.session, action="links.follow_kit", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="organization", entity_id=ctx.org_id, summary="Updated the follow kit", ip=ctx.ip,
                 details={k: v for k, v in data.items() if k != "items"} | ({"profiles_changed": len(data["items"])} if data["items"] else {}))
    kit = await svc.build(ctx.session, org)
    await ctx.session.commit()
    return kit


# ------------------------------------------------------------------ codes & images
Style = Literal["square", "dots"]


@router.get("/{key}/qr.{fmt}")
async def qr_code(key: str, fmt: Literal["png", "svg"], ctx: Context = Depends(require(VIEW)),
                  fg: str = "#0b1d33", bg: str = "#ffffff", style: Style = "square", logo: bool = True,
                  size: int = Query(1024, ge=128, le=4096), download: bool = False):
    kit, org = await _kit(ctx)
    it = _item(kit, key)
    fg, bg = svc.colour(fg, "#0b1d33"), svc.colour(bg, "#ffffff")
    brand = await _brand(ctx.session, ctx.org_id) if logo else None
    name = f"qr-{svc.file_stem(it)}.{fmt}"
    if fmt == "svg":
        return _file(svc.qr_svg(it["short_url"], fg=fg, bg=bg, style=style, logo_uri=brand.logo_uri if brand else None),
                     "image/svg+xml", name, download)
    return _file(svc.qr_png(it["short_url"], px=size, fg=fg, bg=bg, style=style, logo_png=_logo_png(brand) if brand else None),
                 "image/png", name, download)


@router.get("/{key}/barcode.{fmt}")
async def bar_code(key: str, fmt: Literal["png", "svg"], ctx: Context = Depends(require(VIEW)),
                   fg: str = "#000000", bg: str = "#ffffff", download: bool = False):
    kit, _ = await _kit(ctx)
    it = _item(kit, key)
    fg, bg = svc.colour(fg, "#000000"), svc.colour(bg, "#ffffff")
    name = f"barcode-{svc.file_stem(it)}.{fmt}"
    if fmt == "svg":
        return _file(svc.barcode_svg(it["short_url"], fg, bg), "image/svg+xml", name, download)
    return _file(svc.barcode_png(it["short_url"], fg, bg), "image/png", name, download)


@router.get("/{key}/card.png")
async def card(key: str, ctx: Context = Depends(require(VIEW)),
               shape: Literal["square", "portrait", "story", "landscape"] = "square",
               theme: Literal["dark", "light"] = "dark", download: bool = False):
    kit, _ = await _kit(ctx)
    it = _item(kit, key)
    brand = await _brand(ctx.session, ctx.org_id)
    try:
        png = await svc.card_png(it, kit, brand, shape, theme, _logo_png(brand))
    except Exception as e:  # noqa: BLE001 - the renderer (Chromium) can be missing or busy
        raise _bad(f"Couldn't draw the image: {str(e)[:200]}", status.HTTP_503_SERVICE_UNAVAILABLE) from e
    return _file(png, "image/png", f"follow-{svc.file_stem(it)}-{shape}.png", download)


@router.get("/pack.zip")
async def pack(ctx: Context = Depends(require(VIEW)), keys: str = Query("", max_length=4000),
               images: bool = True, shape: Literal["square", "portrait", "story", "landscape"] = "square",
               theme: Literal["dark", "light"] = "dark", fg: str = "#0b1d33", bg: str = "#ffffff", style: Style = "square",
               logo: bool = True):
    """One download with the QR codes (PNG + SVG), barcodes, links and (optionally) images for the chosen profiles."""
    kit, _ = await _kit(ctx)
    wanted = [k for k in keys.split(",") if k] or ["all"] + [i["key"] for i in kit["items"] if i["include"] and i["url"]]
    brand = await _brand(ctx.session, ctx.org_id)
    lp = _logo_png(brand) if logo else None
    fg, bg = svc.colour(fg, "#0b1d33"), svc.colour(bg, "#ffffff")
    files: list[tuple[str, bytes]] = []
    lines = ["Profile,Short link,Opens"]
    for k in wanted:
        it = svc.find(kit, k)
        if not it or not it.get("short_url"):
            continue
        stem = svc.file_stem(it)
        files.append((f"{stem}/qr-{stem}.png", svc.qr_png(it["short_url"], px=1024, fg=fg, bg=bg, style=style, logo_png=lp)))
        files.append((f"{stem}/qr-{stem}.svg", svc.qr_svg(it["short_url"], fg=fg, bg=bg, style=style,
                                                          logo_uri=brand.logo_uri if logo else None).encode()))
        files.append((f"{stem}/barcode-{stem}.png", svc.barcode_png(it["short_url"], "#000000", "#ffffff")))
        files.append((f"{stem}/barcode-{stem}.svg", svc.barcode_svg(it["short_url"], "#000000", "#ffffff").encode()))
        if images:
            try:
                files.append((f"{stem}/follow-{stem}-{shape}.png", await svc.card_png(it, kit, brand, shape, theme, lp)))
            except Exception:  # noqa: BLE001 - codes are still useful without the image
                pass
        label = "All channels page" if k == "all" else f"{it['platform_label']} ({it.get('handle') or it['name']})"
        lines.append(f'"{label}",{it["short_url"]},{it.get("page_url") or it.get("url")}')
    if not files:
        raise _bad("Nothing to download yet. Add profile addresses first.")
    files.append(("links.csv", ("﻿" + "\n".join(lines)).encode()))
    return Response(svc.pack(files), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="follow-kit.zip"', "Cache-Control": "no-store"})


# ------------------------------------------------------------------ public page
async def _all_orgs() -> list[Organization]:
    async with platform_session() as ps:
        return list((await ps.scalars(select(Organization).where(Organization.status == "active"))).all())


_NOT_FOUND = ("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Not found</title>"
              "<body style='font-family:system-ui;text-align:center;padding:60px 16px;color:#334155'><h1>Page not found</h1>"
              "<p>This link may have changed.</p></body>")


@public.get("/f/{slug}")
async def public_follow(slug: str):
    org = next((o for o in await _all_orgs() if svc.settings_of(o)["slug"] == slug.lower()), None)
    if org is None:
        return HTMLResponse(_NOT_FOUND, status_code=404, headers=_PAGE_HEADERS)
    async with SessionLocal() as s:
        set_tenant(s, org.id)
        org = await s.get(Organization, org.id)
        kit = await svc.build(s, org)
        brand = await _brand(s, org.id)
        from app.services.media import load_brand

        b = await load_brand(s)
        await s.commit()
        html = svc.page_html(kit, org, brand.logo_uri, (b.colors if b else None) or {})
    return HTMLResponse(html, headers=_PAGE_HEADERS)
