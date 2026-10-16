"""The one place social adapters get an HTTP client. Tests monkeypatch `client` with a
factory that returns an httpx.AsyncClient on an httpx.MockTransport."""

import httpx

DEFAULT_TIMEOUT = 60.0
UPLOAD_TIMEOUT = 600.0


def client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0),
        follow_redirects=False,
        headers={"User-Agent": "HRP-Digital-Marketing-Studio/1.0"},
    )
