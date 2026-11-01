"""Email from anywhere: who can send (senders), default recipients, test emails, and sending a report or an
exported file (CSV) as an attachment."""

import base64
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.deps import Context, require
from app.models import Organization
from app.services import audit, mailer
from app.services import reports as rep
from app.services.analytics_reports import ReportError

router = APIRouter(tags=["email"])
MAX_FILE_BYTES = 15 * 1024 * 1024
FILE_TYPES = {"text/csv", "application/pdf", "application/json", "text/plain",
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


@router.get("/email/senders")
async def list_senders(ctx: Context = Depends(require("content.view"))):
    cfg = mailer.email_settings(ctx.org)
    default = await mailer.default_sender(ctx.session, ctx.org)
    rows = await mailer.senders(ctx.session)
    return {"senders": [mailer.sender_out(c, default.id if default else None) for c in rows],
            "report_recipients": cfg["report_recipients"], "report_cc": cfg["report_cc"],
            "ready": default is not None}


class EmailSettingsIn(BaseModel):
    default_sender_id: str | None = None
    report_recipients: list[str] | str | None = None
    report_cc: list[str] | str | None = None


@router.patch("/email/settings")
async def update_email_settings(body: EmailSettingsIn, ctx: Context = Depends(require("org.settings.manage"))):
    org = await ctx.session.get(Organization, ctx.org_id)
    cur = mailer.email_settings(org)
    data = body.model_dump(exclude_unset=True)
    try:
        if "report_recipients" in data:
            cur["report_recipients"] = mailer.parse_addresses(data["report_recipients"])
        if "report_cc" in data:
            cur["report_cc"] = mailer.parse_addresses(data["report_cc"])
        if "default_sender_id" in data:
            cur["default_sender_id"] = (str((await mailer.pick_sender(ctx.session, org, data["default_sender_id"])).id)
                                        if data["default_sender_id"] else None)
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    org.publishing = {**(org.publishing or {}), "email": cur}
    audit.record(ctx.session, action="org.updated", organization_id=org.id, actor=ctx.user, entity_type="organization",
                 entity_id=org.id, summary="Updated email settings (sender and report recipients)", ip=ctx.ip)
    await ctx.session.commit()
    return await list_senders(ctx)


class TestIn(BaseModel):
    sender_id: str | None = None
    to: str | None = None


@router.post("/email/test")
async def send_test(body: TestIn, ctx: Context = Depends(require("integration.manage"))):
    from app.services.notify import send_email

    try:
        cfg = await mailer.pick_sender(ctx.session, ctx.org, body.sender_id)
        to = mailer.parse_addresses(body.to or ctx.user.email) or [ctx.user.email]
        await send_email(cfg, to, "Test email from HRP Digital Marketing Studio",
                         f"This is a test from the “{cfg.name}” sender. If you can read this, email works.")
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, mailer.explain(e))
    return {"message": f"Test email sent to {', '.join(to)}. Check the inbox (and the spam folder)."}


class SendBase(BaseModel):
    to: list[str] | str
    cc: list[str] | str | None = None
    sender_id: str | None = None
    subject: str | None = Field(None, max_length=200)
    message: str | None = Field(None, max_length=5000)


class SendReportIn(SendBase):
    kind: str = Field(max_length=40)
    params: dict = Field(default_factory=dict)


@router.post("/email/report")
async def email_report(body: SendReportIn, ctx: Context = Depends(require("content.view"))):
    spec = rep.catalog().get(body.kind)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown report.")
    if spec.permission not in ctx.permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this report.")
    params = {k: v for k, v in body.params.items() if isinstance(k, str) and not k.startswith("_") and v not in (None, "")}
    params["_permissions"] = sorted(ctx.permissions)
    try:
        mailer.parse_addresses(body.to)  # fail fast before making the PDF
        name, pdf = await rep.generate(ctx.session, ctx.org, body.kind, params)
        msg = await mailer.send_file(ctx.session, ctx.org, sender_id=body.sender_id, to=body.to, cc=body.cc,
                                     subject=body.subject or f"{spec.title} – {ctx.org.name}", message=body.message or "",
                                     filename=name, data=pdf, mime="application/pdf", sent_by=ctx.user.full_name or ctx.user.email)
    except (mailer.MailError, rep.ReportInputError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except ReportError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Couldn't make the PDF: {e}")
    audit.record(ctx.session, action="report.emailed", organization_id=ctx.org_id, actor=ctx.user, entity_type="report",
                 summary=f"Emailed “{spec.title}”: {msg}", ip=ctx.ip)
    await ctx.session.commit()
    return {"message": msg}


class SendFileIn(SendBase):
    filename: str = Field(max_length=200)
    mime: str = Field("text/csv", max_length=100)
    content: str | None = None          # text content (CSV)
    content_b64: str | None = None      # or base64 for binary files


@router.post("/email/file")
async def email_file(body: SendFileIn, ctx: Context = Depends(require("content.view"))):
    mime = body.mime.split(";")[0].strip().lower()
    if mime not in FILE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This kind of file can't be emailed from here.")
    try:
        data = base64.b64decode(body.content_b64, validate=True) if body.content_b64 is not None else (body.content or "").encode("utf-8")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file couldn't be read.")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is too large to email (15 MB at most).")
    name = re.sub(r"[^\w.\- ]+", "_", body.filename).strip() or "export.csv"
    try:
        msg = await mailer.send_file(ctx.session, ctx.org, sender_id=body.sender_id, to=body.to, cc=body.cc,
                                     subject=body.subject or f"{name} – {ctx.org.name}", message=body.message or "",
                                     filename=name, data=data, mime=mime, sent_by=ctx.user.full_name or ctx.user.email)
    except mailer.MailError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    audit.record(ctx.session, action="file.emailed", organization_id=ctx.org_id, actor=ctx.user, entity_type="file",
                 summary=f"Emailed {name}: {msg}", ip=ctx.ip)
    await ctx.session.commit()
    return {"message": msg}
