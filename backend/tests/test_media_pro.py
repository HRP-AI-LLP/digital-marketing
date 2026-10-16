"""Professional media system: design kit, layouts, automatic selection, occasions, deterministic motion runtime,
GIF loops, 3D scenes (headless WebGL + 2D fallback), .glb uploads, gallery previews, variants, attach, shorts."""

import io
import json
import re
import struct
import uuid

import pytest
import pytest_asyncio
from PIL import Image, ImageChops, ImageStat

from app.core.config import get_settings
from app.models import MediaAsset, Post
from app.services import content, media, media_render, tts
from app.services.media_templates import TEMPLATES, BrandStyle, default_logo_uri, registry, render_html
from app.services.media_templates import select as tsel
from app.services.media_templates.kit import Kit, words
from app.services.media_templates.occasions import OCCASIONS, match_occasion
from app.services.media_templates.three_d import demo_glb
from tests.conftest import auth, login
from tests.test_media import _gen, no_llm, renderer_ok, tenant  # noqa: F401

ADMIN_PW = "Str0ngPassw0rd!"


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def media_env(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage-pro"))
    old_motion = json.loads(json.dumps(media.MOTION))
    old_3d = dict(media.THREE_D)
    media.MOTION["gif"].update(seconds=2.0, fps=10, max_width=320)
    media.MOTION["animation"].update(seconds=2.0, fps=10)
    media.THREE_D.update(clip_seconds=1.0, fps=8, quality=0.4)
    yield
    media.MOTION.update(old_motion)
    media.THREE_D.update(old_3d)
    s.storage_dir = old
    await media_render.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def hrp(client, admin):
    t = await login(client, "admin@hrpai.in", ADMIN_PW)
    return {"tokens": t, "org_id": uuid.UUID(t["organization"]["id"])}


def brand() -> BrandStyle:
    return BrandStyle(logo_uri=default_logo_uri(), website="hrpai.in")


def _img(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def _not_blank(png: bytes) -> bool:
    return len(set(_img(png).resize((24, 24)).getdata())) > 12


# ------------------------------------------------------------------ registry, HTML safety, design rules
async def test_registry_covers_layouts_occasions_and_3d():
    names = {t["name"] for t in registry()}
    for n in ("hook_bold", "data_chart", "feature_spotlight", "steps_card", "myth_fact", "event_card", "testimonial", "logo_wall",
              "hiring", "carousel_cover", "carousel_slide", "carousel_cta", "animated_text"):
        assert n in names
    assert {f"greeting_{k}" for k in OCCASIONS} <= names
    assert {f"scene3d_{k}" for k in ("logo", "cards", "neural", "globe", "particles", "model")} <= names
    for t in registry():
        assert t["group"] and t["themes"]


async def test_every_template_escapes_and_loads_nothing_external():
    evil = "</script><script>alert(1)</script> & *bold*"
    for name, spec in TEMPLATES.items():
        for motion in ("still", "loop"):
            html = render_html(name, {"headline": evil, "quote": evil, "caption": evil, "heading": evil, "lines": [evil], "body": evil,
                                      "bullets": [evil, "two", "three"], "stat": "42%", "names": [evil, "b", "c"]},
                               1080, 1350, brand(), motion=motion, duration=3)
            assert "<script>alert(1)" not in html, name
            assert not re.search(r"<(script|link|img|iframe)[^>]+(src|href)=['\"]?https?://", html, re.I), name
            assert "url(http" not in html and "@import" not in html, name
            assert "window.__seek" in html or "__runtime" in html, name  # every template implements the seek contract


async def test_type_scale_safe_areas_and_formats():
    b = brand()
    sq, story, wide = Kit(1080, 1080, b), Kit(1080, 1920, b), Kit(1920, 1080, b)
    assert sq.fmt == "square" and story.fmt == "story" and wide.fmt == "wide" and Kit(1000, 1500, b).fmt == "pin"
    assert story.pads()[0] >= 1920 * 0.1 and story.pads()[2] >= 1920 * 0.14  # Stories/Reels UI zones stay clear
    assert sq.s == 1.0 and 1.0 < story.s <= 1.15 and story.boost > 1
    html = render_html("hook_bold", {"headline": "x"}, 1080, 1080, b)
    assert "text-wrap:balance" in html and "Plus Jakarta Sans" in html and "--g:8.000px" in html


async def test_kinetic_words_markup():
    out = words("Still screening CVs *by hand*? <b>")
    assert out.count("class='w'") == 6 and "hand?</span>" in out and "&lt;b&gt;" in out
    assert "wi hl" in out


# ------------------------------------------------------------------ selection and occasions
async def test_automatic_layout_selection_and_variety():
    assert tsel.choose({"stat": "73%"}) == "stat_card"
    assert tsel.choose({"bullets": ["2023: 10", "2024: 20", "2025: 31"]}) == "data_chart"
    assert tsel.choose({"bullets": ["Import: a", "Configure: b", "Launch: c"]}, title="How to set up payroll") == "steps_card"
    assert tsel.choose({"quote": "Great"}, title="Client story from Acme") in ("quote_card", "testimonial")
    assert tsel.choose({}, title="We're hiring a Senior ML Engineer") == "hiring"
    assert tsel.choose({}, title="Join our live webinar on RAG chatbots") == "event_card"
    assert tsel.choose({}, title="Introducing the new HRP AI agent", text="Now live for every customer.") == "announcement"
    assert tsel.choose({"template": "quote_card", "stat": "9%"}) == "quote_card"  # explicit choice wins
    assert tsel.choose({"visual": {"layout": "myth_fact"}}) == "myth_fact"
    first = tsel.choose({}, title="Hire faster with AI", photo_ok=True)
    assert tsel.choose({}, title="Hire faster with AI", photo_ok=True, recent=[first]) != first  # no back-to-back repeats
    assert len(set(tsel.ranked({}, title="Hire faster with AI", n=3))) == 3
    assert tsel.choose({}, title="Hire faster", kind="gif") == "animated_text"


async def test_occasion_matching():
    assert match_occasion({"name": "Diwali", "category": "festival", "date": "2026-11-08"}) == "diwali"
    assert match_occasion({"name": "Eid al-Adha", "category": "festival"}) == "eid"
    assert match_occasion({"name": "Republic Day", "category": "national"}) == "republic_day"
    assert match_occasion({"name": "Bihu", "category": "festival"}) == "pongal"
    assert match_occasion({"name": "Some Local Fair", "category": "festival"}) == "festive"
    assert match_occasion({"name": "World Mental Health Day", "category": "global_day"}) == "awareness"
    assert match_occasion({"name": "National Engineers' Day", "category": "industry_day"}) == "tech_day"
    assert match_occasion(None, "Wishing you a very Happy Onam from all of us!") == "onam"
    assert match_occasion(None, "Our Diwali sale analysis shows 30% growth") is None  # mentions, not greetings
    # the occasion from the calendar beats the writer's layout hint
    assert tsel.choose({"occasion": {"name": "Christmas"}, "visual": {"layout": "hook_bold"}}) == "greeting_christmas"
    # national days never draw the flag or emblem: abstract tricolour only
    html = render_html("greeting_independence_day", {}, 1080, 1080, brand())
    assert "#ff9933" in html and "#138808" in html and "chakra" not in html.lower() and "emblem" not in html.lower()


# ------------------------------------------------------------------ rendering
async def test_layouts_render_in_all_main_formats():
    await renderer_ok()
    sizes = [(1080, 1080), (1080, 1350), (1080, 1920), (1920, 1080), (1000, 1500)]
    for name in ("stat_card", "feature_spotlight", "greeting_diwali"):
        for w, h in sizes:
            sw, sh = w // 3, h // 3
            png = await media_render.render_png(render_html(name, dict(TEMPLATES[name].sample), sw, sh, brand()), sw, sh)
            assert _img(png).size == (sw, sh) and _not_blank(png), (name, w, h)


async def test_text_never_overflows_its_box():
    await renderer_ok()
    long = "An unusually long headline that keeps going " * 6
    html = render_html("hook_bold", {"headline": long, "body": long}, 540, 540, brand())
    async with media_render._page(540, 540) as page:
        await media_render._load(page, html, 20)
        over = await page.evaluate("() => [...document.querySelectorAll('[data-fitbox]')].some(b => b.scrollHeight > b.clientHeight + 2)")
    assert over is False


async def test_seek_is_deterministic_and_intro_differs_from_still():
    await renderer_ok()
    html = render_html("stat_card", dict(TEMPLATES["stat_card"].sample), 360, 450, brand(), motion="intro", duration=4)
    a = await media_render.render_png(html, 360, 450, at=0.6)
    b = await media_render.render_png(html, 360, 450, at=0.6)
    still = await media_render.render_png(html, 360, 450)
    assert a == b
    assert ImageChops.difference(_img(a), _img(still)).getbbox() is not None


async def test_gif_loop_is_seamless_and_small(tmp_path):
    await renderer_ok()
    html = render_html("tip_card", dict(TEMPLATES["tip_card"].sample), 320, 400, brand(), motion="loop", duration=2.0)
    start = await media_render.render_png(html, 320, 400, at=0.0)
    end = await media_render.render_png(html, 320, 400, at=2.0)
    assert start == end  # t = duration is exactly the first frame again: the loop has no seam
    assert start == await media_render.render_png(html, 320, 400)  # the first frame is the composed still (good thumbnail)
    mp4 = await media_render.render_video(html, 320, 400, 2.0, tmp_path / "l.mp4", fps=10)
    gif = await media_render.mp4_to_gif(mp4, tmp_path / "l.gif", max_width=320, fps=10, max_seconds=2.0)
    assert gif.read_bytes()[:6] == b"GIF89a" and gif.stat().st_size < 8 * 1024 * 1024


# ------------------------------------------------------------------ 3D
async def test_3d_scene_renders_or_falls_back(hrp, no_llm, monkeypatch):
    await renderer_ok()
    if await media.webgl_ok():
        png = await media.render_3d_png("scene3d_neural", {"headline": "Agents"}, 320, 400, brand())
        assert _img(png).size == (320, 400) and _not_blank(png)
    assets = await _gen(hrp["org_id"], platform="instagram", post_type="image", title="3D",
                        meta={"template": "scene3d_globe", "size": "360x450"})
    assert assets[0].meta["template"] in ("scene3d_globe", "hook_bold")

    async def broken(*a, **k):
        raise media_render.RenderError("Scene failed to render: WebGL unavailable")

    monkeypatch.setattr(media, "render_3d_png", broken)
    fb = await _gen(hrp["org_id"], platform="instagram", post_type="image", title="3D fallback",
                    meta={"template": "scene3d_logo", "size": "360x450"})
    assert fb[0].meta["fallback_from"] == "scene3d_logo" and fb[0].source == "template:animated_text"


def _glb_with_external_uri() -> bytes:
    doc = json.dumps({"asset": {"version": "2.0"}, "meshes": [{"primitives": []}], "buffers": [{"uri": "https://evil.example/x.bin",
                                                                                                "byteLength": 4}]}).encode()
    doc += b" " * ((4 - len(doc) % 4) % 4)
    return struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(doc)) + struct.pack("<II", len(doc), 0x4E4F534A) + doc


async def test_model_upload_validation_and_turntable(client, hrp):
    await renderer_ok()
    h = auth(hrp["tokens"])
    r = await client.post("/media/upload", headers=h, files={"files": ("knot.glb", demo_glb(), "model/gltf-binary")})
    assert r.status_code == 201, r.text
    item = r.json()["items"][0]
    assert item["kind"] == "model3d" and item["mime"] == "model/gltf-binary" and item["meta"]["meshes"] == 1
    for bad, msg in [(_glb_with_external_uri(), "external"), (demo_glb()[:-40], "incomplete"), (b"glTF" + b"\x01" * 30, "2.0")]:
        r = await client.post("/media/upload", headers=h, files={"files": ("m.glb", bad, "model/gltf-binary")})
        assert r.status_code == 400 and msg in r.json()["detail"], r.text
    # turntable job
    from app.services.jobs import run_inline
    import app.modules.media  # noqa: F401

    r = await client.post("/media/3d", headers=h, json={"scene": "model", "output": "image", "size": "320x320", "model_asset_id": item["id"]})
    assert r.status_code == 202, r.text
    r2 = await client.post("/media/3d", headers=h, json={"scene": "model", "output": "image"})
    assert r2.status_code == 400
    res = await run_inline("media.generate", hrp["org_id"], {"kind": "3d", "scene": "model", "output": "image", "fields": {},
                                                             "size": "320x320", "model_asset_id": item["id"]})
    async with tenant(hrp["org_id"]) as s:
        a = await s.get(MediaAsset, uuid.UUID(res["asset_ids"][0]))
        assert a.kind == "image" and (a.width, a.height) == (320, 320)


async def test_3d_clip_job(hrp):
    await renderer_ok()
    from app.services.jobs import run_inline
    import app.modules.media  # noqa: F401

    res = await run_inline("media.generate", hrp["org_id"], {"kind": "3d", "scene": "particles", "output": "video", "fields": {},
                                                             "size": "320x568", "seconds": 1.0})
    async with tenant(hrp["org_id"]) as s:
        a = await s.get(MediaAsset, uuid.UUID(res["asset_ids"][0]))
        info = await media_render.probe(media.storage.abs_path(a.path))
        assert a.kind == "video" and info["vcodec"] == "h264" and (info["width"], info["height"]) == (320, 568)


# ------------------------------------------------------------------ studio API: previews, variants, attach
async def test_gallery_previews_are_signed_and_cached(client, hrp):
    await renderer_ok()
    h = auth(hrp["tokens"])
    r = await client.get("/media/templates", headers=h, params={"size": "1080x1080"})
    data = r.json()
    t = next(x for x in data["templates"] if x["name"] == "quote_card")
    assert data["scenes3d"] and data["occasions"] and "1080x1920" in data["preview_sizes"]
    r1 = await client.get("http://test" + t["preview_url"])
    assert r1.status_code == 200 and r1.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r1.content)).size[0] == 480
    r2 = await client.get("http://test" + t["preview_url"])
    assert r2.content == r1.content  # cached
    bad = t["preview_url"].replace("quote_card", "stat_card")
    assert (await client.get("http://test" + bad)).status_code == 403


async def test_variants_and_attach(client, hrp, no_llm):
    await renderer_ok()
    from app.core.db import platform_session
    from app.models import Job
    from app.services.jobs import run_inline
    import app.modules.media  # noqa: F401

    h = auth(hrp["tokens"])
    async with tenant(hrp["org_id"]) as s:
        post = Post(organization_id=hrp["org_id"], platform="linkedin_org", post_type="image", title="Variants", media_ids=[], status="draft")
        s.add(post)
        await s.commit()
    r = await client.post("/media/generate", headers=h, json={"kind": "image", "platform": "linkedin_org", "size": "400x400",
                                                              "variants": 3, "post_id": str(post.id),
                                                              "fields": {"headline": "Payroll in *minutes*", "bullets": ["One", "Two", "Three"]}})
    assert r.status_code == 202, r.text
    async with platform_session() as s:
        payload = (await s.get(Job, uuid.UUID(r.json()["job_id"]))).payload
    res = await run_inline("media.generate", hrp["org_id"], payload)
    assert len(res["asset_ids"]) == 3 and res["variants"]
    async with tenant(hrp["org_id"]) as s:
        rows = [await s.get(MediaAsset, uuid.UUID(i)) for i in res["asset_ids"]]
        assert len({a.meta["template"] for a in rows}) == 3 and len({a.meta["variant_group"] for a in rows}) == 1
        assert (await s.get(Post, post.id)).media_ids == []  # options aren't attached automatically
    r = await client.post("/media/attach", headers=h, json={"post_id": str(post.id), "asset_ids": [res["asset_ids"][1]]})
    assert r.status_code == 200 and r.json()["media_ids"] == [res["asset_ids"][1]]
    assert (await client.post("/media/generate", headers=h, json={"kind": "video", "variants": 3, "fields": {"body": "x"}})).status_code == 400


async def test_greeting_post_uses_the_occasion(hrp, no_llm):
    await renderer_ok()
    assets = await _gen(hrp["org_id"], platform="instagram", post_type="image", title="Festive greetings",
                        text="Wishing you light and joy this festive season. Team HRP AI",
                        meta={"occasion": {"name": "Diwali", "category": "festival", "date": "2026-11-08"}, "size": "360x450"})
    a = assets[0]
    assert a.source == "template:greeting_diwali"
    gifs = await _gen(hrp["org_id"], platform="facebook_page", post_type="gif", title="Holi",
                      meta={"occasion": {"name": "Holi", "category": "festival"}})
    assert gifs[0].source == "template:greeting_holi" and gifs[0].kind == "gif"


# ------------------------------------------------------------------ short video
async def test_short_video_has_synced_subtitles_and_end_card(hrp, no_llm, monkeypatch):
    await renderer_ok()
    monkeypatch.setattr(media, "VIDEO_MIN_S", 5.0)
    monkeypatch.setattr(media, "VIDEO_FPS", 10)

    async def fake_tts(session, text, out_path, voice=None, **kw):
        await media_render.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=300:duration=1.2", "-c:a", "aac", str(out_path.with_suffix(".m4a"))])
        out_path.with_suffix(".m4a").rename(out_path)
        return tts.TtsResult(out_path, 1.2, "edge_tts", tts.DEFAULT_VOICE, [tts.Word(0.0, 0.5, "Hire"), tts.Word(0.5, 1.1, "faster.")])

    monkeypatch.setattr(tts, "synthesize", fake_tts)
    assets = await _gen(hrp["org_id"], platform="youtube", post_type="video", title="Shorts",
                        meta={"size": "270x480", "cta": "Book a demo",
                              "video_script": [{"caption": "Still *screening* by hand?", "voiceover": "Hire faster."},
                                               {"caption": "AI ranks in seconds", "voiceover": "Hire faster."},
                                               {"caption": "You decide", "voiceover": "Hire faster."}]})
    v = assets[0]
    info = await media_render.probe(media.storage.abs_path(v.path))
    assert info["vcodec"] == "h264" and info["has_audio"] and (info["width"], info["height"]) == (270, 480)
    assert v.meta["subtitles"] is True and v.meta["template"] == "short" and v.meta["scenes"] == 3
    assert 5.0 <= info["duration"] <= 14


# ------------------------------------------------------------------ content engine hints
async def test_writer_visual_hints_are_sanitised():
    d = content.normalize_draft({"text": "Body", "visual": {"layout": "stat_card", "stat": "68%", "evil": "<x>", "bullets": ["a", 2, {"x": 1}],
                                                            "headline": "x" * 500}}, "linkedin_org", "image")
    assert d["visual"]["layout"] == "stat_card" and d["visual"]["bullets"] == ["a", "2"] and "evil" not in d["visual"]
    assert len(d["visual"]["headline"]) <= 220
    d2 = content.normalize_draft({"text": "Body", "visual": {"layout": "nope"}}, "linkedin_org", "image")
    assert "visual" not in d2
    assert "visual" not in content.normalize_draft({"text": "Body", "visual": {"stat": "1"}}, "linkedin_org", "text")
