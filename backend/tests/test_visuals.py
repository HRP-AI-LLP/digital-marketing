"""Brand → Visuals: no people in generated media, product demos, natural voice-over text, tech-news topics."""

import functools
import http.server
import json
import random
import threading
import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant
from app.models import BrandProfile, IdeaBrief, Job, MediaAsset, Post
from app.services import content as svc
from app.services import demos, image_gen, media_policy, media_render, stock, tts
from app.services.media_templates import BrandStyle
from tests.test_content import mk_account, mk_post, new_org


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def storage_env(tmp_path_factory):
    s = get_settings()
    old = s.storage_dir
    s.storage_dir = str(tmp_path_factory.mktemp("storage"))
    yield
    s.storage_dir = old
    await media_render.shutdown()


async def set_visuals(org_id, **media) -> None:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        b = await svc.get_brand(s, org_id)
        b.settings = {**(b.settings or {}), "media": media}
        await s.commit()


# ------------------------------------------------------------------ people
async def test_people_words_are_removed_and_detected():
    assert media_policy.strip_people("HR manager reviewing payroll on a laptop") == "reviewing payroll on a laptop"
    assert media_policy.strip_people("employees with managers") == media_policy.STOCK_SAFE
    assert media_policy.describes_people({"alt": "", "page_url": "https://www.pexels.com/video/a-woman-typing-on-a-laptop-856990/"})
    assert media_policy.describes_people({"alt": "business, team, meeting, office"})
    assert not media_policy.describes_people({"alt": "server room, data center, blue light"})
    p = media_policy.from_settings({})
    assert p.people == "none" and p.no_people and p.news_share == 20 and p.demos == []


async def test_blank_and_abstract_images_have_no_people():
    import io

    buf = io.BytesIO()
    arr = (np.linspace(0, 255, 640 * 480 * 3).reshape(480, 640, 3) % 255).astype("uint8")
    Image.fromarray(arr).save(buf, "PNG")
    assert media_policy.image_has_people(buf.getvalue()) is False
    assert media_policy._detectors() is not None  # OpenCV is installed, so the pixel check is active


async def test_stock_drops_people_results_and_checks_files(client, monkeypatch):
    o = await new_org(client)
    seen: dict = {}

    async def fake_search(session, query, type_, orientation=None, per_page=12, provider=None):
        seen["query"] = query
        return {"items": [
            {"provider": "pexels", "id": "1", "alt": "smiling woman at laptop", "page_url": ""},
            {"provider": "pexels", "id": "2", "alt": "laptop on desk", "page_url": ""},
            {"provider": "pexels", "id": "3", "alt": "server racks", "page_url": ""},
        ], "errors": [], "providers": ["pexels"]}

    async def fake_import(session, provider, type_, item_id, *, query=None, check_people=False):
        seen.setdefault("tried", []).append((item_id, check_people))
        if item_id == "2":
            raise stock.ShowsPeople("face in the photo")
        return MediaAsset(id=uuid.uuid4(), kind="image", source="stock:pexels", meta={"stock_id": item_id})

    monkeypatch.setattr(stock, "search", fake_search)
    monkeypatch.setattr(stock, "import_item", fake_import)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        a = await stock.find_image(s, "businessman reviewing payroll on laptop")
    assert seen["query"] == "reviewing payroll on laptop"
    assert seen["tried"] == [("2", True), ("3", True)]  # "smiling woman" never fetched; "2" rejected by the face check
    assert a.meta["stock_id"] == "3"

    await set_visuals(o["id"], people="allow")
    seen.clear()
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        a = await stock.find_image(s, "businessman reviewing payroll")
    assert seen["query"] == "businessman reviewing payroll" and seen["tried"] == [("1", False)]


async def test_ai_prompts_forbid_people_and_people_images_are_redrawn(client, monkeypatch):
    o = await new_org(client)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        prompt, negative = await image_gen.build_prompt(s, "A recruiter shaking hands with a candidate")
    assert "no people" in prompt and "recruiter" not in prompt and "faces" in negative

    calls = []

    async def fake_draw(cfg, model, prompt, negative, w, h, seed):
        calls.append(seed)
        return b"img"

    flags = iter([True, False])
    monkeypatch.setattr(media_policy, "image_has_people", lambda data: next(flags))
    monkeypatch.setattr(image_gen, "cover_fit", lambda raw, w, h: raw)

    class Cfg:
        id, name, provider, settings, organization_id, enabled = uuid.uuid4(), "Fake", "pollinations", {}, o["id"], True

    async def chain(session):
        return [(Cfg, "flux")]

    async def order(session, ch):
        return ch, []

    from app.services.llm import health, keys

    monkeypatch.setattr(image_gen, "image_chain", chain)
    monkeypatch.setattr(health, "order_chain", order)
    monkeypatch.setattr(image_gen, "_style", lambda cfg: "fake")
    monkeypatch.setitem(image_gen.STYLES, "fake", fake_draw)

    async def with_keys(cfg, fn):
        return await fn()

    async def ok(*a, **k):
        return None

    from app.services.llm import router

    async def no_budget(session, cfg):
        return False

    monkeypatch.setattr(router, "over_budget", no_budget)
    monkeypatch.setattr(keys, "with_keys", with_keys)
    monkeypatch.setattr(health, "record_success", ok)
    monkeypatch.setattr(image_gen, "_log_usage", lambda *a, **k: None)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        img = await image_gen.generate(s, "office", 64, 64, seed=10)
    assert img.data == b"img" and len(calls) == 2 and calls[1] != calls[0]  # drawn again with another seed


async def test_visuals_api_and_media_tags(client):
    o = await new_org(client)
    r = await client.get("/content/brand/visuals", headers=o["h"])
    assert r.status_code == 200 and r.json()["people"] == "none" and r.json()["news_share"] == 20
    r = await client.put("/content/brand/visuals", headers=o["h"], json={
        "people": "team", "news_share": 25,
        "demos": [{"product": "HRP HRMS", "keywords": ["HRMS", "payroll"], "urls": ["https://hrpai.in/products/hrms"]}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["people"] == "team" and d["demos"][0]["product"] == "HRP HRMS" and d["demos"][0]["screens"] == 0
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        j = await s.scalar(select(Job).where(Job.kind == "demos.capture", Job.organization_id == o["id"]))
    assert j is not None and j.payload == {"product": "HRP HRMS"}
    bad = await client.put("/content/brand/visuals", headers=o["h"], json={"demos": [{"product": "X", "urls": ["hrpai.in"]}]})
    assert bad.status_code == 422

    # team photo and demo tags in the Media library
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        a = MediaAsset(organization_id=o["id"], kind="image", mime="image/png", path="x.png", size_bytes=1, sha256="0" * 64,
                       source="upload", meta={})
        s.add(a)
        await s.commit()
        aid = a.id
    r = await client.patch(f"/media/{aid}", headers=o["h"], json={"team": True, "demo": "HRP HRMS"})
    assert r.status_code == 200 and r.json()["meta"] == {"team": True, "demo": "HRP HRMS"}
    assert (await client.get("/content/brand/visuals", headers=o["h"])).json()["team_photos"] == 1
    r = await client.patch(f"/media/{aid}", headers=o["h"], json={"team": False, "demo": ""})
    assert r.json()["meta"] == {}


# ------------------------------------------------------------------ product demos
@pytest.fixture
def demo_site(tmp_path):
    page = tmp_path / "index.html"
    rows = "".join(f"<section style='height:420px;background:hsl({i * 40},60%,55%)'><h2>Feature {i}</h2></section>" for i in range(8))
    page.write_text(f"<!doctype html><html><body style='margin:0;font-family:sans-serif'><h1>HRP HRMS demo</h1>{rows}</body></html>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


async def test_capture_demo_and_use_it_in_an_image_post(client, demo_site):
    try:
        await media_render.render_png("<html><body>ok</body></html>", 32, 32)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium unavailable: {e}")
    o = await new_org(client)
    await set_visuals(o["id"], demos=[{"product": "HRP HRMS", "keywords": ["payroll"], "urls": [demo_site]}])
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        pol = await media_policy.get(s)
        d = demos.find(pol, "Close payroll in a day")
        assert d is not None and demos.find(pol, "Our office party") is None
        items = await demos.capture(s, o["id"], d)
        await s.commit()
    shots = [a for a in items if a.kind == "image"]
    vids = [a for a in items if a.kind == "video"]
    assert len(shots) == 6 and len(vids) == 2  # 3 screenshots + 1 scroll-through, laptop and phone
    assert {a.meta["view"] for a in vids} == {"laptop", "phone"} and all(a.duration_s and a.duration_s > 4 for a in vids)

    acc = await mk_account(o["id"], platform="linkedin_org")
    post = await mk_post(o["id"], acc, status="in_review", post_type="image", text="Close payroll in a day with HRP HRMS.",
                         title="Payroll in a day")
    from app.services import media as media_svc

    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        p = await s.get(Post, post.id)
        style = BrandStyle(name="HRP AI")
        a = await media_svc._make_image(s, p, {"visual": {"headline": "Payroll in *a day*"}}, style, (1080, 1080))
        await s.commit()
    assert a.source == "template:demo" and a.meta["demo_of"] == "HRP HRMS"

    # capturing again retires the old captures (posts may still use them) and keeps the new ones
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        pol = await media_policy.get(s)
        await demos.capture(s, o["id"], pol.demos[0])
        await s.commit()
        now_items = await demos.assets(s, pol.demos[0])
    assert len(now_items) == 8 and not {x.id for x in items} & {x.id for x in now_items}


# ------------------------------------------------------------------ voice
async def test_speakable_text():
    t = tts.speakable("Meet HRP HRMS 🚀 — payroll & attendance in one place. Visit https://hrpai.in/products/hrms #HRTech")
    assert t == "Meet HRP HRMS, payroll and attendance in one place. Visit hrpai dot in HRTech"
    assert tts._edge_voice(None) == tts.DEFAULT_VOICE and tts._edge_voice("en-IN-NeerjaNeural") == tts.DEFAULT_VOICE
    assert tts._edge_voice("en-GB-RyanNeural") == "en-GB-RyanNeural"


async def test_script_is_spoken_in_one_take_and_cut_per_scene(tmp_path, monkeypatch):
    """One natural take, cut at the scene boundaries using the word timings ("in 2026" arrives as one timing)."""
    full = tmp_path / "vo_full.mp3"
    # the bundled ffmpeg (imageio-ffmpeg), like the app: CI machines have no system ffmpeg
    await media_render.run_ffmpeg(["-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=4", str(full)])
    W = tts.Word
    words = [W(0.1, 0.4, "Payroll"), W(0.4, 0.6, "took"), W(0.6, 0.9, "days"), W(1.3, 1.5, "Now"), W(1.5, 1.8, "it's"),
             W(1.8, 2.1, "done"), W(2.1, 2.6, "in 2026"), W(3.0, 3.3, "See"), W(3.3, 3.8, "hrpai")]
    calls = []

    async def fake(session, text, out, voice=None, **kw):
        calls.append(text)
        return tts.TtsResult(full, 4.0, "edge_tts", "v", words)

    monkeypatch.setattr(tts, "synthesize", fake)
    res = await tts.synthesize_lines(None, ["Payroll took days", "Now it's done in 2026!", "", "See hrpai"], tmp_path)
    assert len(calls) == 1 and calls[0] == "Payroll took days. Now it's done in 2026! See hrpai."
    assert res[2] is None and [w.text for w in res[1].words] == ["Now", "it's", "done", "in 2026"]
    assert 1.0 < res[0].duration < 1.4 and res[1].words[0].start < 0.1


# ------------------------------------------------------------------ tech news
async def test_some_posts_take_fresh_tech_news(client):
    o = await new_org(client)
    await set_visuals(o["id"], news_share=60)
    acc = await mk_account(o["id"])
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        s.add(IdeaBrief(organization_id=o["id"], run_date="2026-11-14", title="New open model beats GPT-class on reasoning",
                        theme="tech_ai", score=0.9, status="new", facts=[{"fact": "x", "publisher": "The Verge"}]))
        s.add(IdeaBrief(organization_id=o["id"], run_date="2026-11-14", title="Our product tip", theme="company", score=0.99,
                        status="new"))
        await s.commit()
    pick = next(u for u in (uuid.uuid4() for _ in range(200)) if random.Random(f"news:{u}").random() * 100 < 60)
    skip = next(u for u in (uuid.uuid4() for _ in range(200)) if random.Random(f"news:{u}").random() * 100 >= 60)
    p1 = await mk_post(o["id"], acc, id=pick, topic=None)
    p2 = await mk_post(o["id"], acc, id=skip, topic=None)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        a = await svc.news_topic(s, await s.get(Post, p1.id))
        b = await svc.news_topic(s, await s.get(Post, p2.id))
        await s.commit()
    assert a is not None and a.title.startswith("New open model") and a.data["news"] is True and a.status == "used"
    assert b is None
    async with SessionLocal() as s:  # used once only
        set_tenant(s, o["id"])
        p3 = await mk_post(o["id"], acc, id=next(u for u in (uuid.uuid4() for _ in range(200))
                                                  if random.Random(f"news:{u}").random() * 100 < 60), topic=None)
        assert await svc.news_topic(s, await s.get(Post, p3.id)) is None


async def test_team_photos_are_only_a_reference_for_new_photos(client, monkeypatch):
    """"Only our team": a scene with people gets a NEW image made from a team photo; the photo itself is never used.
    Without a service that accepts a reference photo, there are no people at all."""
    import io

    from app.services import media as media_svc

    o = await new_org(client)
    await set_visuals(o["id"], people="team")
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), (120, 90, 70)).save(buf, "JPEG")
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        ref = await media_svc.store_bytes(s, o["id"], buf.getvalue(), "jpg", source="upload", kind="image", meta={"team": True})
        await s.commit()
        ref_id = ref.id

    # no reference-capable image service: no person, and never the uploaded photo
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        assert await media_svc._team_visual(s, o["id"], "HR manager welcoming a new employee", 400, 400) is None

    seen = {}

    async def fake_ref(session, scene, ref, mime, w, h, **kw):
        seen.update(scene=scene, ref_len=len(ref), mime=mime)
        out = io.BytesIO()
        Image.new("RGB", (w, h), (10, 120, 200)).save(out, "PNG")
        return image_gen.GeneratedImage(out.getvalue(), "gemini_images", uuid.uuid4(), "gemini-2.5-flash-image", "p", "")

    monkeypatch.setattr(image_gen, "generate_from_reference", fake_ref)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        a = await media_svc._team_visual(s, o["id"], "HR manager welcoming a new employee", 400, 400)
        assert await media_svc._team_visual(s, o["id"], "server room at night", 400, 400) is None  # no people needed
        await s.commit()
    assert a is not None and a.id != ref_id and a.source.startswith("ai:")
    assert a.meta["ai_person"] is True and a.meta["team_ref"] == str(ref_id)
    assert seen["ref_len"] == len(buf.getvalue()) and "welcoming" in seen["scene"]


async def test_team_mode_lets_the_writer_ask_for_team_members():
    """In "Only our team" mode scene descriptions may ask for a team member (that's what triggers a team photo),
    and the people check recognises it; in "No people" mode they may not."""
    from app.services.content import GenSettings, build_system_prompt

    b = BrandProfile(settings={"media": {"people": "team"}}, voice=None, audience=None, do_list=[], dont_list=[],
                     hashtags=[], ctas=[], banned_words=[], language="en", emoji_policy="light")
    cfg = GenSettings(mode="manual")
    team = build_system_prompt(b, "youtube", "video", cfg, "", [], [])
    assert "our team member" in team and "never people" not in team
    b.settings = {"media": {"people": "none"}}
    none = build_system_prompt(b, "youtube", "video", cfg, "", [], [])
    assert "never people" in none and "our team member" not in none
    assert media_policy.mentions_people("our team member presenting dashboard")


async def test_team_photos_and_stock_only_never_invents_images(client, monkeypatch):
    """Brand → Visuals "Team photos and stock only": no AI-generated images or clips anywhere in automatic posts;
    stock (checked for people) instead, and nobody but the team ("allow people" is turned into "team")."""
    from app.services import media as media_svc

    o = await new_org(client)
    r = await client.put("/content/brand/visuals", headers=o["h"], json={"sources": "team_stock", "people": "allow"})
    assert r.status_code == 200 and r.json()["sources"] == "team_stock" and r.json()["people"] == "team"

    async def no_ai(*a, **k):
        raise AssertionError("AI image generation must not be used")

    async def yes(*a, **k):
        return True

    picked = {}

    async def stock_photo(session, query, orientation=None):
        picked["q"] = query
        return None

    monkeypatch.setattr(image_gen, "generate", no_ai)
    monkeypatch.setattr(image_gen, "has_image_provider", yes)
    monkeypatch.setattr(stock, "has_stock", yes)
    monkeypatch.setattr(stock, "find_image", stock_photo)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        _, _, note = await media_svc.background(s, o["id"], "a dashboard on a laptop in a bright office", 600, 600, "auto")
        assert await media_policy.get(s) is not None and not (await media_policy.get(s)).ai_images
    assert note.startswith("gradient") and "laptop" in picked["q"]  # stock was asked; AI never

    r = await client.put("/content/brand/visuals", headers=o["h"], json={"sources": "mixed"})
    assert r.json()["sources"] == "mixed"
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        assert (await media_policy.get(s)).ai_images


@pytest.fixture
def signin_site(tmp_path):
    guard = "<script>if(localStorage.getItem('ok')!=='1')location.href='login.html'</script>"
    nav = ("<nav><a href='app.html'>Home</a> <a href='reports.html'>Reports</a> <a href='people.html#x'>People</a> "
           "<a href='logout.html'>Log out</a> <a href='https://elsewhere.example/x'>Out</a></nav>")
    (tmp_path / "login.html").write_text(
        "<!doctype html><form id=f><input name=email type=email><input name=pw type=password><button>Sign in</button></form>"
        "<script>document.getElementById('f').onsubmit=e=>{e.preventDefault();const d=new FormData(e.target);"
        "if(d.get('email')==='demo@hrpai.in'&&d.get('pw')==='Demo-123'){localStorage.setItem('ok','1');location.href='app.html'}"
        "else{document.body.insertAdjacentHTML('beforeend','<p>Wrong password</p>')}}</script>")
    (tmp_path / "app.html").write_text(f"<!doctype html>{guard}{nav}<h1>Dashboard</h1>")
    rows = "".join(f"<section style='height:420px;background:hsl({i * 40},60%,55%)'>Report {i}</section>" for i in range(8))
    (tmp_path / "reports.html").write_text(f"<!doctype html>{guard}{nav}{rows}")
    (tmp_path / "people.html").write_text(f"<!doctype html>{guard}{nav}<h1>People</h1>")
    (tmp_path / "logout.html").write_text("<!doctype html><script>localStorage.clear()</script>bye")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_demo_behind_a_sign_in(client, signin_site):
    """A demo account: the studio signs in, explores the app's menu (not "Log out", not other sites) and captures it;
    the password is stored encrypted and never returned."""
    try:
        await media_render.render_png("<html><body>ok</body></html>", 32, 32)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Chromium unavailable: {e}")
    o = await new_org(client)
    body = {"demos": [{"product": "HRP HRMS", "keywords": ["payroll"], "login_url": f"{signin_site}/login.html",
                       "username": "demo@hrpai.in", "password": "Demo-123"}]}
    r = await client.put("/content/brand/visuals", headers=o["h"], json=body)
    assert r.status_code == 200, r.text
    d = r.json()["demos"][0]
    assert d["has_password"] is True and d["username"] == "demo@hrpai.in" and "Demo-123" not in r.text and "secret" not in d
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        b = await s.scalar(select(BrandProfile))
        stored = b.settings["media"]["demos"][0]
        assert "Demo-123" not in json.dumps(stored) and stored["secret"]
        demo = (await media_policy.get(s)).demos[0]
        items = await demos.capture(s, o["id"], demo)
        await s.commit()
    urls = {a.meta["url"].rsplit("/", 1)[-1] for a in items}
    assert urls == {"app.html", "reports.html", "people.html"}  # no log out, no other site
    assert all(a.meta.get("signed_in") for a in items)
    assert any(a.kind == "video" and a.meta["url"].endswith("reports.html") for a in items)  # long page: scroll video

    # saving again without a password keeps it; a wrong password is reported clearly
    body["demos"][0]["password"] = None
    r = await client.put("/content/brand/visuals", headers=o["h"], json=body)
    assert r.json()["demos"][0]["has_password"] is True
    body["demos"][0]["password"] = "wrong"
    await client.put("/content/brand/visuals", headers=o["h"], json=body)
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        with pytest.raises(demos.DemoError, match="couldn't sign in"):
            await demos.capture(s, o["id"], (await media_policy.get(s)).demos[0])
    # removing the sign-in forgets the password
    r = await client.put("/content/brand/visuals", headers=o["h"], json={"demos": [{"product": "HRP HRMS", "clear_login": True,
                                                                                   "urls": [f"{signin_site}/login.html"]}]})
    assert r.json()["demos"][0]["has_password"] is False
