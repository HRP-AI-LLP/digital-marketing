"""Integrations, notifications, dashboard and brand look."""

import pytest

from app.services.llm import router as llm_router
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"


@pytest.fixture
async def a(client, admin):
    return await login(client, "admin@hrpai.in", ADMIN_PW)


async def test_provider_crud_masks_secrets(client, a):
    r = await client.post("/integrations/providers", headers=auth(a), json={
        "provider": "groq", "secrets": {"api_key": "gsk_supersecret1234"}, "settings": {"default_model": "llama-3.1-8b-instant"}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "supersecret" not in r.text and body["secrets"]["api_key"].endswith("1234")
    pid = body["id"]
    # Saving the masked value back keeps the original secret.
    r = await client.patch(f"/integrations/providers/{pid}", headers=auth(a), json={"secrets": {"api_key": body["secrets"]["api_key"]}})
    assert r.status_code == 200
    from app.core.crypto import decrypt_json
    from app.core.db import SessionLocal, set_tenant
    from app.models import ProviderConfig

    async with SessionLocal() as s:
        set_tenant(s, a["organization"]["id"])
        cfg = await s.get(ProviderConfig, pid)
        assert decrypt_json(cfg.secrets_enc)["api_key"] == "gsk_supersecret1234"
    assert (await client.delete(f"/integrations/providers/{pid}", headers=auth(a))).status_code == 204


async def test_provider_requires_key(client, a):
    r = await client.post("/integrations/providers", headers=auth(a), json={"provider": "openai", "secrets": {}})
    assert r.status_code == 400 and "API key" in r.json()["detail"]


async def test_routes_validation(client, a):
    r = await client.put("/integrations/routes", headers=auth(a), json={"routes": {"nonsense": []}})
    assert r.status_code == 400
    r = await client.post("/integrations/providers", headers=auth(a), json={"provider": "ollama", "settings": {"default_model": "m"}})
    pid = r.json()["id"]
    r = await client.put("/integrations/routes", headers=auth(a), json={"routes": {"write": [{"provider_id": pid, "model": "m2"}]}})
    assert r.status_code == 200 and r.json()["routes"]["write"][0]["model"] == "m2"
    await client.delete(f"/integrations/providers/{pid}", headers=auth(a))


async def test_router_falls_back_and_logs_usage(client, a, monkeypatch):
    p1 = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "ollama", "name": "first", "settings": {"default_model": "bad"}})).json()["id"]
    p2 = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "lmstudio", "name": "second", "settings": {"default_model": "good"}})).json()["id"]
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"write": [
        {"provider_id": p1, "model": "bad"}, {"provider_id": p2, "model": "good"}]}})

    from app.services.llm import client as llm

    async def fake_chat(conn, model, messages, **kw):
        if model == "bad":
            raise llm.ProviderError("boom")
        return llm.ChatResult("hello", 5, 3, 10, model)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert r.status_code == 200 and r.json()["model"] == "good"
    usage = (await client.get("/integrations/usage", headers=auth(a))).json()
    rows = {(u["model"], u["failures"]) for u in usage["by_model"]}
    assert ("bad", 1) in rows and ("good", 0) in rows
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"write": []}})
    for p in (p1, p2):
        await client.delete(f"/integrations/providers/{p}", headers=auth(a))


async def test_parse_json_variants():
    assert llm_router.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm_router.parse_json('Sure! {"a": [1,2]} hope that helps') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        llm_router.parse_json("no json here")


async def test_notification_prefs_and_inapp(client, a):
    r = await client.get("/notifications/prefs", headers=auth(a))
    assert r.status_code == 200 and "post_review" in r.json()["events"]
    r = await client.put("/notifications/prefs/email", headers=auth(a), json={"enabled": True, "events": ["post_failed"], "target": "me@hrpai.in"})
    assert r.json()["channels"]["email"]["events"] == ["post_failed"]
    assert (await client.put("/notifications/prefs/email", headers=auth(a), json={"events": ["bogus"]})).status_code == 400
    # Telegram link needs a configured bot.
    assert (await client.post("/notifications/telegram/link", headers=auth(a))).status_code == 400

    from app.services.notify import notify_org

    await notify_org(a["organization"]["id"], "post_review", "A post needs review", "Body", link="/approvals", permission="content.approve")
    n = (await client.get("/notifications?unread=true", headers=auth(a))).json()
    assert n["unread"] >= 1 and any(i["title"] == "A post needs review" for i in n["items"])
    assert (await client.post("/notifications/read", headers=auth(a), json={})).status_code == 204
    assert (await client.get("/notifications", headers=auth(a))).json()["unread"] == 0


async def test_dashboard_shape(client, a):
    d = (await client.get("/dashboard", headers=auth(a))).json()
    assert set(d["counts"]) >= {"in_review", "scheduled_7d", "published_7d", "failed", "manual_pending"}
    assert [s["key"] for s in d["setup"]][:2] == ["org", "ai"]


async def test_brand_look(client, a):
    r = await client.put("/content/brand/look", headers=auth(a), json={"colors": {"primary": "not-a-colour"}})
    assert r.status_code == 422
    r = await client.put("/content/brand/look", headers=auth(a), json={
        "colors": {"primary": "#112233", "secondary": "#22d3ee"}, "display_name": "HRP AI", "website": "hrpai.in"})
    assert r.status_code == 200
    b = r.json()
    assert b["colors"]["primary"] == "#112233" and b["look"]["website"] == "hrpai.in"


async def _user_with_role(client, a, email: str, perms: list[str]) -> dict:
    r = await client.post("/roles", headers=auth(a), json={"name": f"Role {email}", "permissions": perms})
    assert r.status_code == 201, r.text
    r = await client.post("/users", headers=auth(a), json={"email": email, "full_name": "Tester", "role_ids": [r.json()["id"]]})
    assert r.status_code == 201, r.text
    return await login(client, email, r.json()["temporary_password"])


async def test_cannot_grant_roles_beyond_own_permissions(client, a):
    u = await _user_with_role(client, a, "usermgr@hrpai.in", ["user.view", "user.manage", "role.view"])
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(a))).json()}
    r = await client.post("/users", headers=auth(u), json={"email": "x2@hrpai.in", "full_name": "X", "role_ids": [roles["Administrator"]]})
    assert r.status_code == 403 and "Administrator" in r.json()["detail"]
    r = await client.patch(f"/users/{u['user']['id']}", headers=auth(u), json={"role_ids": [roles["Administrator"]]})
    assert r.status_code == 403


async def test_automatic_modes_need_approve_and_publish(client, a):
    from app.core.db import SessionLocal, set_tenant
    from app.models import SocialAccount

    async with SessionLocal() as s:
        set_tenant(s, a["organization"]["id"])
        acc = SocialAccount(organization_id=a["organization"]["id"], platform="manual", name="Manual test", status="manual")
        s.add(acc)
        await s.commit()
        acc_id = str(acc.id)
    u = await _user_with_role(client, a, "scheduler@hrpai.in", ["schedule.view", "schedule.manage", "content.view"])
    body = {"social_account_id": acc_id, "name": "Auto", "times": ["10:00"], "post_types": {"text": 1}, "mode": "full_auto"}
    r = await client.post("/content/schedules", headers=auth(u), json=body)
    assert r.status_code == 403
    r = await client.post("/content/schedules", headers=auth(u), json={**body, "mode": "approve_first"})
    assert r.status_code == 201, r.text
    r = await client.patch(f"/content/schedules/{r.json()['id']}", headers=auth(u), json={"mode": "auto_with_window"})
    assert r.status_code == 403


async def test_jobs_api_requires_permissions(client, a):
    u = await _user_with_role(client, a, "nobody@hrpai.in", ["org.settings.view"])
    assert (await client.get("/jobs", headers=auth(u))).status_code == 403
