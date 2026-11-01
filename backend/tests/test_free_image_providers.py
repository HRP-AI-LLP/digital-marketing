"""Free image providers (Pollinations, Cloudflare Workers AI, NVIDIA NIM, SiliconFlow, AI Horde) and Openverse stock."""

import base64
import io
import json
import uuid

import httpx
import pytest
from PIL import Image

from app.models import ProviderConfig
from app.services import image_gen, stock
from app.services.llm.catalog import BY_ID


def png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 64), "teal").save(b, "PNG")
    return b.getvalue()


PNG = png()


@pytest.fixture
def http(monkeypatch):
    calls: list[httpx.Request] = []
    routes: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        for pat, fn in routes:
            if pat in str(req.url):
                return fn(req)
        return httpx.Response(404, json={"error": "no route " + str(req.url)})

    monkeypatch.setattr(image_gen, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(image_gen.asyncio, "sleep", lambda s: _noop())
    return calls, routes


async def _noop():
    return None


def cfg(provider: str, key: str | None = "k", base: str | None = None) -> ProviderConfig:
    from app.core.crypto import encrypt_json

    return ProviderConfig(id=uuid.uuid4(), organization_id=uuid.uuid4(), provider=provider, kind="image", name=provider,
                          base_url=base, enabled=True, settings={}, secrets_enc=encrypt_json({"api_key": key} if key else {}))


async def test_catalog_lists_the_free_providers():
    for pid in ("pollinations", "cloudflare_images", "nvidia_images", "siliconflow_images", "ai_horde", "openverse"):
        assert pid in BY_ID
    assert not BY_ID["pollinations"].key and not BY_ID["ai_horde"].key and not BY_ID["openverse"].key


async def test_pollinations_needs_no_key(http):
    calls, routes = http
    routes.append(("image.pollinations.ai/prompt/", lambda r: httpx.Response(200, content=PNG, headers={"content-type": "image/png"})))
    out = await image_gen._pollinations(cfg("pollinations", key=None), "flux", "a calm office", "blurry", 1080, 1350, 7)
    assert out == PNG
    q = calls[0].url.params
    assert q["model"] == "flux" and q["nologo"] == "true" and q["seed"] == "7" and "authorization" not in calls[0].headers


async def test_cloudflare_flux_base64_and_account_check(http):
    calls, routes = http
    routes.append(("/ai/run/@cf/black-forest-labs/flux-1-schnell",
                   lambda r: httpx.Response(200, json={"result": {"image": base64.b64encode(PNG).decode()}})))
    with pytest.raises(image_gen.ImageGenError):
        await image_gen._cf_image(cfg("cloudflare_images"), "", "x", "", 512, 512, 1)
    c = cfg("cloudflare_images", base="https://api.cloudflare.com/client/v4/accounts/abc/ai/run")
    assert await image_gen._cf_image(c, "@cf/black-forest-labs/flux-1-schnell", "x", "", 512, 512, 1) == PNG
    assert json.loads(calls[-1].content)["steps"] == 8


async def test_nvidia_and_siliconflow(http):
    calls, routes = http
    routes.append(("ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-schnell",
                   lambda r: httpx.Response(200, json={"artifacts": [{"base64": base64.b64encode(PNG).decode()}]})))
    assert await image_gen._nvidia_image(cfg("nvidia_images"), "black-forest-labs/flux.1-schnell", "x", "", 1024, 1024, 3) == PNG
    routes.append(("api.siliconflow.com/v1/images/generations", lambda r: httpx.Response(200, json={"images": [{"url": "https://cdn.sf/x.png"}]})))
    routes.append(("cdn.sf/x.png", lambda r: httpx.Response(200, content=PNG)))
    assert await image_gen._siliconflow_image(cfg("siliconflow_images"), "Kwai-Kolors/Kolors", "x", "bad", 1080, 1350, 3) == PNG
    body = json.loads([c for c in calls if "siliconflow" in str(c.url)][0].content)
    assert body["image_size"] == "960x1280" and body["negative_prompt"] == "bad"


async def test_ai_horde_queue(http):
    calls, routes = http
    state = {"n": 0}

    def check(r):
        state["n"] += 1
        return httpx.Response(200, json={"done": state["n"] >= 2, "is_possible": True})

    routes.append(("/generate/async", lambda r: httpx.Response(202, json={"id": "job1"})))
    routes.append(("/generate/check/job1", check))
    routes.append(("/generate/status/job1", lambda r: httpx.Response(200, json={"generations": [{"img": "https://r2.horde/img.webp"}]})))
    routes.append(("r2.horde/img.webp", lambda r: httpx.Response(200, content=PNG)))
    out = await image_gen._horde(cfg("ai_horde", key=None), "", "x", "ugly", 1080, 1080, 5)
    assert out == PNG
    sent = calls[0]
    assert sent.headers["apikey"] == "0000000000" and "Client-Agent" in sent.headers and " ### ugly" in json.loads(sent.content)["prompt"]


async def test_openverse_search_without_key(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        assert "authorization" not in req.headers and req.url.params["license_type"] == "commercial,modification"
        return httpx.Response(200, json={"results": [{"id": "ov1", "url": "https://img/x.jpg", "thumbnail": "https://img/t.jpg",
                                                      "width": 1200, "height": 800, "creator": "Ana", "license": "by",
                                                      "license_version": "4.0", "title": "Desk", "foreign_landing_url": "https://f/x"}]})

    monkeypatch.setattr(image_gen, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    c = stock._Cfg(cfg("openverse", key=None), "", "https://api.openverse.org/v1")
    items = await stock._search_one(c, "desk", "photo", "landscape", 5)
    assert items[0]["provider"] == "openverse" and items[0]["download_url"] == "https://img/x.jpg"
    assert "CC BY 4.0" in items[0]["attribution"]
