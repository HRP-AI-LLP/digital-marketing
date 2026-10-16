from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant
from app.models import Role
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"


async def test_health(client):
    r = await client.get("/health")
    assert r.json()["status"] == "ok"


async def test_bad_password_is_rejected(client):
    r = await client.post("/auth/login", json={"email": "admin@hrpai.in", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Email or password is incorrect."


async def test_bootstrap_creates_hrp_with_admin(client, admin):
    assert admin["organization"]["name"] == "HRP AI LLP"
    assert admin["organization"]["timezone"] == "Asia/Kolkata"
    assert admin["user"]["is_platform_admin"] is True
    assert admin["user"]["must_change_password"] is False
    assert "user.manage" in admin["permissions"]


async def test_old_access_token_dies_after_password_change(client):
    r = await client.post("/platform/organizations", headers=auth(await login(client, "admin@hrpai.in", ADMIN_PW)),
                          json={"name": "Pw Test Org", "admin_email": "pw@test.in", "admin_name": "Pw"})
    temp = r.json()["temporary_password"]
    t1 = await login(client, "pw@test.in", temp)
    r = await client.post("/auth/change-password", headers=auth(t1),
                          json={"current_password": temp, "new_password": "AnotherPass123"})
    assert r.status_code == 200
    assert (await client.get("/auth/me", headers=auth(t1))).status_code == 401
    assert (await client.get("/auth/me", headers=auth(r.json()))).status_code == 200


async def test_refresh_rotation_and_reuse_detection(client):
    t = await login(client, "admin@hrpai.in", ADMIN_PW)
    r1 = await client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]})
    assert r1.status_code == 200
    # A second tab using the same token within a minute still gets a session.
    assert (await client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 200
    # Reusing a rotated token later (simulated by back-dating) signs the user out everywhere.
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.core.db import platform_session
    from app.core.security import hash_refresh_token
    from app.models import RefreshToken

    async with platform_session() as s:
        await s.execute(update(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(t["refresh_token"]))
                        .values(revoked_at=datetime.now(UTC) - timedelta(minutes=5)))
        await s.commit()
    assert (await client.post("/auth/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]})).status_code == 401


async def test_tenant_isolation_through_api(client, admin):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(a),
                          json={"name": "Acme Retail", "admin_email": "owner@acme-retail.in", "admin_name": "Acme Owner"})
    assert r.status_code == 201, r.text
    acme = await login(client, "owner@acme-retail.in", r.json()["temporary_password"])
    assert acme["organization"]["name"] == "Acme Retail"

    acme_users = (await client.get("/users", headers=auth(acme))).json()
    assert [u["email"] for u in acme_users] == ["owner@acme-retail.in"]

    hrp_users = (await client.get("/users", headers=auth(a))).json()
    assert "owner@acme-retail.in" not in [u["email"] for u in hrp_users]

    # Acme admin cannot touch an HRP user or role.
    hrp_admin_id = admin["user"]["id"]
    r = await client.patch(f"/users/{hrp_admin_id}", headers=auth(acme), json={"is_active": False})
    assert r.status_code == 404
    hrp_role_id = (await client.get("/roles", headers=auth(a))).json()[1]["id"]
    assert (await client.delete(f"/roles/{hrp_role_id}", headers=auth(acme))).status_code == 404

    # Non-platform admins can't reach the platform API.
    assert (await client.get("/platform/organizations", headers=auth(acme))).status_code == 403


async def test_row_level_security_in_database(client, admin):
    """Even raw queries through the app role only see the current tenant's rows."""
    hrp_id = admin["organization"]["id"]
    async with SessionLocal() as s:
        set_tenant(s, hrp_id)
        roles = (await s.scalars(select(Role))).all()
        assert roles and all(str(r.organization_id) == hrp_id for r in roles)
    async with SessionLocal() as s:  # no tenant set -> nothing visible
        assert (await s.scalars(select(Role))).all() == []


async def test_rbac_viewer_cannot_manage_users(client):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(a))).json()}
    r = await client.post("/users", headers=auth(a),
                          json={"email": "viewer@hrpai.in", "full_name": "Vee Viewer", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 201, r.text
    v = await login(client, "viewer@hrpai.in", r.json()["temporary_password"])
    assert (await client.get("/users", headers=auth(v))).status_code == 200
    r = await client.post("/users", headers=auth(v),
                          json={"email": "x@hrpai.in", "full_name": "X", "role_ids": [roles["Viewer"]]})
    assert r.status_code == 403
    assert "user.manage" in r.json()["detail"]


async def test_custom_role_and_system_role_protection(client):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/roles", headers=auth(a),
                          json={"name": "SEO writer", "permissions": ["seo.view", "seo.manage"]})
    assert r.status_code == 201
    rid = r.json()["id"]
    r = await client.patch(f"/roles/{rid}", headers=auth(a), json={"permissions": ["seo.view"]})
    assert r.json()["permissions"] == ["seo.view"]
    bad = await client.post("/roles", headers=auth(a), json={"name": "Bad role", "permissions": ["bogus.perm"]})
    assert bad.status_code == 400
    admin_role = next(x for x in (await client.get("/roles", headers=auth(a))).json() if x["is_system"])
    assert (await client.patch(f"/roles/{admin_role['id']}", headers=auth(a), json={"permissions": []})).status_code == 400
    assert (await client.delete(f"/roles/{admin_role['id']}", headers=auth(a))).status_code == 400
    assert (await client.delete(f"/roles/{rid}", headers=auth(a))).status_code == 204


async def test_last_admin_cannot_be_removed(client):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(a),
                          json={"name": "Solo Org", "admin_email": "solo@test.in", "admin_name": "Solo"})
    solo = await login(client, "solo@test.in", r.json()["temporary_password"])
    viewer_id = next(x["id"] for x in (await client.get("/roles", headers=auth(solo))).json() if x["name"] == "Viewer")
    r = await client.patch(f"/users/{solo['user']['id']}", headers=auth(solo), json={"role_ids": [viewer_id]})
    assert r.status_code == 400
    assert "last Administrator" in r.json()["detail"]


async def test_user_in_two_orgs_can_switch(client):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    orgs = (await client.get("/platform/organizations", headers=auth(a))).json()
    acme = next(o for o in orgs if o["name"] == "Acme Retail")
    # HRP admin adds an existing Acme user to HRP as Reviewer.
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=auth(a))).json()}
    r = await client.post("/users", headers=auth(a),
                          json={"email": "owner@acme-retail.in", "full_name": "Acme Owner", "role_ids": [roles["Reviewer"]]})
    assert r.status_code == 201 and r.json()["temporary_password"] is None
    # That user can't be renamed or have a password reset by HRP (they also belong to Acme).
    uid = r.json()["member"]["user_id"]
    assert (await client.post(f"/users/{uid}/reset-password", headers=auth(a))).status_code == 403

    pa = await login(client, "admin@hrpai.in", ADMIN_PW, org_id=acme["id"])  # platform support access
    assert pa["organization"]["id"] == acme["id"]
    sw = await client.post("/auth/switch-organization", headers=auth(a),
                           json={"organization_id": acme["id"], "refresh_token": a["refresh_token"]})
    assert sw.status_code == 200 and sw.json()["organization"]["name"] == "Acme Retail"


async def test_suspended_org_blocks_its_users(client):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(a),
                          json={"name": "Paused Co", "admin_email": "p@paused-co.in", "admin_name": "P"})
    org_id, pw = r.json()["organization"]["id"], r.json()["temporary_password"]
    p = await login(client, "p@paused-co.in", pw)
    await client.patch(f"/platform/organizations/{org_id}", headers=auth(a), json={"status": "suspended"})
    assert (await client.get("/users", headers=auth(p))).status_code == 403
    assert (await client.post("/auth/login", json={"email": "p@paused-co.in", "password": pw})).status_code == 403


async def test_audit_log_is_tenant_scoped(client):
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    items = (await client.get("/audit", headers=auth(a))).json()["items"]
    assert any(i["action"] == "user.added" for i in items)
    assert not any("Acme" in (i["summary"] or "") for i in items)
