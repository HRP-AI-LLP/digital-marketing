"""Two-step sign-in (TOTP), recovery codes, lockout, the organization's require-2FA policy, admin reset,
signed-in devices and new-device sign-in emails."""

import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.db import platform_session
from app.models import AuditLog, Job, User
from app.services import qr, twofa
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"


# ---- RFC 6238 appendix B test vectors -------------------------------------------------------------------
SEED_SHA1 = b"12345678901234567890"
SEED_SHA256 = b"12345678901234567890123456789012"
SEED_SHA512 = b"1234567890123456789012345678901234567890123456789012345678901234"
VECTORS = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


async def test_rfc6238_vectors():
    for t, sha1, sha256, sha512 in VECTORS:
        assert twofa.totp(SEED_SHA1, t, digits=8) == sha1
        assert twofa.totp(SEED_SHA256, t, digits=8, algorithm="sha256") == sha256
        assert twofa.totp(SEED_SHA512, t, digits=8, algorithm="sha512") == sha512


async def test_rfc4226_hotp_vectors():
    expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]
    assert [twofa.hotp(SEED_SHA1, i) for i in range(10)] == expected


async def test_match_step_window_and_replay():
    secret = twofa.new_secret()
    key = twofa.secret_bytes(secret)
    now = 1_700_000_000
    step = now // 30
    assert twofa.match_step(secret, twofa.hotp(key, step), at=now) == step
    assert twofa.match_step(secret, twofa.hotp(key, step - 1), at=now) == step - 1  # phone clock a bit behind
    assert twofa.match_step(secret, twofa.hotp(key, step + 2), at=now) is None
    assert twofa.match_step(secret, twofa.hotp(key, step), at=now, last_step=step) is None  # replay
    assert twofa.match_step(secret, "12 34", at=now) is None


async def test_otpauth_uri_and_qr():
    uri = twofa.otpauth_uri("JBSWY3DPEHPK3PXP", "someone@hrpai.in")
    assert uri.startswith("otpauth://totp/HRP%20Marketing%20Studio:someone@hrpai.in?secret=JBSWY3DPEHPK3PXP")
    assert "issuer=HRP%20Marketing%20Studio" in uri
    svg = qr.svg(uri)
    assert svg.startswith("<svg") and "<path" in svg
    code = qr.QrCode(uri.encode())
    assert code.size == code.version * 4 + 17
    # Finder pattern corners are dark, the separator next to them light.
    assert code.modules[0][0] and code.modules[6][6] and not code.modules[7][7]
    assert qr.svg("x" * 400) is None


# ---- helpers --------------------------------------------------------------------------------------------
async def _new_org(client, name: str) -> tuple[dict, str, str]:
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    email = f"{uuid.uuid4().hex[:8]}@{name.lower().replace(' ', '-')}.in"
    r = await client.post("/platform/organizations", headers=auth(a),
                          json={"name": name, "admin_email": email, "admin_name": "Owner Person"})
    assert r.status_code == 201, r.text
    temp = r.json()["temporary_password"]
    return await login(client, email, temp), email, temp


async def _enable(client, tokens: dict) -> tuple[str, list[str]]:
    r = await client.post("/account/2fa/setup", headers=auth(tokens))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["qr_svg"].startswith("<svg")
    secret = body["secret"].replace(" ", "")
    r = await client.post("/account/2fa/enable", headers=auth(tokens), json={"code": "000000"
                          if twofa.totp(twofa.secret_bytes(secret)) != "000000" else "111111"})
    assert r.status_code == 400
    r = await client.post("/account/2fa/enable", headers=auth(tokens),
                          json={"code": twofa.totp(twofa.secret_bytes(secret))})
    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert r.json()["totp_enabled"] is True and r.json()["recovery_codes_left"] == 10
    return secret, codes


async def _code_after_last(email: str, secret: str) -> str:
    """A valid code newer than the last accepted one (so tests don't wait 30 seconds)."""
    async with platform_session() as s:
        u = await s.scalar(select(User).where(User.email == email))
        last = u.totp_last_step or 0
    step = max(int(time.time() // 30), last + 1)
    assert step <= int(time.time() // 30) + 1, "test ran out of valid steps"
    return twofa.hotp(twofa.secret_bytes(secret), step)


async def _unlock(email: str) -> None:
    async with platform_session() as s:
        await s.execute(update(User).where(User.email == email).values(totp_locked_until=None, totp_failed_count=0,
                                                                         totp_last_step=None))
        await s.commit()


# ---- flows ----------------------------------------------------------------------------------------------
async def test_login_challenge_flow_and_recovery_codes(client, admin):
    tokens, email, pw = await _new_org(client, "Twofa Flow Org")
    secret, codes = await _enable(client, tokens)

    r = await client.post("/auth/login", json={"email": email, "password": pw, "device_name": "Web"})
    assert r.status_code == 200
    body = r.json()
    assert body["two_factor_required"] is True and "access_token" not in body
    challenge = body["challenge"]

    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "code": "123456"
                          if twofa.totp(twofa.secret_bytes(secret)) != "123456" else "654321"})
    assert r.status_code == 401
    assert "didn't work" in r.json()["detail"]
    r = await client.post("/auth/login/2fa", json={"challenge": "garbage", "code": "123456"})
    assert r.status_code == 401

    code = await _code_after_last(email, secret)
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "code": code})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["access_token"] and t["user"]["totp_enabled"] is True
    assert (await client.get("/auth/me", headers=auth(t))).status_code == 200

    # The same code can't be used twice (replay).
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "code": code})
    assert r.status_code == 401

    # A recovery code works once.
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "recovery_code": codes[0].upper()})
    assert r.status_code == 200, r.text
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "recovery_code": codes[0]})
    assert r.status_code == 401 and "already used" in r.json()["detail"]
    st = (await client.get("/account/security", headers=auth(t))).json()
    assert st["recovery_codes_left"] == 9

    async with platform_session() as s:
        actions = (await s.scalars(select(AuditLog.summary).where(AuditLog.actor_email == email,
                                                                  AuditLog.action == "auth.login"))).all()
    assert any("recovery code" in (a or "") for a in actions)

    # Turning it off needs a code.
    r = await client.post("/account/2fa/disable", headers=auth(t), json={})
    assert r.status_code == 400
    await _unlock(email)
    r = await client.post("/account/2fa/disable", headers=auth(t), json={"code": await _code_after_last(email, secret)})
    assert r.status_code == 200 and r.json()["totp_enabled"] is False
    r = await client.post("/auth/login", json={"email": email, "password": pw})
    assert "access_token" in r.json()


async def test_lockout_after_five_wrong_codes(client, admin):
    tokens, email, pw = await _new_org(client, "Twofa Lock Org")
    secret, _ = await _enable(client, tokens)
    challenge = (await client.post("/auth/login", json={"email": email, "password": pw})).json()["challenge"]
    right = twofa.totp(twofa.secret_bytes(secret))
    wrong = "000000" if right != "000000" else "999999"
    for i in range(4):
        assert (await client.post("/auth/login/2fa", json={"challenge": challenge, "code": wrong})).status_code == 401
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "code": wrong})
    assert r.status_code == 429
    # Even the right code is refused while locked.
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "code": await _code_after_last(email, secret)})
    assert r.status_code == 429 and "Try again in" in r.json()["detail"]
    async with platform_session() as s:
        u = await s.scalar(select(User).where(User.email == email))
        assert u.totp_locked_until > datetime.now(UTC) + timedelta(minutes=9)
        # Lock expires.
        u.totp_locked_until = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "code": await _code_after_last(email, secret)})
    assert r.status_code == 200, r.text


async def test_expired_challenge_and_password_change_invalidate(client, admin):
    tokens, email, pw = await _new_org(client, "Twofa Challenge Org")
    secret, _ = await _enable(client, tokens)
    challenge = (await client.post("/auth/login", json={"email": email, "password": pw})).json()["challenge"]
    async with platform_session() as s:
        await s.execute(update(User).where(User.email == email).values(token_version=User.token_version + 1))
        await s.commit()
    r = await client.post("/auth/login/2fa", json={"challenge": challenge, "code": await _code_after_last(email, secret)})
    assert r.status_code == 401


async def test_require_2fa_policy_and_admin_reset(client, admin):
    owner, email, pw = await _new_org(client, "Twofa Policy Org")
    # A member with a custom role that can manage settings, but isn't an Administrator.
    r = await client.post("/roles", headers=auth(owner),
                          json={"name": "Settings editor", "permissions": ["org.settings.view", "org.settings.manage", "user.view"]})
    assert r.status_code == 201, r.text
    role_id = r.json()["id"]
    member_email = f"m-{uuid.uuid4().hex[:6]}@policy.in"
    r = await client.post("/users", headers=auth(owner),
                          json={"email": member_email, "full_name": "Member Person", "role_ids": [role_id], "send_email": False})
    assert r.status_code == 201, r.text
    member_pw = r.json()["temporary_password"]
    member = await login(client, member_email, member_pw)
    assert member["two_factor_setup_required"] is False

    r = await client.patch("/organization/security", headers=auth(member), json={"require_2fa": True})
    assert r.status_code == 403
    r = await client.patch("/organization/security", headers=auth(owner), json={"require_2fa": True})
    assert r.status_code == 200 and r.json()["require_2fa"] is True and r.json()["can_change"] is True
    assert (await client.get("/organization/security", headers=auth(member))).json()["can_change"] is False

    member = await login(client, member_email, member_pw)
    assert member["two_factor_setup_required"] is True
    secret, _ = await _enable(client, member)
    me = (await client.get("/auth/me", headers=auth(member))).json()
    assert me["two_factor_setup_required"] is False and me["user"]["totp_enabled"] is True

    users = (await client.get("/users", headers=auth(owner))).json()
    assert next(u for u in users if u["email"] == member_email)["two_factor_enabled"] is True

    # The member lost their phone: the Administrator resets it.
    r = await client.post(f"/users/{member['user']['id']}/reset-2fa", headers=auth(owner))
    assert r.status_code == 200, r.text
    assert r.json()["two_factor_enabled"] is False
    async with platform_session() as s:
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "user.2fa_reset",
                                                      AuditLog.entity_id == member["user"]["id"]))
    assert entry is not None and member_email in entry.summary
    member = await login(client, member_email, member_pw)  # password only again, setup required
    assert "access_token" in member and member["two_factor_setup_required"] is True
    r = await client.post(f"/users/{member['user']['id']}/reset-2fa", headers=auth(owner))
    assert r.status_code == 400


async def test_sessions_list_and_sign_out_others(client, admin):
    t1, email, pw = await _new_org(client, "Twofa Sessions Org")
    r = await client.post("/auth/login", json={"email": email, "password": pw, "device_name": "Android phone"})
    t2 = r.json()
    sessions = (await client.get("/account/sessions", headers=auth(t1))).json()
    assert len(sessions) == 2
    assert sum(1 for x in sessions if x["current"]) == 1
    assert {x["device_name"] for x in sessions} >= {"Android phone"}
    # Refresh keeps the device and the original sign-in time.
    r = await client.post("/auth/refresh", json={"refresh_token": t2["refresh_token"]})
    t2 = r.json()
    after = (await client.get("/account/sessions", headers=auth(t2))).json()
    phone = next(x for x in after if x["device_name"] == "Android phone")
    assert phone["current"] is True and phone["signed_in_at"] <= phone["last_used_at"]

    r = await client.post("/account/sessions/sign-out-others", headers=auth(t1))
    assert r.json()["signed_out"] == 1
    fresh = r.json()["tokens"]
    # The other device: its access token stops working and its refresh token is refused (without signing this
    # device out as a suspected theft).
    assert (await client.get("/auth/me", headers=auth(t2))).status_code == 401
    r = await client.post("/auth/refresh", json={"refresh_token": t2["refresh_token"]})
    assert r.status_code == 401 and "signed out on this device" in r.json()["detail"]
    assert (await client.get("/auth/me", headers=auth(fresh))).status_code == 200
    assert (await client.post("/auth/refresh", json={"refresh_token": fresh["refresh_token"]})).status_code == 200
    left = (await client.get("/account/sessions", headers=auth(fresh))).json()
    assert len(left) == 1

    # Signing out one device by id.
    t3 = (await client.post("/auth/login", json={"email": email, "password": pw, "device_name": "Tablet"})).json()
    tab = next(x for x in (await client.get("/account/sessions", headers=auth(t3))).json() if x["device_name"] == "Tablet")
    assert (await client.delete(f"/account/sessions/{tab['id']}", headers=auth(t3))).status_code == 200
    assert (await client.post("/auth/refresh", json={"refresh_token": t3["refresh_token"]})).status_code == 401


async def test_new_device_sign_in_email(client, admin, monkeypatch):
    t, email, pw = await _new_org(client, "Twofa Notice Org")  # first sign-in: device remembered, no email
    org_id = t["organization"]["id"]

    async def jobs():
        async with platform_session() as s:
            return (await s.scalars(select(Job).where(Job.kind == "security.signin_notice",
                                                      Job.organization_id == uuid.UUID(org_id)))).all()

    await login(client, email, pw)  # same device ("Web" with no browser → no name): nothing
    assert len(await jobs()) == 0
    r = await client.post("/auth/login", json={"email": email, "password": pw, "device_name": "iPhone app"})
    assert r.status_code == 200
    rows = await jobs()
    assert len(rows) == 1 and rows[0].payload["device"] == "iPhone app"
    await client.post("/auth/login", json={"email": email, "password": pw, "device_name": "iPhone app"})
    assert len(await jobs()) == 1  # known now

    sent = []

    async def fake_sender(session, org=None):
        return object()

    async def fake_send(cfg, to, subject, body, link=None, **kw):
        sent.append((to, subject, body))

    import app.modules.security  # noqa: F401  (registers the handler)
    from app.services import jobs as jobsvc

    monkeypatch.setattr("app.services.mailer.default_sender", fake_sender)
    monkeypatch.setattr("app.services.notify.send_email", fake_send)
    res = await jobsvc.run_inline("security.signin_notice", uuid.UUID(org_id), rows[0].payload)
    assert res == {"sent": True}
    assert sent[0][0] == email and "iPhone app" in sent[0][2]
