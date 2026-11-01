"""Adding a person emails them their sign-in details and a temporary password they must change on first sign-in."""

import uuid

from app.core.crypto import encrypt_json
from app.models import ProviderConfig
from app.services import notify
from tests.conftest import login
from tests.test_content import add, new_org


async def _roles(client, h):
    return {r["name"]: r["id"] for r in (await client.get("/roles", headers=h)).json()}


async def test_new_user_gets_email_with_temporary_password(client, monkeypatch):
    o = await new_org(client)
    roles = await _roles(client, o["h"])
    email = f"new-{uuid.uuid4().hex[:8]}@welcome-test.in"
    # without SMTP: nothing sent, the admin is told and still sees the password
    r = await client.post("/users", headers=o["h"], json={"email": email, "full_name": "Asha Rao", "role_ids": [roles["Viewer"]]})
    d = r.json()
    assert r.status_code == 201 and d["email_sent"] is False and "SMTP" in d["email_note"] and d["temporary_password"]

    sent = []

    async def fake_send(cfg, to, subject, body, link=None, *, attachments=None, html=None):
        sent.append({"to": to, "subject": subject, "body": body, "html": html})

    monkeypatch.setattr(notify, "send_email", fake_send)
    await add(o["id"], ProviderConfig(organization_id=o["id"], kind="smtp", provider="smtp", name="Mail",
                                      secrets_enc=encrypt_json({"host": "smtp.test", "username": "u", "password": "p"}),
                                      settings={}, enabled=True))
    email2 = f"new-{uuid.uuid4().hex[:8]}@welcome-test.in"
    r = await client.post("/users", headers=o["h"], json={"email": email2, "full_name": "Ravi Kumar", "role_ids": [roles["Viewer"]]})
    d = r.json()
    assert d["email_sent"] is True and email2 in d["email_note"]
    (msg,) = sent
    temp = d["temporary_password"]
    assert msg["to"] == email2 and temp in msg["body"] and temp in msg["html"] and "choose your own password" in msg["body"]
    assert "Hi Ravi" in msg["body"]
    # the temporary password works once and forces a new password
    tokens = await login(client, email2, temp)
    me = (await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})).json()
    assert (me.get("user") or me)["must_change_password"] is True

    # resetting a password emails the new temporary one
    uid = d["member"]["user_id"]
    r = await client.post(f"/users/{uid}/reset-password", headers=o["h"])
    assert r.json()["email_sent"] is True and r.json()["temporary_password"] in sent[-1]["body"]
    assert "reset your password" in sent[-1]["body"]

    # opting out of the email
    r = await client.post("/users", headers=o["h"], json={"email": f"x-{uuid.uuid4().hex[:6]}@welcome-test.in", "full_name": "No Mail",
                                                         "role_ids": [roles["Viewer"]], "send_email": False})
    assert r.json()["email_sent"] is False and len(sent) == 2
