"""AI suggestions for the Brand page. Generation runs as a background job ("brand.suggest"); the result
(current vs suggested values) comes back in the job and the person accepts fields one by one. Accepting uses
the normal brand and pillar endpoints, so nothing is ever overwritten silently."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.deps import Context, require
from app.services.jobs import enqueue
from app.services.llm.router import resolve_chain

router = APIRouter(prefix="/brand-ai", tags=["brand"])


class SuggestIn(BaseModel):
    kind: Literal["voice", "pillars", "campaigns"]


@router.post("/suggest", status_code=202)
async def suggest(body: SuggestIn, ctx: Context = Depends(require("content.create"))):
    if not await resolve_chain(ctx.session, "plan"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add an AI text model in Settings → Integrations to get suggestions.")
    job = await enqueue(ctx.session, "brand.suggest", ctx.org_id, {"kind": body.kind}, created_by=ctx.user.id,
                        priority=1, max_attempts=1)
    await ctx.session.commit()
    return {"job_id": job.id}
