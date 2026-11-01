"""PDF reports for every area: the catalog (what this person may export) and the export itself."""

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.deps import Context, require
from app.services import audit
from app.services import reports as rep
from app.services.analytics_reports import ReportError

log = logging.getLogger(__name__)
router = APIRouter(tags=["reports"])
GROUPS = ["Overview", "Content", "Growth", "Settings"]


@router.get("/reports/catalog")
async def report_catalog(ctx: Context = Depends(require("content.view"))):
    items = [{"kind": s.kind, "title": s.title, "description": s.description, "group": s.group, "period": s.period,
              "needs_id": s.needs_id}
             for s in rep.catalog().values() if s.permission in ctx.permissions]
    items.sort(key=lambda x: (GROUPS.index(x["group"]) if x["group"] in GROUPS else 9, x["title"]))
    return {"items": items}


class ExportIn(BaseModel):
    kind: str = Field(max_length=40)
    params: dict = Field(default_factory=dict)


@router.post("/reports/pdf")
async def export_pdf(body: ExportIn, ctx: Context = Depends(require("content.view"))):
    spec = rep.catalog().get(body.kind)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown report.")
    if spec.permission not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this report.")
    params = {k: v for k, v in body.params.items() if isinstance(k, str) and not k.startswith("_") and v not in (None, "")}
    params["_permissions"] = sorted(ctx.permissions)
    try:
        name, pdf = await rep.generate(ctx.session, ctx.org, body.kind, params)
    except rep.ReportInputError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except ReportError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Couldn't make the PDF: {e}")
    audit.record(ctx.session, action="report.exported", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="report", summary=f"Exported “{spec.title}” as PDF", ip=ctx.ip)
    await ctx.session.commit()
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store"})
