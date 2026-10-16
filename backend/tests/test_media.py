"""Media studio: rendering (real Chromium + ffmpeg), signed files, uploads, public URLs, provider request
shapes (mocked HTTP), generate_for_post per post type, tenant isolation."""

import base64
import io
import json
import time
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt_json
from app.core.db import SessionLocal, set_tenant
from app.core.signing import _sig, media_url
from app.models import LlmUsage, MediaAsset, Post, ProviderConfig
from app.services import image_gen, media, media_render, stock, tts, video
from app.services.media_templates import TEMPLATES, BrandStyle, default_logo_uri, registry, render_html
from app.services.media_templates.base import contrast, ensure_contrast
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"


# ------------------------------------------------------------------ fixtures
@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def media_env(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage"))
    old_motion = json.loads(json.dumps(media.MOTION))
    media.MOTION["gif"].update(seconds=2.0, fps=10)
    media.MOTION["animation"].update(seconds=2.0, fps=10)
    yield
    media.MOTION.update(old_motion)
    s.storage_dir = old
    await media_render.shutdown()


_RENDER: dict = {}


async def renderer_ok() -> None:
    """Skip render tests (with the reason) when Chromium can't be launched here."""
    if "ok" not in _RENDER:
        try:
            await media_render.render_png("<html><body>ok</body></html>", 64, 64)
            _RENDER["ok"], _RENDER["why"] = True, ""
        except Exception as e:  # noqa: BLE001
            _RENDER["ok"], _RENDER["why"] = False, str(e)
    if not _RENDER["ok"]:
        pytest.skip(f"Chromium/Playwright unavailable: {_RENDER['why'][:300]}")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def hrp(client, admin):
    t = await login(client, "admin@hrpai.in", ADMIN_PW)
    return {"tokens": t, "org_id": uuid.UUID(t["organization"]["id"])}


async def _new_org(client, name: str, email: str) -> dict:
    a = await login(client, "admin@hrpai.in", ADMIN_PW)
    r = await client.post("/platform/organizations", headers=auth(a), json={"name": name, "admin_email": email, "admin_name": name})
    assert r.status_code == 201, r.text
    t = await login(client, email, r.json()["temporary_password"])
    if t["user"]["must_change_password"]:
        r2 = await client.post("/auth/change-password", headers=auth(t),
                               json={"current_password": r.json()["temporary_password"], "new_password": "MediaPass123!"})
        t = r2.json()
    return {"tokens": t, "org_id": uuid.UUID(t["organization"]["id"])}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def other(client, admin):
    return await _new_org(client, "Media Other Org", "owner@media-other.in")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def prov(client, admin):
    """Separate org for provider tests so configured providers don't leak into the no-provider tests."""
    return await _new_org(client, "Media Providers Org", "owner@media-prov.in")


def tenant(org_id) -> "SessionLocal":
    s = SessionLocal()
    set_tenant(s, org_id)
    return s


def png_bytes(w=64, h=48, color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def jpg_bytes(w=64, h=48) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 50, 50)).save(buf, format="JPEG")
    return buf.getvalue()


async def add_provider(org_id, provider: str, kind: str, secrets: dict, settings: dict | None = None,
                       base_url: str | None = None) -> uuid.UUID:
    async with tenant(org_id) as s:
        cfg = ProviderConfig(organization_id=org_id, kind=kind, provider=provider, name=provider, base_url=base_url,
                             secrets_enc=encrypt_json(secrets), settings=settings or {}, enabled=True)
        s.add(cfg)
        await s.commit()
        return cfg.id


async def drop_providers(org_id, kind: str) -> None:
    async with tenant(org_id) as s:
        for c in (await s.scalars(select(ProviderConfig).where(ProviderConfig.kind == kind))).all():
            await s.delete(c)
        await s.commit()


def brand() -> BrandStyle:
    return BrandStyle(logo_uri=default_logo_uri(), website="hrpai.in")


# ------------------------------------------------------------------ templates (HTML only)
async def test_registry_and_html_escaping():
    names = {t["name"] for t in registry()}
    for n in ("quote_card", "tip_card", "stat_card", "announcement", "carousel_cover", "carousel_slide", "carousel_cta",
              "animated_text", "video_caption_overlay"):
        assert n in names
    evil = "<script>alert(1)</script> & *bold*"
    for name in TEMPLATES:
        html = render_html(name, {"headline": evil, "quote": evil, "caption": evil, "heading": evil, "lines": [evil],
                                  "bullets": [evil, "two", "three"], "stat": "42%"}, 1080, 1350, brand())
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;" in html
        assert "font-family:'Inter'" in html  # embedded font
    assert '<em class="hl">bold</em>' in render_html("photo_headline", {"headline": "a *bold* move"}, 1080, 1080, brand())


async def test_contrast_is_enforced():
    assert contrast("#ffffff", "#062f59") > 10
    fixed = ensure_contrast("#22d3ee", "#ffffff", 4.5)
    assert contrast(fixed, "#ffffff") >= 4.5
    # a brand with a very light "dark" colour still gets readable text
    b = BrandStyle.from_profile(colors={"dark": "#cfe8ff", "primary": "#ffcc00"})
    html = render_html("quote_card", {"quote": "hi"}, 800, 800, b)
    assert "--fg:#0f172a" in html or "--fg:#ffffff" not in html


# ------------------------------------------------------------------ real rendering
async def test_render_png_exact_size():
    await renderer_ok()
    html = render_html("tip_card", {"headline": "Three ways to *save time*", "bullets": ["One", "Two", "Three"]}, 540, 675, brand())
    png = await media_render.render_png(html, 540, 675)
    im = Image.open(io.BytesIO(png))
    assert im.size == (540, 675)
    assert len(set(im.convert("RGB").resize((20, 20)).getdata())) > 10  # not blank
    ov = await media_render.render_png(render_html("video_caption_overlay", {"caption": "Hello"}, 360, 640, brand()), 360, 640,
                                       transparent=True)
    oim = Image.open(io.BytesIO(ov))
    assert oim.mode == "RGBA" and oim.getpixel((180, 320))[3] == 0  # transparent middle


async def test_animation_to_mp4_and_gif(tmp_path):
    await renderer_ok()
    html = render_html("animated_text", {"lines": ["Line one", "Line two"], "duration": 1.5}, 320, 320, brand())
    mp4 = await media_render.render_video(html, 320, 320, 1.5, tmp_path / "a.mp4", fps=10)
    info = await media_render.probe(mp4)
    assert info["vcodec"] == "h264" and info["width"] == 320 and abs(info["duration"] - 1.5) < 0.25
    assert b"moov" in mp4.read_bytes()[:4096]  # faststart: moov atom before mdat
    gif = await media_render.mp4_to_gif(mp4, tmp_path / "a.gif", max_width=200, fps=10)
    data = gif.read_bytes()
    assert data[:6] == b"GIF89a" and len(data) < 8 * 1024 * 1024
    assert Image.open(gif).size[0] == 200


async def _tone(path: Path, seconds: float, freq: int = 440) -> Path:
    await media_render.run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-c:a", "aac",
                                   "-b:a", "96k", str(path)])
    return path


async def test_video_pipeline_with_voice_and_music(tmp_path):
    await renderer_ok()
    stills = []
    for i, c in enumerate([(200, 60, 60), (40, 160, 90)]):
        p = tmp_path / f"s{i}.png"
        Image.new("RGB", (400, 300), c).save(p)
        stills.append(p)
    ov = tmp_path / "ov.png"
    ov.write_bytes(await media_render.render_png(render_html("video_caption_overlay", {"caption": "Scene"}, 360, 640, brand()),
                                                 360, 640, transparent=True))
    voice = await _tone(tmp_path / "v.m4a", 1.2, 440)
    music = await _tone(tmp_path / "m.m4a", 1.0, 220)
    scenes = [video.Scene(2.0, stills[0], "image", ov, "in"), video.Scene(2.0, stills[1], "image", None, "left")]
    out = tmp_path / "out.mp4"
    info = await video.build_video(scenes, out, 360, 640, work=tmp_path, voice_clips=[(voice, 0.2)], music=music, fps=15)
    probe = await media_render.probe(out)
    assert probe["vcodec"] == "h264" and probe["has_audio"]
    assert (probe["width"], probe["height"]) == (360, 640)
    assert abs(info["duration"] - 4.0) < 0.35
    poster = await media_render.poster_frame(out, tmp_path / "p.jpg")
    assert poster.exists() and poster.stat().st_size > 0


# ------------------------------------------------------------------ storage, signed files, uploads
async def test_signed_file_endpoint_and_range(client, hrp):
    async with tenant(hrp["org_id"]) as s:
        a = await media.store_bytes(s, hrp["org_id"], png_bytes(), "png", source="upload")
        await s.commit()
    out = media.asset_out(a)
    assert set(out) >= {"id", "kind", "mime", "width", "height", "duration_s", "size_bytes", "source", "prompt", "alt_text",
                        "created_at", "url", "thumb_url", "meta"}
    assert out["width"] == 64 and out["kind"] == "image" and out["thumb_url"] == out["url"]
    r = await client.get("http://test" + out["url"])  # no Authorization header
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "private" in r.headers["cache-control"] and r.content[:4] == b"\x89PNG"
    r = await client.get("http://test" + out["url"], headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    bad = out["url"][:-4] + "0000"
    assert (await client.get("http://test" + bad)).status_code == 403
    exp = int(time.time()) - 60
    expired = f"/api/v1/media/file/{a.id}?o={hrp['org_id']}&e={exp}&s={_sig(str(a.id), str(hrp['org_id']), exp)}"
    assert (await client.get("http://test" + expired)).status_code == 403


async def test_upload_validation_and_library(client, hrp):
    h = auth(hrp["tokens"])
    files = [("files", ("photo.png", png_bytes(120, 80), "image/png")), ("files", ("pic.jpeg", jpg_bytes(), "image/jpeg"))]
    r = await client.post("/media/upload", headers=h, files=files)
    assert r.status_code == 201, r.text
    items = r.json()["items"]
    assert [i["kind"] for i in items] == ["image", "image"] and items[0]["width"] == 120
    # wrong content / mismatched extension / unsupported type
    r = await client.post("/media/upload", headers=h, files=[("files", ("x.png", b"not an image at all", "image/png"))])
    assert r.status_code == 400 and "PNG, JPG" in r.json()["detail"]
    r = await client.post("/media/upload", headers=h, files=[("files", ("x.png", jpg_bytes(), "image/png"))])
    assert r.status_code == 400 and "looks like a JPG" in r.json()["detail"]
    r = await client.post("/media/upload", headers=h, files=[("files", ("x.exe", b"MZ\x90\x00" * 10, "application/octet-stream"))])
    assert r.status_code == 400
    pdf = b"%PDF-1.4\n1 0 obj<</Type /Page>>endobj\n%%EOF"
    r = await client.post("/media/upload", headers=h, files=[("files", ("doc.pdf", pdf, "application/pdf"))])
    assert r.status_code == 201 and r.json()["items"][0]["kind"] == "document"
    # library list, filters, alt text, delete
    r = await client.get("/media", headers=h, params={"kind": "image", "source": "upload"})
    assert r.status_code == 200 and r.json()["total"] >= 2
    aid = items[0]["id"]
    r = await client.patch(f"/media/{aid}", headers=h, json={"alt_text": "A blue rectangle"})
    assert r.json()["alt_text"] == "A blue rectangle"
    r = await client.get("/media", headers=h, params={"search": "blue rect"})
    assert [i["id"] for i in r.json()["items"]] == [aid]
    assert (await client.delete(f"/media/{aid}", headers=h)).status_code == 204
    assert (await client.get(f"/media/{aid}", headers=h)).status_code == 404


async def test_upload_video_probes_duration(client, hrp, tmp_path):
    clip = tmp_path / "c.mp4"
    await media_render.run_ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)])
    r = await client.post("/media/upload", headers=auth(hrp["tokens"]), files=[("files", ("clip.mp4", clip.read_bytes(), "video/mp4"))])
    assert r.status_code == 201, r.text
    it = r.json()["items"][0]
    assert it["kind"] == "video" and (it["width"], it["height"]) == (160, 90) and abs(it["duration_s"] - 1.5) < 0.2
    assert it["thumb_url"] and it["thumb_url"].endswith("&v=poster")
    r = await client.get("http://test" + it["thumb_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


async def test_delete_refused_when_post_is_scheduled(client, hrp):
    h = auth(hrp["tokens"])
    async with tenant(hrp["org_id"]) as s:
        a = await media.store_bytes(s, hrp["org_id"], png_bytes(), "png", source="upload")
        s.add(Post(organization_id=hrp["org_id"], platform="linkedin_org", post_type="image", status="scheduled", media_ids=[str(a.id)]))
        await s.commit()
    r = await client.delete(f"/media/{a.id}", headers=h)
    assert r.status_code == 409 and "scheduled or published" in r.json()["detail"]


# ------------------------------------------------------------------ public URLs
async def test_ensure_public_url_needs_a_host(hrp):
    async with tenant(hrp["org_id"]) as s:
        a = await media.store_bytes(s, hrp["org_id"], png_bytes(), "png", source="upload")
        with pytest.raises(media.MediaUnavailable) as e:
            await media.ensure_public_url(s, a)
        assert "public media host" in e.value.message
        await s.rollback()


async def test_public_base_url_and_endpoint(client, prov):
    await add_provider(prov["org_id"], "public_base_url", "media_host", {"public_base_url": "https://media.example.com"})
    try:
        async with tenant(prov["org_id"]) as s:
            a = await media.store_bytes(s, prov["org_id"], png_bytes(), "png", source="upload")
            url = await media.ensure_public_url(s, a)
            await s.commit()
        assert url.startswith(f"https://media.example.com/api/v1/media/public/{a.id}/") and url.endswith(".png")
        r = await client.get("http://test" + url.removeprefix("https://media.example.com"))
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG" and "public" in r.headers["cache-control"]
        tampered = url.removeprefix("https://media.example.com").replace(".png", "").rstrip("0123456789abcdef") + "00"
        assert (await client.get("http://test" + tampered)).status_code == 403
    finally:
        await drop_providers(prov["org_id"], "media_host")


async def test_instagram_gets_a_jpeg_copy(client, prov):
    await add_provider(prov["org_id"], "public_base_url", "media_host", {"public_base_url": "https://media.example.com"})
    try:
        from app.services.social import common

        async with tenant(prov["org_id"]) as s:
            a = await media.store_bytes(s, prov["org_id"], png_bytes(), "png", source="upload")
            url = await common.public_url(s, a, jpeg=True)
            again = await common.public_url(s, a, jpeg=True)
            await s.commit()
        assert ".jpg?v=jpg" in url and not (a.meta or {}).get("jpg_path")  # made on first fetch
        assert again.split("/public/")[1].split(".")[0] == url.split("/public/")[1].split(".")[0]  # same asset
        r = await client.get("http://test" + url.removeprefix("https://media.example.com"),
                             headers={"cf-connecting-ip": "203.0.113.9"})
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    finally:
        await drop_providers(prov["org_id"], "media_host")


async def test_cloudinary_signed_upload(prov, monkeypatch):
    await add_provider(prov["org_id"], "cloudinary", "media_host",
                       {"api_key": "123456", "api_secret": "shh", "cloud_name": "democloud"})
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["body"] = req.content
        return httpx.Response(200, json={"secure_url": "https://res.cloudinary.com/democloud/image/upload/v1/x.png"})

    monkeypatch.setattr(image_gen, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    try:
        async with tenant(prov["org_id"]) as s:
            a = await media.store_bytes(s, prov["org_id"], png_bytes(), "png", source="upload")
            url = await media.ensure_public_url(s, a)
            assert url.startswith("https://res.cloudinary.com/") and a.public_url == url
            assert await media.ensure_public_url(s, a) == url  # cached
            await s.commit()
        assert seen["url"] == "https://api.cloudinary.com/v1_1/democloud/image/upload"
        body = seen["body"]
        assert b'name="api_key"' in body and b"123456" in body and b'name="signature"' in body and b'name="timestamp"' in body
        assert b"shh" not in body  # the secret is never sent
    finally:
        await drop_providers(prov["org_id"], "media_host")


# ------------------------------------------------------------------ AI image providers (mocked HTTP)
async def _cfg(org_id, pid) -> ProviderConfig:
    async with tenant(org_id) as s:
        return await s.get(ProviderConfig, pid)


async def test_image_provider_request_shapes(prov, monkeypatch):
    org = prov["org_id"]
    img = png_bytes(32, 32)
    b64 = base64.b64encode(img).decode()
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request):
        calls.append(req)
        u = str(req.url)
        if u.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": b64}]})
        if ":generateContent" in u:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}, {"inlineData": {"mimeType": "image/png", "data": b64}}]}}]})
        if "hf-inference" in u:
            return httpx.Response(200, content=img, headers={"content-type": "image/png"})
        if "stable-image/generate" in u:
            return httpx.Response(200, content=img, headers={"content-type": "image/png"})
        if u.endswith("/sdapi/v1/txt2img"):
            return httpx.Response(200, json={"images": [b64]})
        if u.endswith("/prompt"):
            return httpx.Response(200, json={"prompt_id": "p1"})
        if "/history/p1" in u:
            return httpx.Response(200, json={"p1": {"status": {"completed": True}, "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}})
        if "/view" in u:
            return httpx.Response(200, content=img, headers={"content-type": "image/png"})
        return httpx.Response(404, json={"error": "nope"})

    monkeypatch.setattr(image_gen, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    wf = json.dumps({"3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}"}},
                     "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{width}}", "height": "{{height}}"}},
                     "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
                     "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}"}}})
    specs = [
        ("openai_images", {"api_key": "sk-1"}, {"default_model": "gpt-image-1"}),
        ("gemini_images", {"api_key": "g-1"}, {}),
        ("hf_images", {"api_key": "hf-1"}, {}),
        ("stability", {"api_key": "st-1"}, {"default_model": "core"}),
        ("a1111", {}, {}),
        ("comfyui", {"workflow_json": wf}, {}),
    ]
    ids = {p: await add_provider(org, p, "image", sec, st) for p, sec, st in specs}
    try:
        for p, _, _ in specs:
            cfg = await _cfg(org, ids[p])
            fn = image_gen.STYLES[image_gen._style(cfg)]
            calls.clear()
            raw = await fn(cfg, image_gen._default_model(cfg), 'a "calm" office', "text, watermark", 1080, 1350, 7)
            assert raw[:4] == b"\x89PNG", p
            req = calls[0]
            if p == "openai_images":
                assert str(req.url) == "https://api.openai.com/v1/images/generations"
                assert req.headers["authorization"] == "Bearer sk-1"
                body = json.loads(req.content)
                assert body["model"] == "gpt-image-1" and body["size"] == "1024x1536" and "response_format" not in body
            elif p == "gemini_images":
                assert str(req.url).endswith("/models/gemini-2.5-flash-image:generateContent")
                assert req.headers["x-goog-api-key"] == "g-1"
                body = json.loads(req.content)
                assert body["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]
                assert body["generationConfig"]["imageConfig"]["aspectRatio"] == "4:5"
            elif p == "hf_images":
                assert str(req.url) == "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
                assert req.headers["authorization"] == "Bearer hf-1"
                assert json.loads(req.content)["inputs"] == 'a "calm" office'
            elif p == "stability":
                assert str(req.url) == "https://api.stability.ai/v2beta/stable-image/generate/core"
                assert req.headers["accept"] == "image/*" and b'name="aspect_ratio"' in req.content and b"4:5" in req.content
            elif p == "a1111":
                body = json.loads(req.content)
                assert str(req.url) == "http://127.0.0.1:7860/sdapi/v1/txt2img"
                assert body["width"] % 8 == 0 and body["height"] > body["width"] and body["seed"] == 7
            elif p == "comfyui":
                body = json.loads(req.content)["prompt"]
                assert body["6"]["inputs"]["text"] == 'a "calm" office'  # JSON-escaped substitution
                assert body["3"]["inputs"]["seed"] == 7 and isinstance(body["5"]["inputs"]["width"], int)
                assert any("/view" in str(c.url) for c in calls)
        # full generate(): chain with a failing first provider, cover-fit output, usage rows
        for p in ("gemini_images", "hf_images", "stability", "a1111", "comfyui"):
            await drop_providers_by_id(org, ids[p])
        await add_provider(org, "xai_images", "image", {"api_key": "x"}, {"default_model": "grok-2-image"},
                           base_url="https://broken.example.com/v1")
        async with tenant(org) as s:
            chain = await image_gen.image_chain(s)
            assert [c.provider for c, _ in chain] == ["openai_images", "xai_images"]
            res = await image_gen.generate(s, "office", 600, 400)
            assert Image.open(io.BytesIO(res.data)).size == (600, 400) and res.provider == "openai_images"
            await s.commit()
            n = await s.scalar(select(LlmUsage).where(LlmUsage.kind == "image", LlmUsage.ok.is_(True)))
            assert n is not None
    finally:
        await drop_providers(org, "image")


async def drop_providers_by_id(org_id, pid) -> None:
    async with tenant(org_id) as s:
        c = await s.get(ProviderConfig, pid)
        if c:
            await s.delete(c)
        await s.commit()


async def test_image_fallback_chain_and_prompt_llm(prov, monkeypatch):
    org = prov["org_id"]
    img = png_bytes(16, 16)

    def handler(req: httpx.Request):
        if "bad.example" in str(req.url):
            return httpx.Response(500, json={"error": {"message": "down"}})
        return httpx.Response(200, content=img, headers={"content-type": "image/png"})

    async def fake_json(session, task, prompt, **kw):
        assert task == "image_prompt" and "Brief:" in prompt
        return {"prompt": "sunlit modern office, people collaborating", "negative": "clutter"}

    monkeypatch.setattr(image_gen, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr("app.services.llm.router.complete_json", fake_json)
    await add_provider(org, "hf_images", "image", {"api_key": "a"}, base_url="https://bad.example.com/models")
    await add_provider(org, "hf_images", "image", {"api_key": "b"})
    try:
        async with tenant(org) as s:
            prompt, negative = await image_gen.build_prompt(s, "Our new HR assistant")
            assert prompt.startswith("sunlit modern office") and "no text" in prompt and "clutter" in negative
            res = await image_gen.generate(s, prompt, 300, 300, negative=negative)
            assert Image.open(io.BytesIO(res.data)).size == (300, 300)
            await s.commit()
            rows = (await s.scalars(select(LlmUsage).where(LlmUsage.kind == "image").order_by(LlmUsage.created_at))).all()
            assert [r.ok for r in rows][-2:] == [False, True]
    finally:
        await drop_providers(org, "image")


# ------------------------------------------------------------------ stock + tts (mocked)
async def test_stock_search_and_import(prov, monkeypatch):
    org = prov["org_id"]
    img = jpg_bytes(80, 60)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        u = str(req.url)
        if "/v1/search" in u:
            return httpx.Response(200, json={"photos": [{"id": 11, "width": 80, "height": 60, "url": "https://www.pexels.com/photo/11",
                                                         "photographer": "Asha", "photographer_url": "https://pexels.com/@asha",
                                                         "alt": "team", "src": {"original": "https://images.pexels.com/11.jpg", "medium": "m"}}]})
        if "/v1/photos/11" in u:
            return httpx.Response(200, json={"id": 11, "width": 80, "height": 60, "url": "https://www.pexels.com/photo/11",
                                             "photographer": "Asha", "src": {"original": "https://images.pexels.com/11.jpg"}})
        if u == "https://images.pexels.com/11.jpg":
            return httpx.Response(200, content=img, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    monkeypatch.setattr(image_gen, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    await add_provider(org, "pexels", "stock", {"api_key": "px"})
    try:
        async with tenant(org) as s:
            res = await stock.search(s, "team meeting", "photo", "portrait")
            assert res["providers"] == ["pexels"] and res["items"][0]["attribution"] == "Photo by Asha on Pexels"
            assert seen[0].headers["authorization"] == "px" and seen[0].url.params["orientation"] == "portrait"
            a = await stock.import_item(s, "pexels", "photo", "11", query="team meeting")
            assert a.source == "stock:pexels" and a.meta["attribution"] and a.width == 80
            await s.commit()
    finally:
        await drop_providers(org, "stock")


async def test_openai_tts_request(prov, monkeypatch, tmp_path):
    org = prov["org_id"]
    tone = await _tone(tmp_path / "t.m4a", 1.0)
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, content=tone.read_bytes(), headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(image_gen, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    await add_provider(org, "openai_tts", "tts", {"api_key": "sk"}, {"default_model": "tts-1"})
    try:
        async with tenant(org) as s:
            r = await tts.synthesize(s, "Hello there team", tmp_path / "o.mp3", voice="nova")
            assert seen["url"] == "https://api.openai.com/v1/audio/speech" and seen["body"]["voice"] == "nova"
            assert abs(r.duration - 1.0) < 0.2 and len(r.words) == 3 and r.provider == "openai_tts"
            await s.commit()
    finally:
        await drop_providers(org, "tts")


# ------------------------------------------------------------------ generate_for_post (no providers -> gradient)
@pytest.fixture
def no_llm(monkeypatch):
    async def fake_json(*a, **k):
        raise AssertionError("LLM must not be needed without an image provider")

    monkeypatch.setattr("app.services.llm.router.complete_json", fake_json)


async def _gen(org_id, **post_kw) -> list[MediaAsset]:
    async with tenant(org_id) as s:
        post = Post(organization_id=org_id, **post_kw)
        s.add(post)
        await s.flush()
        assets = await media.generate_for_post(s, post, await media.load_brand(s))
        await s.commit()
        return assets


async def test_generate_image_post(hrp, no_llm):
    await renderer_ok()
    assets = await _gen(hrp["org_id"], platform="instagram", post_type="image", title="Hire *faster* with AI",
                        meta={"image_brief": "recruiter reviewing CVs", "alt_text": "Headline about hiring faster"})
    assert len(assets) == 1
    a = assets[0]
    assert (a.width, a.height) == (1080, 1350) and a.mime == "image/png" and a.source == "template:photo_headline"
    assert a.alt_text == "Headline about hiring faster" and a.meta["background"] == "gradient"
    assert media.storage.abs_path(a.path).exists()


async def test_generate_carousel_and_document(hrp, no_llm):
    await renderer_ok()
    slides = [{"heading": f"Step {i}", "body": "Do the thing that matters most."} for i in range(1, 10)]
    assets = await _gen(hrp["org_id"], platform="linkedin_org", post_type="carousel", title="Nine steps", meta={"slides": slides})
    assert 3 <= len(assets) <= 8 and len(assets) == 8  # capped: cover + 6 + CTA
    assert all((a.width, a.height) == (1200, 1200) for a in assets)
    assert [a.meta["index"] for a in assets] == list(range(1, 9)) and len({a.meta["group"] for a in assets}) == 1
    docs = await _gen(hrp["org_id"], platform="linkedin_org", post_type="document", title="Guide", meta={"slides": slides[:3]})
    d = docs[0]
    assert d.kind == "document" and d.mime == "application/pdf" and d.meta["pages"] == 5
    assert media.storage.abs_path(d.path).read_bytes()[:5] == b"%PDF-"
    assert media.asset_out(d)["thumb_url"].endswith("&v=poster")


async def test_generate_gif_and_animation(hrp, no_llm):
    await renderer_ok()
    gifs = await _gen(hrp["org_id"], platform="facebook_page", post_type="gif", title="Big news", text="We launched. Try it today.")
    g = gifs[0]
    assert g.kind == "gif" and g.mime == "image/gif" and g.size_bytes <= 8 * 1024 * 1024 and g.width <= 720
    assert media.asset_file(g, "mp4")[0] is not None and "mp4_url" in media.asset_out(g)
    anims = await _gen(hrp["org_id"], platform="instagram", post_type="animation", title="Moving brand")
    v = anims[0]
    assert v.kind == "video" and v.mime == "video/mp4" and (v.width, v.height) == (1080, 1350)
    info = await media_render.probe(media.storage.abs_path(v.path))
    assert info["vcodec"] == "h264" and abs(info["duration"] - 2.0) < 0.3


async def test_generate_video_post(hrp, no_llm, monkeypatch, tmp_path):
    await renderer_ok()
    monkeypatch.setattr(media, "VIDEO_MIN_S", 6.0)
    monkeypatch.setattr(media, "VIDEO_FPS", 12)

    async def fake_tts(session, text, out_path, voice=None, **kw):
        await media_render.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=1.0", "-c:a", "aac", str(out_path.with_suffix(".m4a"))])
        out_path.with_suffix(".m4a").rename(out_path)
        return tts.TtsResult(out_path, 1.0, "edge_tts", voice or tts.DEFAULT_VOICE, [])

    monkeypatch.setattr(tts, "synthesize", fake_tts)
    assets = await _gen(hrp["org_id"], platform="youtube", post_type="video", title="Quick tip",
                        meta={"size": "360x640", "video_script": [{"caption": "Save *hours* each week", "voiceover": "Save hours."},
                                                                   {"caption": "Automate screening", "voiceover": "Automate it."}]})
    v = assets[0]
    info = await media_render.probe(media.storage.abs_path(v.path))
    assert info["vcodec"] == "h264" and info["has_audio"] and (info["width"], info["height"]) == (360, 640)
    assert 5.5 <= info["duration"] <= 12 and v.meta["voice"] == tts.DEFAULT_VOICE and v.meta["scenes"] == 2
    assert v.meta["visual_sources"] == ["gradient", "gradient"]


async def test_text_posts_have_no_media(hrp):
    assert await _gen(hrp["org_id"], platform="x", post_type="text", text="hi") == []


# ------------------------------------------------------------------ API: templates, render, jobs, isolation
async def test_templates_render_and_generate_job(client, hrp, no_llm):
    await renderer_ok()
    from app.services.jobs import run_inline

    h = auth(hrp["tokens"])
    r = await client.get("/media/templates", headers=h)
    assert r.status_code == 200 and any(t["name"] == "stat_card" for t in r.json()["templates"])
    r = await client.post("/media/render", headers=h, json={"template": "stat_card", "size": "600x600",
                                                            "fields": {"stat": "3x", "stat_label": "faster hiring"}})
    assert r.status_code == 200, r.text
    assert r.json()["width"] == 600 and r.json()["source"] == "template:stat_card"
    assert (await client.post("/media/render", headers=h, json={"template": "nope"})).status_code == 400

    async with tenant(hrp["org_id"]) as s:
        post = Post(organization_id=hrp["org_id"], platform="instagram", post_type="image", title="Post", media_ids=[])
        s.add(post)
        await s.commit()
    r = await client.post("/media/generate", headers=h, json={"kind": "image", "platform": "instagram", "post_id": str(post.id),
                                                              "fields": {"headline": "Studio headline", "template": "announcement"}})
    assert r.status_code == 202, r.text
    from app.core.db import platform_session
    from app.models import Job

    async with platform_session() as s:
        job = await s.get(Job, uuid.UUID(r.json()["job_id"]))
        assert job.kind == "media.generate" and job.organization_id == hrp["org_id"]
        payload = job.payload
    import app.modules.media  # noqa: F401 - registers the handler

    res = await run_inline("media.generate", hrp["org_id"], payload)
    assert len(res["asset_ids"]) == 1
    async with tenant(hrp["org_id"]) as s:
        p = await s.get(Post, post.id)
        assert p.media_ids == res["asset_ids"]
        a = await s.get(MediaAsset, uuid.UUID(res["asset_ids"][0]))
        assert a.source == "template:announcement" and (a.width, a.height) == (1080, 1350)
    r = await client.post("/media/generate", headers=h, json={"kind": "podcast"})
    assert r.status_code == 400
    r = await client.post("/media/ai-image", headers=h, json={"prompt": "a calm office"})
    assert r.status_code == 409  # no image provider in this org


async def test_tenant_isolation(client, hrp, other):
    async with tenant(hrp["org_id"]) as s:
        a = await media.store_bytes(s, hrp["org_id"], png_bytes(), "png", source="upload", alt_text="hrp-only-asset")
        await s.commit()
    ho = auth(other["tokens"])
    r = await client.get("/media", headers=ho, params={"search": "hrp-only-asset"})
    assert r.status_code == 200 and r.json()["total"] == 0
    assert (await client.get(f"/media/{a.id}", headers=ho)).status_code == 404
    assert (await client.delete(f"/media/{a.id}", headers=ho)).status_code == 404
    # a valid signature for the other org can't reach this org's file (RLS)
    forged = media_url(a.id, other["org_id"])
    assert (await client.get("http://test" + forged)).status_code == 404
    # swapping the org in a real link breaks the signature
    real = media_url(a.id, hrp["org_id"]).replace(str(hrp["org_id"]), str(other["org_id"]))
    assert (await client.get("http://test" + real)).status_code == 403
    async with tenant(other["org_id"]) as s:
        assert await s.get(MediaAsset, a.id) is None


async def test_capabilities_and_voices(client, hrp, monkeypatch):
    async def no_net():
        raise OSError("offline")

    import edge_tts

    monkeypatch.setattr(edge_tts, "list_voices", no_net)
    tts._VOICES.update(items=[], at=0)
    h = auth(hrp["tokens"])
    caps = (await client.get("/media/capabilities", headers=h)).json()
    assert caps["image"] is False and caps["stock"] == [] and "edge_tts" in caps["tts"]
    v = (await client.get("/media/voices", headers=h)).json()
    assert v["default"] == "en-IN-NeerjaNeural" and any(x["id"] == "hi-IN-SwaraNeural" for x in v["voices"])
