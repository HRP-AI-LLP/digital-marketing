"""Link tracking: the API (/api/v1/links, permissions links.view / links.manage) and the public short-link redirect
/r/<code> (no sign-in; allowed through the tunnel, see main.TUNNEL_ALLOWED)."""

import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import Campaign, LinkClick, Post, TrackedLink
from app.services import audit
from app.services import links as svc
from app.services.platforms import PLATFORMS, offered_platforms
from app.services.reports import ReportInputError, parse_period

router = APIRouter(prefix="/links", tags=["links"])
public = APIRouter(tags=["links public"], include_in_schema=False)

VIEW, MANAGE = "links.view", "links.manage"


class LinkIn(BaseModel):
    url: str = Field(max_length=2000)
    label: str | None = Field(None, max_length=200)
    platform: str | None = Field(None, max_length=30)  # a social platform id, or bio | email | website | print | other
    campaign_id: uuid.UUID | None = None
    utm: dict[str, str] = Field(default_factory=dict)


class LinkPatch(BaseModel):
    label: str | None = Field(None, max_length=200)


class SettingsIn(BaseModel):
    enabled: bool


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


def _period(ctx: Context, start: str | None, end: str | None, days: int):
    tz = ZoneInfo(ctx.org.timezone or "Asia/Kolkata")
    try:
        return parse_period({"start": start, "end": end}, tz, max(1, min(days, 366)))
    except ReportInputError as e:
        raise _bad(str(e))


def _settings_out(ctx: Context) -> dict:
    base = svc.public_base()
    return {**svc.settings_of(ctx.org), "base_url": base or None, "ready": bool(base),
            "channels": [{"id": k, "label": k.replace("-", " ").capitalize() if k != "bio" else "Link-in-bio"}
                         for k in svc.CHANNELS],
            "platforms": [{"id": p.id, "label": p.label} for p in offered_platforms() if p.id != "manual"]}


async def _posts(ctx: Context, ids) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = (await ctx.session.scalars(select(Post).where(Post.id.in_(ids)))).all()
    return {p.id: {"id": str(p.id), "title": (p.title or p.text or p.topic or "Untitled post")[:140],
                   "platform": p.platform, "status": p.status,
                   "published_at": p.published_at.isoformat() if p.published_at else None,
                   "external_url": p.external_url} for p in rows}


@router.get("/settings")
async def get_settings_(ctx: Context = Depends(require(VIEW))):
    return _settings_out(ctx)


@router.put("/settings")
async def save_settings(body: SettingsIn, ctx: Context = Depends(require(MANAGE))):
    org = ctx.org
    cur = dict((org.publishing or {}).get("link_tracking") or {})
    cur["enabled"] = body.enabled
    org.publishing = {**(org.publishing or {}), "link_tracking": cur}
    audit.record(ctx.session, action="links.settings", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="organization", entity_id=org.id,
                 summary=f"Automatic link tracking turned {'on' if body.enabled else 'off'}", ip=ctx.ip)
    await ctx.session.commit()
    return _settings_out(ctx)


@router.get("")
async def list_links(start: str | None = None, end: str | None = None, days: int = 30,
                     campaign_id: uuid.UUID | None = None, platform: str | None = None, post_id: uuid.UUID | None = None,
                     source: str | None = Query(None, pattern="^(auto|manual)$"), q: str | None = None,
                     ctx: Context = Depends(require(VIEW))):
    s = ctx.session
    d0, d1 = _period(ctx, start, end, days)
    query = select(TrackedLink).order_by(TrackedLink.created_at.desc())
    if campaign_id:
        query = query.where(TrackedLink.campaign_id == campaign_id)
    if platform:
        query = query.where(TrackedLink.platform == platform)
    if post_id:
        query = query.where(TrackedLink.post_id == post_id)
    if source:
        query = query.where(TrackedLink.source == source)
    if q and q.strip():
        like = f"%{q.strip()[:100]}%"
        query = query.where(TrackedLink.target_url.ilike(like) | TrackedLink.label.ilike(like) | TrackedLink.code.ilike(like))
    links = list((await s.scalars(query.limit(2000))).all())
    filtered = bool(campaign_id or platform or post_id or source or (q and q.strip()))
    ids = [lk.id for lk in links] if filtered else None
    if filtered and not ids:
        ids = [uuid.uuid4()]  # matches nothing
    span = (d1 - d0).days + 1
    per = await svc.clicks_by_link(s, d0, d1, ids)
    prev = sum((await svc.clicks_by_link(s, d0 - timedelta(days=span), d0 - timedelta(days=1), ids)).values())
    posts = await _posts(ctx, {lk.post_id for lk in links})
    base = svc.public_base()
    items = []
    for lk in links:
        row = svc.link_dict(lk, base, per.get(lk.id, 0))
        row["post"] = posts.get(lk.post_id)
        items.append(row)
    items.sort(key=lambda r: (r["clicks"], r["created_at"] or ""), reverse=True)
    by_platform: dict[str, int] = {}
    by_post: dict[str, dict] = {}
    for r in items:
        if r["clicks"]:
            key = r["platform"] or "other"
            by_platform[key] = by_platform.get(key, 0) + r["clicks"]
        if r["post"] is not None:
            t = by_post.setdefault(r["post"]["id"], {**r["post"], "clicks": 0, "links": 0})
            t["clicks"] += r["clicks"]
            t["links"] += 1
    total = sum(per.values())
    return {
        "period": {"start": d0.isoformat(), "end": d1.isoformat(), "days": span},
        "settings": _settings_out(ctx),
        "kpis": {"clicks": total, "clicks_previous": prev, "links": len(links),
                 "links_clicked": sum(1 for r in items if r["clicks"]),
                 "clicks_all_time": sum(lk.clicks_total or 0 for lk in links),
                 "per_day": round(total / span, 1) if span else 0},
        "series": await svc.series(s, d0, d1, ids),
        "by_platform": sorted(({"key": k, "clicks": v} for k, v in by_platform.items()), key=lambda x: -x["clicks"]),
        "by_referrer": await svc.breakdown(s, d0, d1, LinkClick.referrer, ids),
        "by_app": await svc.breakdown(s, d0, d1, LinkClick.app, ids),
        "by_device": await svc.breakdown(s, d0, d1, LinkClick.device, ids),
        "top_posts": sorted(by_post.values(), key=lambda x: -x["clicks"])[:20],
        "items": items[:500],
    }


async def _get(ctx: Context, link_id: uuid.UUID) -> TrackedLink:
    lk = await ctx.session.get(TrackedLink, link_id)
    if lk is None:
        raise _bad("That link doesn't exist.", status.HTTP_404_NOT_FOUND)
    return lk


@router.get("/{link_id}")
async def get_link(link_id: uuid.UUID, start: str | None = None, end: str | None = None, days: int = 30,
                   ctx: Context = Depends(require(VIEW))):
    lk = await _get(ctx, link_id)
    s = ctx.session
    d0, d1 = _period(ctx, start, end, days)
    ids = [lk.id]
    per = await svc.clicks_by_link(s, d0, d1, ids)
    posts = await _posts(ctx, [lk.post_id])
    return {**svc.link_dict(lk, svc.public_base(), per.get(lk.id, 0)), "post": posts.get(lk.post_id),
            "period": {"start": d0.isoformat(), "end": d1.isoformat(), "days": (d1 - d0).days + 1},
            "series": await svc.series(s, d0, d1, ids),
            "by_referrer": await svc.breakdown(s, d0, d1, LinkClick.referrer, ids),
            "by_app": await svc.breakdown(s, d0, d1, LinkClick.app, ids),
            "by_device": await svc.breakdown(s, d0, d1, LinkClick.device, ids)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: LinkIn, ctx: Context = Depends(require(MANAGE))):
    url = svc.safe_target(body.url)
    if url is None:
        raise _bad("Enter a full web address that starts with https:// (or http://).")
    if svc.is_short_link(url):
        raise _bad("That is already a tracked link. Paste the address it should open instead.")
    platform = (body.platform or "").strip() or None
    if platform and platform not in PLATFORMS and platform not in svc.CHANNELS:
        raise _bad("Choose where you'll share this link.")
    if body.campaign_id and await ctx.session.get(Campaign, body.campaign_id) is None:
        raise _bad("That campaign doesn't exist.")
    lk = await svc.create_link(ctx.session, ctx.org_id, url, platform=platform, campaign_id=body.campaign_id,
                               label=body.label, utm=body.utm, source="manual", created_by=ctx.user.id)
    audit.record(ctx.session, action="links.created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="tracked_link", entity_id=lk.id, summary=f"Tracked link /r/{lk.code} created", ip=ctx.ip)
    await ctx.session.commit()
    return {**svc.link_dict(lk, svc.public_base(), 0), "post": None}


@router.patch("/{link_id}")
async def rename(link_id: uuid.UUID, body: LinkPatch, ctx: Context = Depends(require(MANAGE))):
    lk = await _get(ctx, link_id)
    lk.label = (body.label or "").strip()[:200] or None
    await ctx.session.commit()
    return svc.link_dict(lk, svc.public_base())


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(link_id: uuid.UUID, ctx: Context = Depends(require(MANAGE))):
    lk = await _get(ctx, link_id)
    if lk.source == "auto":
        raise _bad("This link is in a published post, so it can't be deleted (the post would stop working).")
    audit.record(ctx.session, action="links.deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="tracked_link", entity_id=lk.id, summary=f"Tracked link /r/{lk.code} deleted", ip=ctx.ip)
    await ctx.session.delete(lk)
    await ctx.session.commit()


# ============================================================================ public redirect
_NOT_FOUND = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
              "content='width=device-width,initial-scale=1'><meta name='robots' content='noindex'><title>Link not found"
              "</title></head><body style='font-family:system-ui,sans-serif;text-align:center;padding:60px 16px;"
              "color:#334155'><h1 style='font-size:1.5rem'>This link doesn't work any more</h1><p>The link you followed "
              "may be mistyped, or it was removed. Please check the address and try again.</p></body></html>")
_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "X-Content-Type-Options": "nosniff"}


@public.api_route("/r/{code}", methods=["GET", "HEAD"])
async def follow(code: str, request: Request):
    found = await svc.find_public(code)
    if found is None:
        return HTMLResponse(_NOT_FOUND, status_code=404, headers={**_HEADERS, "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
    link, org = found
    if request.method == "GET":
        try:
            await svc.record_click(org, link, request.headers)
        except Exception:  # noqa: BLE001 - counting must never stop the visitor reaching the page
            svc.log.warning("Couldn't count a click on /r/%s", code, exc_info=True)
    return RedirectResponse(svc.redirect_url(link), status_code=status.HTTP_302_FOUND,
                            headers={**_HEADERS, "Referrer-Policy": "strict-origin-when-cross-origin"})
