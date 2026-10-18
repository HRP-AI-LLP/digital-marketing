"""Networks background module: importing Canva designs into the media library (`canva.import`)."""

import logging

from app.services.jobs import JobContext, PermanentError, handler
from app.services.social import canva

log = logging.getLogger("networks")


@handler("canva.import")
async def canva_import(ctx: JobContext) -> dict:
    p = ctx.payload or {}
    await ctx.progress("Asking Canva to export the design…")
    try:
        assets = await canva.import_design(ctx.session, ctx.org_id, str(p.get("design_id") or ""), p.get("format") or "png",
                                           p.get("pages"), progress=ctx.progress)
    except canva.CanvaError as e:
        await ctx.session.commit()  # keep rotated tokens / expired status
        if e.retryable:
            raise
        raise PermanentError(e.message)
    await ctx.session.commit()
    return {"asset_ids": [str(a.id) for a in assets], "count": len(assets)}
