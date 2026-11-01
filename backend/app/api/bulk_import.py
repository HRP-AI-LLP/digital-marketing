"""Bulk import of posts from a CSV file: template, preview (row-level checks) and commit."""

from __future__ import annotations

import asyncio
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.media import _parse_multipart, _read_body
from app.core.deps import Context, require
from app.services import audit
from app.services import bulk_import as svc
from app.services.platforms import PLATFORMS

router = APIRouter(prefix="/content/import", tags=["content"])


async def _file(request: Request) -> bytes:
    ctype = request.headers.get("content-type", "")
    body = await _read_body(request, svc.MAX_FILE_BYTES + 64 * 1024)
    if ctype.startswith("multipart/"):
        files = await asyncio.to_thread(_parse_multipart, body, ctype)
        if not files:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a CSV file to upload.")
        name, data = files[0]
        if not name.lower().endswith((".csv", ".txt", ".tsv")):
            if name.lower().endswith((".xlsx", ".xls")):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Excel files can't be read directly. In Excel choose "
                                    "File → Save As → CSV UTF-8, then upload that file.")
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a .csv file.")
        return data
    return body


@router.get("/template")
async def template(ctx: Context = Depends(require("content.create"))):
    """The CSV template, using one of the organization's real account names in the examples when there is one."""
    lk = await svc.load_lookups(ctx.session, ctx.org)
    text = svc.template_csv()
    if lk.accounts:
        rows = list(csv.reader(io.StringIO(text)))
        for i, r in enumerate(rows[1:]):
            acc = lk.accounts[min(i, len(lk.accounts) - 1)]
            r[0] = acc.name
            supported = PLATFORMS[acc.platform].post_types if acc.platform in PLATFORMS else ("text",)
            if r[8] not in supported:
                r[8] = "image" if "image" in supported else supported[0]
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        text = buf.getvalue()
    return {
        "filename": "posts-import-template.csv", "csv": text, "columns": svc.COLUMNS,
        "accounts": [{"name": a.name, "platform": a.platform, "handle": a.handle} for a in lk.accounts],
        "max_rows": svc.MAX_ROWS,
    }


@router.post("/preview")
async def preview(request: Request, ctx: Context = Depends(require("content.create"))):
    data = await _file(request)
    try:
        rows, lk = await svc.check_all(ctx.session, ctx.org, data)
    except svc.ImportError_ as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    out = svc.summary(rows, lk.tz)
    out["auto_approve"] = lk.auto_approve
    return out


@router.post("/commit")
async def commit(request: Request, skip_invalid: bool = Query(False), ctx: Context = Depends(require("content.create"))):
    data = await _file(request)
    try:
        rows, lk = await svc.check_all(ctx.session, ctx.org, data)
        if any(r.image_urls for r in rows if not r.errors) and "media.manage" not in ctx.permissions:
            raise svc.ImportError_("Rows with image URLs add files to the media library, which needs the "
                                   "'media.manage' permission. Remove the image URLs or ask an administrator.")
        res = await svc.commit(ctx.session, ctx.org, ctx.user, rows, lk, skip_invalid=skip_invalid)
    except svc.ImportError_ as e:
        await ctx.session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    except Exception:
        await ctx.session.rollback()
        raise
    audit.record(ctx.session, action="post.imported", organization_id=ctx.org_id, actor=ctx.user, entity_type="post",
                 summary=f"Imported {len(res.created)} post(s) from a CSV file", ip=ctx.ip)
    await ctx.session.commit()
    await svc.notify_imported(ctx.org_id, res.waiting)
    return {
        "created": len(res.created), "post_ids": [str(p.id) for p in res.created], "skipped_rows": [r.n for r in res.skipped],
        "waiting": res.waiting, "scheduled": res.scheduled, "drafts": res.drafts,
        "message": (f"Imported {len(res.created)} post(s): {res.drafts} draft(s), {res.waiting} waiting for approval, "
                    f"{res.scheduled} scheduled." + (f" Skipped {len(res.skipped)} row(s) with problems." if res.skipped else "")),
    }
