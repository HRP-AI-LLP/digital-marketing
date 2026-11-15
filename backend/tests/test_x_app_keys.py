"""X sign-in: the app keys are checked, because X itself only says "Something went wrong"."""

import base64

import httpx
import pytest

from app.services.social import x
from app.services.social.common import PublishError

GOOD = base64.urlsafe_b64encode(b"cHJYb0xYOHhJdW5GZ3pLc0VBRGg:1:ci").decode().rstrip("=")
CB = "https://uat-dm.hrpai.in/api/v1/social/oauth/x/callback"


async def test_client_id_shapes():
    assert x.client_id_problem(GOOD) is None
    assert "API Key" in x.client_id_problem("Xy7PqR2sT4uV6wX8yZ0aB1cDe")  # 25 characters: an OAuth 1.0a consumer key
    assert "Access Token" in x.client_id_problem("1234567890123456789-AbCdEfGhIjKlMnOpQrStUvWxYz")
    assert "spaces" in x.client_id_problem(f" {GOOD}")
    assert "empty" in x.client_id_problem("")


async def test_sign_in_link_refuses_api_key_and_matches_docs():
    with pytest.raises(PublishError, match="API Key"):
        x.authorize_url({"client_id": "Xy7PqR2sT4uV6wX8yZ0aB1cDe"}, CB, "st", {}, "ch")
    url = x.authorize_url({"client_id": GOOD}, CB, "st", {}, "ch")
    assert "scope=tweet.read%20tweet.write%20users.read%20offline.access%20media.write" in url
    assert "redirect_uri=https%3A%2F%2Fuat-dm.hrpai.in%2Fapi%2Fv1%2Fsocial%2Foauth%2Fx%2Fcallback" in url
    assert "code_challenge_method=S256" in url


@pytest.mark.parametrize(("status", "body", "ok"), [
    (400, {"error": "invalid_request", "error_description": "Value passed for the token was invalid."}, True),
    (401, {"error": "unauthorized_client", "error_description": "Missing valid authorization header"}, False),
])
async def test_live_key_check(monkeypatch, status, body, ok):
    seen = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, auth=None, data=None):
            seen.update(url=url, data=data)
            return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(x.net, "client", lambda **k: Client())
    creds = {"client_id": GOOD, "client_secret": "s"}
    if ok:
        msg = await x.check_app(creds, CB)
        assert "accepted" in msg and CB in msg
    else:
        with pytest.raises(ValueError, match="didn't accept this Client ID"):
            await x.check_app(creds, CB)
    assert seen["url"].endswith("/2/oauth2/token") and seen["data"]["grant_type"] == "refresh_token"
