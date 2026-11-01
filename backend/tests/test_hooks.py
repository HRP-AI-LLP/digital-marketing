"""Connections: outgoing webhooks (signature, SSRF protection, retries with backoff, auto-disable, emit never
raises) and personal API tokens (scopes, forbidden endpoints, revoke, rate limit). No real network: the webhook
HTTP client is an httpx.MockTransport and name resolution is faked."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant
from app.models import AuditLog, Job, Notification, Webhook, WebhookDelivery
import app.modules.webhooks  # noqa: F401  (registers the webhook.deliver handler)
from app.services import api_tokens
from app.services import webhooks as wh
from app.services.jobs import run_inline
from tests.conftest import auth, login
from tests.test_content import mk_account, mk_post, new_org

PUBLIC_IP = "93.184.216.34"


# ------------------------------------------------------------------ fixtures
class Receiver:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.calls.append(request)
        if self.status == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(self.status, json={"ok": self.status < 300})


@pytest.fixture
def receiver(monkeypatch):
    r = Receiver()
    monkeypatch.setattr(wh, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(r.handler)))
    dns = {"hooks.example.com": [PUBLIC_IP], "internal.example.com": ["10.1.2.3"],
           "sneaky.example.com": [PUBLIC_IP, "192.168.1.20"], "meta.example.com": ["169.254.169.254"],
           "v6.example.com": ["::ffff:172.16.0.9"], "localhost": ["127.0.0.1", "::1"]}

    async def fake_resolve(host, port):
        if host not in dns:
            raise OSError("no such host")
        return dns[host]

    monkeypatch.setattr(wh, "resolve", fake_resolve)
    r.dns = dns
    return r


async def _hook(client, o, events=("post.published",), url="https://hooks.example.com/in") -> dict:
    r = await client.post("/connections/webhooks", headers=o["h"],
                          json={"name": "Zapier", "url": url, "events": list(events)})
    assert r.status_code == 201, r.text
    return r.json()


async def _queued(org_id, kind="webhook.deliver") -> list[Job]:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return list((await s.scalars(select(Job).where(Job.kind == kind, Job.status == "queued")
                                     .order_by(Job.created_at))).all())


async def _run_queued(org_id) -> list[dict]:
    """Run every queued delivery job once (like the worker would), marking each done."""
    out = []
    for j in await _queued(org_id):
        async with SessionLocal() as s:
            set_tenant(s, org_id)
            job = await s.get(Job, j.id)
            job.status = "done"
            await s.commit()
        out.append(await run_inline("webhook.deliver", org_id, j.payload))
    return out


async def _deliveries(org_id, hook_id) -> list[WebhookDelivery]:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return list((await s.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == uuid.UUID(str(hook_id)))
                                     .order_by(WebhookDelivery.created_at))).all())


async def _webhook(org_id, hook_id) -> Webhook:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return await s.get(Webhook, uuid.UUID(str(hook_id)))


# ------------------------------------------------------------------ webhooks
async def test_signature_headers_and_body(client, receiver):
    o = await new_org(client)
    h = await _hook(client, o)
    assert h["secret"].startswith("whsec_")
    listed = (await client.get("/connections/webhooks", headers=o["h"])).json()["items"]
    assert listed[0]["id"] == h["id"] and "secret" not in listed[0] and "secret_enc" not in json.dumps(listed)

    n = await wh.emit(o["id"], "post.published", {"id": "p1", "text": "Hello"})
    assert n == 1
    assert await wh.emit(o["id"], "post.failed", {"id": "p1"}) == 0  # not subscribed
    [res] = await _run_queued(o["id"])
    assert res["ok"] is True
    [req] = receiver.calls
    body = req.content
    assert req.headers["X-HRP-Event"] == "post.published"
    assert req.headers["X-HRP-Signature"] == "sha256=" + __import__("hmac").new(
        h["secret"].encode(), body, "sha256").hexdigest()
    assert wh.verify(h["secret"], body, req.headers["X-HRP-Signature"])
    assert not wh.verify("wrong", body, req.headers["X-HRP-Signature"])
    data = json.loads(body)
    assert data["event"] == "post.published" and data["data"] == {"id": "p1", "text": "Hello"}
    assert data["organization"]["id"] == str(o["id"]) and data["organization"]["name"].startswith("Org ")
    assert uuid.UUID(data["id"]) and data["created_at"]
    [d] = await _deliveries(o["id"], h["id"])
    assert str(d.id) == req.headers["X-HRP-Delivery"] and d.ok and d.status_code == 200 and d.attempt == 1
    hook = await _webhook(o["id"], h["id"])
    assert hook.last_status == "ok" and hook.failure_count == 0

    # Test ping and redeliver go through the same path; deliveries are listed with their payload.
    r = await client.post(f"/connections/webhooks/{h['id']}/test", headers=o["h"])
    assert r.status_code == 200
    await _run_queued(o["id"])
    assert receiver.calls[-1].headers["X-HRP-Event"] == "test.ping"
    items = (await client.get(f"/connections/webhooks/{h['id']}/deliveries", headers=o["h"])).json()["items"]
    assert len(items) == 2 and items[0]["event"] == "test.ping" and items[1]["payload"]["data"]["text"] == "Hello"
    r = await client.post(f"/connections/webhooks/{h['id']}/deliveries/{items[1]['id']}/redeliver", headers=o["h"])
    assert r.status_code == 200
    await _run_queued(o["id"])
    again = json.loads(receiver.calls[-1].content)
    assert again["id"] == data["id"] and receiver.calls[-1].headers["X-HRP-Delivery"] != req.headers["X-HRP-Delivery"]

    # Rotating the secret signs with the new one; every change is in the activity log.
    new = (await client.post(f"/connections/webhooks/{h['id']}/rotate-secret", headers=o["h"])).json()["secret"]
    await wh.emit(o["id"], "post.published", {"id": "p2"})
    await _run_queued(o["id"])
    assert wh.verify(new, receiver.calls[-1].content, receiver.calls[-1].headers["X-HRP-Signature"])
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        actions = {a.action for a in (await s.scalars(select(AuditLog))).all()}
    assert {"webhook.created", "webhook.tested", "webhook.redelivered", "webhook.secret_rotated"} <= actions
    assert "whsec_" not in json.dumps([a for a in actions])


async def test_ssrf_and_address_rules(client, receiver, monkeypatch):
    o = await new_org(client)

    async def bad(url, events=("post.published",)):
        r = await client.post("/connections/webhooks", headers=o["h"], json={"name": "x", "url": url, "events": list(events)})
        assert r.status_code == 400, (url, r.text)
        return r.json()["detail"]

    assert "https" in await bad("http://hooks.example.com/in")
    assert "private" in await bad("https://internal.example.com/in")
    assert "private" in await bad("https://sneaky.example.com/in")  # one private answer is enough
    assert "private" in await bad("https://meta.example.com/latest")
    assert "private" in await bad("https://v6.example.com/in")  # IPv4-mapped IPv6
    for ip in ("10.0.0.5", "172.16.3.4", "192.168.0.10", "169.254.169.254", "100.64.0.1", "0.0.0.0", "[fd00::1]"):
        assert "private" in await bad(f"https://{ip}/in"), ip
    assert "localhost" in await bad("http://localhost:5678/webhook")
    assert "localhost" in await bad("https://[::1]/in")
    assert "find" in await bad("https://nowhere.example.com/in")
    assert "user name" in await bad("https://u:p@hooks.example.com/in")
    assert "event" in (await bad("https://hooks.example.com/in", events=["nope"])).lower()
    assert "at least one" in await bad("https://hooks.example.com/in", events=[])

    # Localhost testing switch: http://localhost allowed, other private addresses still refused.
    monkeypatch.setattr(get_settings(), "webhooks_allow_localhost", True)
    h = await _hook(client, o, url="http://localhost:5678/webhook")
    assert "private" in await bad("https://internal.example.com/in")
    receiver.dns["evil.example.com"] = ["127.0.0.1"]
    assert "private" in await bad("https://evil.example.com/in")  # loopback only for the name "localhost"
    monkeypatch.setattr(get_settings(), "webhooks_allow_localhost", False)

    # Checked again at delivery: switching the setting off (or DNS changing) stops delivery, without retries.
    await wh.emit(o["id"], "post.published", {"id": "p"})
    [res] = await _run_queued(o["id"])
    assert res["ok"] is False and "retry_in" not in res and not receiver.calls
    [d] = await _deliveries(o["id"], h["id"])
    assert "localhost" in d.error
    good = await _hook(client, o)
    receiver.dns["hooks.example.com"] = ["10.9.9.9"]
    await wh.emit(o["id"], "post.published", {"id": "p"})
    results = await _run_queued(o["id"])
    assert all(r["ok"] is False for r in results) and not receiver.calls
    assert "private" in (await _deliveries(o["id"], good["id"]))[0].error


async def test_retries_backoff_and_auto_disable(client, receiver):
    o = await new_org(client)
    h = await _hook(client, o, events=["post.failed", "post.published"])
    receiver.status = 500
    await wh.emit(o["id"], "post.failed", {"id": "p"})
    delays = []
    for attempt in range(1, wh.MAX_ATTEMPTS + 1):
        jobs = await _queued(o["id"])
        assert len(jobs) == 1 and jobs[0].payload["attempt"] == attempt
        if attempt > 1:
            delays.append((jobs[0].run_after - datetime.now(UTC)).total_seconds())
        [res] = await _run_queued(o["id"])
        assert res["ok"] is False and res["attempt"] == attempt
    assert await _queued(o["id"]) == []  # gave up after 5 attempts
    for got, want in zip(delays, wh.BACKOFF_SECONDS):
        assert want - 30 < got <= want + 1
    assert wh.BACKOFF_SECONDS == sorted(wh.BACKOFF_SECONDS)
    ds = await _deliveries(o["id"], h["id"])
    assert [d.attempt for d in ds] == [1, 2, 3, 4, 5] and all(d.status_code == 500 and not d.ok for d in ds)
    assert len({d.event_id for d in ds}) == 1
    hook = await _webhook(o["id"], h["id"])
    assert hook.failure_count == 5 and hook.last_status == "failed" and hook.active

    # Timeouts retry too; a 4xx (other than 408/429) does not.
    receiver.status = "timeout"
    await wh.emit(o["id"], "post.published", {"id": "p"})
    [res] = await _run_queued(o["id"])
    assert res["retry_in"] == wh.BACKOFF_SECONDS[0] and "10 seconds" in res["error"]
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        for j in (await s.scalars(select(Job).where(Job.kind == "webhook.deliver", Job.status == "queued"))).all():
            j.status = "cancelled"
        await s.commit()
    receiver.status = 404
    await wh.emit(o["id"], "post.published", {"id": "p"})
    [res] = await _run_queued(o["id"])
    assert "retry_in" not in res and await _queued(o["id"]) == []

    # A success resets the count.
    receiver.status = 200
    await wh.emit(o["id"], "post.published", {"id": "p"})
    await _run_queued(o["id"])
    assert (await _webhook(o["id"], h["id"])).failure_count == 0

    # 20 failures in a row turn it off and tell the admins.
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        (await s.get(Webhook, uuid.UUID(h["id"]))).failure_count = wh.DISABLE_AFTER - 1
        await s.commit()
    receiver.status = 503
    await wh.emit(o["id"], "post.published", {"id": "p"})
    [res] = await _run_queued(o["id"])
    assert res.get("disabled") is True and "retry_in" not in res
    hook = await _webhook(o["id"], h["id"])
    assert hook.active is False and "20 failed" in hook.disabled_reason
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        notes = (await s.scalars(select(Notification).where(Notification.event == "webhook_disabled"))).all()
    assert notes and "Zapier" in notes[0].title
    assert await wh.emit(o["id"], "post.published", {"id": "p"}) == 0  # turned-off webhooks get nothing
    # Turning it back on resets the counter.
    r = await client.patch(f"/connections/webhooks/{h['id']}", headers=o["h"], json={"active": True})
    assert r.status_code == 200 and r.json()["failure_count"] == 0 and r.json()["active"] is True
    assert r.json()["deliveries_30d"] >= 9 and r.json()["success_rate"] is not None


async def test_emit_never_raises(client, receiver, monkeypatch):
    o = await new_org(client)
    await _hook(client, o)
    assert await wh.emit(None, "post.published", {}) == 0
    assert await wh.emit(o["id"], "no.such.event", {}) == 0
    assert await wh.emit(o["id"], "post.published", lambda: 1 / 0) == 0  # data builder fails

    async def boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(wh, "_enqueue_for", boom)
    assert await wh.emit(o["id"], "post.published", {"id": "x"}) == 0
    async with SessionLocal() as s:  # inside a caller's transaction: the caller's work survives
        set_tenant(s, o["id"])
        acc = await mk_account(o["id"])
        assert await wh.emit(o["id"], "post.published", {"id": "x"}, session=s) == 0
        assert (await s.scalars(select(Webhook))).all()
    monkeypatch.undo()
    # Unserialisable data is made JSON-safe.
    monkeypatch.setattr(wh, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(receiver.handler)))
    monkeypatch.setattr(wh, "resolve", lambda h, p: _ip())
    assert await wh.emit(o["id"], "post.published", {"when": datetime.now(UTC), "id": uuid.uuid4(), "obj": object()}) == 1
    [res] = await _run_queued(o["id"])
    assert res["ok"]
    assert acc


async def _ip():
    return [PUBLIC_IP]


async def test_post_events_are_emitted(client, receiver):
    o = await new_org(client)
    await _hook(client, o, events=["post.approved", "post.rejected", "post.needs_approval"])
    acc = await mk_account(o["id"])
    p = await mk_post(o["id"], acc, status="in_review", text="Review me")
    r = await client.post(f"/content/posts/{p.id}/approve", headers=o["h"])
    assert r.status_code == 200, r.text
    p2 = await mk_post(o["id"], acc, status="in_review", text="Not this one")
    r = await client.post(f"/content/posts/{p2.id}/reject", headers=o["h"], json={"reason": "Off-brand"})
    assert r.status_code == 200, r.text
    r = await client.post("/content/posts", headers=o["h"], json={
        "account_id": str(acc.id), "text": "Handwritten", "status": "in_review",
        "scheduled_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()})
    assert r.status_code == 201, r.text
    await _run_queued(o["id"])
    got = {json.loads(c.content)["event"]: json.loads(c.content)["data"] for c in receiver.calls}
    assert got["post.approved"]["id"] == str(p.id) and got["post.approved"]["status"] == "scheduled"
    assert got["post.approved"]["by"] == o["email"]
    assert got["post.rejected"]["reason"] == "Off-brand"
    assert got["post.needs_approval"]["text"] == "Handwritten"


# ------------------------------------------------------------------ API tokens
async def _token(client, o, scopes, **kw) -> dict:
    r = await client.post("/connections/tokens", headers=o["h"], json={"name": "n8n", "scopes": scopes, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(t: dict) -> dict:
    return {"Authorization": f"Bearer {t['token']}"}


async def test_token_scopes_and_forbidden_endpoints(client):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    await mk_post(o["id"], acc, status="draft", text="Hi")
    t = await _token(client, o, ["content.view"])
    assert t["token"].startswith(f"hrp_{t['prefix']}_") and len(t["token"]) > 40
    listed = (await client.get("/connections/tokens", headers=o["h"])).json()["items"]
    assert listed[0]["hint"] == f"hrp_{t['prefix']}_…" and "token" not in listed[0] and "token_hash" not in listed[0]

    r = await client.get("/content/posts", headers=bearer(t))
    assert r.status_code == 200, r.text
    r = await client.post("/content/posts", headers=bearer(t), json={"account_id": str(acc.id), "text": "x"})
    assert r.status_code == 403 and "content.create" in r.json()["detail"]
    assert (await client.get("/connections/webhooks", headers=bearer(t))).status_code == 403

    for path in ("/auth/me", "/users", "/roles", "/permissions", "/connections/tokens", "/platform/organizations"):
        r = await client.get(path, headers=bearer(t))
        assert r.status_code == 403, (path, r.status_code)
    r = await client.post("/connections/tokens", headers=bearer(t), json={"name": "x", "scopes": ["content.view"]})
    assert r.status_code == 403

    # A token with more scopes can drive content.
    t2 = await _token(client, o, ["content.view", "content.create", "content.approve", "content.publish"], expires_in_days=7)
    r = await client.post("/content/posts", headers=bearer(t2), json={"account_id": str(acc.id), "text": "From n8n"})
    assert r.status_code == 201, r.text
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        audits = (await s.scalars(select(AuditLog).where(AuditLog.action == "api_token.created"))).all()
    assert len(audits) == 2 and all(t["token"] not in json.dumps(a.details) for a in audits)

    # Junk and look-alike tokens are refused.
    for bad in ("hrp_", "hrp_abcd_x", f"hrp_{t['prefix']}_wrong", t["token"] + "x"):
        assert (await client.get("/content/posts", headers={"Authorization": f"Bearer {bad}"})).status_code == 401


async def test_token_scopes_limited_to_own_permissions(client):
    o = await new_org(client)
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=o["h"])).json()}
    email = f"rev-{uuid.uuid4().hex[:8]}@content-test.in"
    r = await client.post("/users", headers=o["h"], json={"email": email, "full_name": "Rev", "role_ids": [roles["Reviewer"]]})
    assert r.status_code == 201, r.text
    rev = await login(client, email, r.json()["temporary_password"])
    # Reviewers can't create tokens by default.
    r = await client.post("/connections/tokens", headers=auth(rev), json={"name": "x", "scopes": ["content.view"]})
    assert r.status_code == 403
    # Give the reviewer role the permission; they still can't grant what they don't have.
    rid = roles["Reviewer"]
    role = next(x for x in (await client.get("/roles", headers=o["h"])).json() if x["id"] == rid)
    r = await client.patch(f"/roles/{rid}", headers=o["h"], json={"permissions": role["permissions"] + ["apitoken.create"]})
    assert r.status_code == 200, r.text
    r = await client.post("/connections/tokens", headers=auth(rev), json={"name": "x", "scopes": ["content.create"]})
    assert r.status_code == 400 and "content.create" in r.json()["detail"]
    r = await client.post("/connections/tokens", headers=auth(rev), json={"name": "x", "scopes": ["user.manage"]})
    assert r.status_code == 400
    scopes = {s["code"] for s in (await client.get("/connections/scopes", headers=auth(rev))).json()["items"]}
    assert "content.approve" in scopes and "content.create" not in scopes and "apitoken.create" not in scopes
    tok = (await client.post("/connections/tokens", headers=auth(rev),
                             json={"name": "Zap", "scopes": ["content.view", "content.approve"]})).json()
    assert (await client.get("/content/posts", headers=bearer(tok))).status_code == 200
    # Losing a permission removes it from the token too.
    r = await client.patch(f"/roles/{rid}", headers=o["h"],
                           json={"permissions": [p for p in role["permissions"] if p != "content.view"] + ["apitoken.create"]})
    assert r.status_code == 200
    assert (await client.get("/content/posts", headers=bearer(tok))).status_code == 403
    # The reviewer sees only their own tokens; the admin sees all and can revoke any.
    mine = (await client.get("/connections/tokens", headers=auth(rev))).json()["items"]
    assert [x["id"] for x in mine] == [tok["id"]]
    await _token(client, o, ["content.view"])
    assert len((await client.get("/connections/tokens", headers=o["h"])).json()["items"]) == 2
    assert (await client.delete(f"/connections/tokens/{tok['id']}", headers=o["h"])).status_code == 204
    assert (await client.get("/analytics/reports", headers=bearer(tok))).status_code == 401


async def test_revoke_expiry_and_isolation(client):
    o = await new_org(client)
    other = await new_org(client)
    t = await _token(client, o, ["content.view", "connections.view"])
    assert (await client.get("/connections/webhooks", headers=bearer(t))).status_code == 200
    # Another organization can't revoke it.
    assert (await client.delete(f"/connections/tokens/{t['id']}", headers=other["h"])).status_code == 404
    r = await client.delete(f"/connections/tokens/{t['id']}", headers=o["h"])
    assert r.status_code == 204
    r = await client.get("/content/posts", headers=bearer(t))
    assert r.status_code == 401 and "revoked" in r.json()["detail"]
    items = (await client.get("/connections/tokens", headers=o["h"])).json()["items"]
    assert items[0]["state"] == "revoked"
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        assert (await s.scalars(select(AuditLog).where(AuditLog.action == "api_token.revoked"))).all()

    # Expired tokens stop working; deactivated people's tokens stop working.
    t2 = await _token(client, o, ["content.view"])
    from app.models import ApiToken

    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        (await s.get(ApiToken, uuid.UUID(t2["id"]))).expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    assert (await client.get("/content/posts", headers=bearer(t2))).status_code == 401
    # The token works only in its own organization (it can't see the other org's data).
    t3 = await _token(client, other, ["content.view"])
    acc = await mk_account(o["id"])
    p = await mk_post(o["id"], acc, status="draft", text="Secret plan")
    r = await client.get(f"/content/posts/{p.id}", headers=bearer(t3))
    assert r.status_code == 404
    assert (await client.get("/content/posts", headers=bearer(t3))).status_code == 200


async def test_rate_limit(client, monkeypatch):
    o = await new_org(client)
    t = await _token(client, o, ["content.view"])
    monkeypatch.setattr(api_tokens, "RATE_LIMIT", 3)
    api_tokens.reset_rate_limits()
    for _ in range(3):
        assert (await client.get("/content/posts", headers=bearer(t))).status_code == 200
    r = await client.get("/content/posts", headers=bearer(t))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    # Another token has its own allowance; the window moves on.
    t2 = await _token(client, o, ["content.view"])
    assert (await client.get("/content/posts", headers=bearer(t2))).status_code == 200
    assert api_tokens.rate_limited("zzzz", now=0.0) is None
    for i in range(2):
        api_tokens.rate_limited("zzzz", now=1.0 + i)
    assert api_tokens.rate_limited("zzzz", now=5.0) is not None
    assert api_tokens.rate_limited("zzzz", now=62.0) is None
    api_tokens.reset_rate_limits()


async def test_tokens_through_tunnel_only_when_enabled(client, monkeypatch):
    o = await new_org(client)
    t = await _token(client, o, ["content.view"])
    tunnel = {"cf-connecting-ip": "203.0.113.9", "cf-ray": "abc-BOM"}
    assert (await client.get("/content/posts", headers={**bearer(t), **tunnel})).status_code == 404
    monkeypatch.setattr(get_settings(), "api_tokens_via_tunnel", True)
    assert (await client.get("/content/posts", headers={**bearer(t), **tunnel})).status_code == 200
    assert (await client.get("/users", headers={**bearer(t), **tunnel})).status_code == 403
    # Normal sign-in tokens are still refused through the tunnel.
    assert (await client.get("/content/posts", headers={**o["h"], **tunnel})).status_code == 404


async def test_connections_report(client, receiver):
    from app.models import Organization
    from app.services import reports as rep

    o = await new_org(client)
    h = await _hook(client, o)
    await wh.emit(o["id"], "post.published", {"id": "p"})
    await _run_queued(o["id"])
    await _token(client, o, ["content.view"])
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        _, doc, _ = await rep.build(s, org, "connections", {})
    dump = json.dumps(doc.blocks, default=str)
    assert "Zapier" in dump and "100%" in dump and h["secret"] not in dump and "whsec_" not in dump
