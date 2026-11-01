"""Email: senders (SMTP mailboxes), default recipients, sending reports and exported files."""

import base64

from app.core.crypto import encrypt_json
from app.models import ProviderConfig
from app.services import notify
from app.services import reports as rep
from tests.test_content import add, new_org


async def test_smtp_security_modes():
    assert notify.smtp_security({"port": 465}) == "ssl"
    assert notify.smtp_security({"port": 587}) == "starttls"
    assert notify.smtp_security({"port": "465", "security": "SSL"}) == "ssl"
    assert notify.smtp_security({"port": 25, "use_tls": "false"}) == "none"
    assert notify.smtp_security({"port": 587, "security": "none"}) == "none"
    assert notify.sender_address({"from_email": "info@hrpai.in", "from_name": "HRP AI LLP"}) == "HRP AI LLP <info@hrpai.in>"


async def test_send_reports_and_files(client, monkeypatch):
    o = await new_org(client)
    r = await client.get("/email/senders", headers=o["h"])
    assert r.json()["ready"] is False
    r = await client.post("/email/file", headers=o["h"], json={"to": "a@b.in", "filename": "x.csv", "content": "a,b"})
    assert r.status_code == 400 and "SMTP" in r.json()["detail"]

    sent = []

    async def fake_send(cfg, to, subject, body, link=None, *, attachments=None, html=None, cc=None):
        sent.append({"cfg": cfg.name, "to": to, "cc": cc, "subject": subject, "files": [(a[0], a[2], len(a[1])) for a in attachments or []]})

    monkeypatch.setattr(notify, "send_email", fake_send)

    async def fake_pdf(html, title):
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(rep, "render_pdf", fake_pdf)
    for name, frm in (("GoDaddy info", "info@hrpai.in"), ("Reports box", "reports@hrpai.in")):
        await add(o["id"], ProviderConfig(organization_id=o["id"], kind="smtp", provider="smtp", name=name, settings={}, enabled=True,
                                          secrets_enc=encrypt_json({"host": "smtpout.secureserver.net", "port": "465",
                                                                    "username": frm, "password": "x", "from_email": frm,
                                                                    "from_name": "HRP AI LLP"})))
    d = (await client.get("/email/senders", headers=o["h"])).json()
    assert d["ready"] and [s["from_email"] for s in d["senders"]] == ["info@hrpai.in", "reports@hrpai.in"] and d["senders"][0]["default"]
    second = d["senders"][1]["id"]
    r = await client.patch("/email/settings", headers=o["h"], json={"default_sender_id": second,
                                                                   "report_recipients": "ceo@hrpai.in, Team@HRPAI.in"})
    d = r.json()
    assert d["report_recipients"] == ["ceo@hrpai.in", "team@hrpai.in"] and d["senders"][1]["default"]
    assert (await client.patch("/email/settings", headers=o["h"], json={"report_recipients": "not-an-email"})).status_code == 400

    r = await client.post("/email/report", headers=o["h"], json={"kind": "dashboard", "to": ["ceo@hrpai.in"], "cc": "x@hrpai.in",
                                                                "message": "Weekly numbers"})
    assert r.status_code == 200, r.text
    m = sent[-1]
    assert m["cfg"] == "Reports box" and m["to"] == ["ceo@hrpai.in"] and m["cc"] == ["x@hrpai.in"]
    assert m["files"][0][1] == "application/pdf" and m["files"][0][0].startswith("overview-report")

    csv = "keyword,position\nhrms,3\n"
    r = await client.post("/email/file", headers=o["h"], json={"to": "ceo@hrpai.in", "filename": "rankings.csv", "content": csv,
                                                              "sender_id": d["senders"][0]["id"]})
    assert r.status_code == 200 and sent[-1]["cfg"] == "GoDaddy info" and sent[-1]["files"] == [("rankings.csv", "text/csv", len(csv))]
    r = await client.post("/email/file", headers=o["h"], json={"to": "ceo@hrpai.in", "filename": "a.exe", "mime": "application/x-msdownload",
                                                              "content_b64": base64.b64encode(b"MZ").decode()})
    assert r.status_code == 400
    r = await client.post("/email/report", headers=o["h"], json={"kind": "dashboard", "to": "bad address"})
    assert r.status_code == 400
