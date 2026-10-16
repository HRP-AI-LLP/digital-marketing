"""Public HTTPS tunnel: only OAuth callbacks and signed media links are reachable through it."""

from app.core.config import get_settings
from app.services.social import common
from tests.conftest import auth, login

TUNNEL = {"cf-connecting-ip": "203.0.113.9", "cf-ray": "abc-BOM"}


async def test_tunnel_blocks_everything_but_callbacks(client, admin):
    a = await login(client, "admin@hrpai.in", "Str0ngPassw0rd!")
    # Normal local use is unaffected.
    assert (await client.get("/auth/me", headers=auth(a))).status_code == 200
    # Through the tunnel: API, login and docs are refused, even with a valid token.
    assert (await client.get("/auth/me", headers={**auth(a), **TUNNEL})).status_code == 404
    assert (await client.post("/auth/login", headers=TUNNEL, json={"email": "admin@hrpai.in", "password": "x"})).status_code == 404
    assert (await client.get("/integrations/providers", headers={**auth(a), **TUNNEL})).status_code == 404
    # Allowed: OAuth callback (reaches the handler, which redirects back to the app) and health.
    r = await client.get("/social/oauth/meta/callback?state=nope&code=x", headers=TUNNEL, follow_redirects=False)
    assert r.status_code in (302, 303, 307, 400)
    assert (await client.get("/health", headers=TUNNEL)).status_code == 200
    # Media links must be well-formed; writes are refused.
    assert (await client.get("/media/public/not-a-uuid/tok", headers=TUNNEL)).status_code == 404
    assert (await client.post("/social/oauth/meta/callback", headers=TUNNEL)).status_code == 404


async def test_redirect_uris_use_https_only_where_needed(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "public_base_url", "https://studio.hrpai.in")
    monkeypatch.setattr(s, "oauth_https_providers", "meta,threads,pinterest")
    assert common.redirect_uri("meta") == "https://studio.hrpai.in/api/v1/social/oauth/meta/callback"
    assert common.redirect_uri("google").startswith("http://localhost:")
    assert common.redirect_uri("linkedin").startswith("http://localhost:")
    monkeypatch.setattr(s, "oauth_https_providers", "*")
    assert common.redirect_uri("google") == "https://studio.hrpai.in/api/v1/social/oauth/google/callback"
    monkeypatch.setattr(s, "public_base_url", "")
    assert common.redirect_uri("meta").startswith("http://localhost:")
