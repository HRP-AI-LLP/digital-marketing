"""Canva import: connect (OAuth with PKCE), browse / search designs, import designs into the media library.

Connecting Canva needs the free Canva Connect API integration (catalog provider canva_app). Nothing else in the
app depends on it.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.deps import Context, require
from app.services import audit
from app.services.jobs import enqueue
from app.services.social import canva, common
from app.services.social.common import PublishError

router = APIRouter(prefix="/canva", tags=["canva"])

PERM = "media.manage"


def _err(e: PublishError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY if e.retryable else status.HTTP_400_BAD_REQUEST, e.message)


@router.get("/status")
async def canva_status(ctx: Context = Depends(require(PERM))):
    from app.api.social import guide

    conn = await canva.connection(ctx.session)
    return {"configured": bool(await common.app_credentials(ctx.session, "canva")),
            "connected": bool(conn and conn.status == "connected"),
            "status": conn.status if conn else None, "status_message": conn.status_message if conn else None,
            "display_name": conn.display_name if conn else None,
            "redirect_uri": common.redirect_uri("canva"), "guide": guide("canva")}


class ConnectIn(BaseModel):
    return_to: str | None = Field("/media", max_length=200)


@router.post("/connect")
async def canva_connect(body: ConnectIn | None = None, ctx: Context = Depends(require(PERM))):
    from app.api.social import start_oauth

    return await start_oauth(ctx, "canva", {}, (body.return_to if body else None) or "/media")


@router.delete("/connection", status_code=204)
async def canva_disconnect(ctx: Context = Depends(require(PERM))):
    conn = await canva.connection(ctx.session)
    if conn is not None:
        await ctx.session.delete(conn)
        audit.record(ctx.session, action="canva.disconnected", organization_id=ctx.org_id, actor=ctx.user,
                     entity_type="design_connection", entity_id=conn.id, summary="Disconnected Canva", ip=ctx.ip)
        await ctx.session.commit()


@router.get("/designs")
async def canva_designs(query: str | None = None, continuation: str | None = None, ctx: Context = Depends(require(PERM))):
    try:
        out = await canva.list_designs(ctx.session, query, continuation)
    except PublishError as e:
        await ctx.session.commit()  # keeps an 'expired' status or rotated tokens
        raise _err(e)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach Canva. Try again.")
    await ctx.session.commit()
    return out


class ImportIn(BaseModel):
    design_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    format: str = "png"
    pages: list[int] | None = Field(None, max_length=canva.MAX_PAGES)


@router.post("/import", status_code=202)
async def canva_import(body: ImportIn, ctx: Context = Depends(require(PERM))):
    if body.format not in canva.FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose PNG, JPG, PDF, MP4 or GIF.")
    conn = await canva.connection(ctx.session)
    if conn is None or conn.status != "connected":
        raise HTTPException(status.HTTP_409_CONFLICT, "Connect Canva first.")
    job = await enqueue(ctx.session, "canva.import", ctx.org_id,
                        {"design_id": body.design_id, "format": body.format, "pages": body.pages},
                        created_by=ctx.user.id, max_attempts=2)
    await ctx.session.commit()
    return {"job_id": job.id}
