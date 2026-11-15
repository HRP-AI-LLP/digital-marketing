"""Academy: catalog, course + AI outline (background job), making a lesson by hand (draft posts per format ×
account × language, nothing scheduled), the teacher prompt, lesson scripts, the lesson writer hooks in the post
pipeline, slides and YouTube long-form uploads. The LLM is faked; the database is real (RLS enforced)."""

import uuid

import pytest
from sqlalchemy import select

import app.modules.academy  # noqa: F401 - registers academy.outline
from app.core.db import SessionLocal, set_tenant
from app.models import Course, Post
from app.services import academy
from app.services.jobs import run_inline
from app.services.llm import router as llm_router
from tests.test_content import fake, get_post, mk_account, new_org  # noqa: F401

OUTLINE = {"modules": [
    {"title": "Getting started", "lessons": [{"title": "What is Python?", "summary": "Know what Python is"},
                                              {"title": "Installing Python", "summary": "Install it"}]},
    {"title": "Basics", "lessons": [{"title": "Variables", "summary": "Store values"},
                                     {"title": "Loops", "summary": "Repeat work"}, "If and else"]},
]}

LESSON = {"title": "Python #1: What is Python?", "text": "Python is a language computers understand. Free course by HRP AI.",
          "hashtags": ["PythonProgramming", "LearnWithHRPAI"],
          "lesson_script": [
              {"kind": "title", "heading": "What is Python?", "voiceover": "Welcome to lesson one."},
              {"kind": "explain", "heading": "A language", "bullets": ["Tells the computer what to do", "Easy to read"],
               "voiceover": "Python is a language."},
              {"kind": "analogy", "heading": "Like a recipe", "bullets": ["Steps in order"], "voiceover": "Like a recipe."},
              {"kind": "code", "heading": "Your first line", "code": {"language": "python", "text": "print('Namaste')"},
               "voiceover": "Type this."},
              {"kind": "diagram", "heading": "How it runs", "diagram": {"nodes": ["Code", "Python", "Output"], "flow": "row"},
               "voiceover": "It runs like this."},
              {"kind": "quiz", "heading": "Quick quiz", "quiz": {"question": "What prints text?", "options": ["print", "say", "show"],
                                                                 "answer": 0}, "voiceover": "Answer this."},
              {"kind": "recap", "heading": "Recap", "bullets": ["Python is a language"], "voiceover": "To recap."},
              {"kind": "outro", "heading": "Next", "voiceover": "See you next time."},
          ]}


@pytest.fixture
def teach(fake, monkeypatch):  # noqa: F811
    base = fake.complete_json

    async def complete_json(session, task, prompt, *, system=None, temperature=0.5, max_tokens=2500):
        if task == "plan" and "complete free video course" in prompt:
            fake.calls.append((task, prompt, system))
            return OUTLINE
        if task == "write" and system and "free IT course" in system:
            fake.calls.append((task, prompt, system))
            if "lesson_script" in system:
                return LESSON
            if "video_script" in system:
                return {"title": "Python #1", "text": "Python in 50 seconds. Free course by HRP AI.", "hashtags": ["Python"],
                        "video_script": [{"caption": "What is Python?", "voiceover": "Python is a language.", "query": "laptop code",
                                          "code": {"language": "python", "text": "print('hi')"}},
                                         {"caption": "Like a recipe", "voiceover": "Steps in order.", "query": "kitchen"}]}
            return {"text": "Lesson 1 in slides. Free course by HRP AI.", "hashtags": ["Python"],
                    "slides": [{"heading": "What is Python?", "body": "A language"}, {"heading": "Recap", "body": "Done"}]}
        return await base(session, task, prompt, system=system, temperature=temperature, max_tokens=max_tokens)

    monkeypatch.setattr(llm_router, "complete_json", complete_json)
    return fake


async def _course(oid) -> Course:
    async with SessionLocal() as s:
        set_tenant(s, oid)
        return (await s.scalars(select(Course))).one()


async def _posts(oid) -> list[Post]:
    async with SessionLocal() as s:
        set_tenant(s, oid)
        return list((await s.scalars(select(Post).order_by(Post.created_at))).all())


def test_outline_cleaning_and_lessons():
    out = academy.clean_outline({"outline": OUTLINE})
    assert [m["title"] for m in out["modules"]] == ["Getting started", "Basics"]
    c = Course(title="Python", outline=out)
    items = academy.lessons(c)
    assert len(items) == 5 and items[4] == {"n": 5, "module_no": 2, "module": "Basics", "title": "If and else", "summary": ""}
    assert academy.lesson(c, 6) is None
    assert academy.clean_outline({"modules": [{"title": "", "lessons": ["x"]}, {"title": "A", "lessons": []}]}) == {"modules": []}


def test_formats_fit_platforms():
    assert academy.account_fits("lesson", "youtube")
    assert not academy.account_fits("lesson", "instagram")
    assert academy.account_fits("short", "instagram") and academy.account_fits("short", "youtube")
    assert academy.account_fits("carousel", "linkedin_org")
    assert not academy.account_fits("carousel", "youtube")


def test_lesson_script_cleaning():
    s = academy.clean_lesson_script(LESSON["lesson_script"] + [{"kind": "explain"}, "junk",
                                    {"heading": "Q", "quiz": {"question": "?", "options": ["a", "b"], "answer": 9}}])
    assert len(s) == 9
    assert s[3]["code"] == {"language": "python", "text": "print('Namaste')"}
    assert s[4]["diagram"]["nodes"] == ["Code", "Python", "Output"]
    assert s[8]["quiz"]["answer"] == 1  # clamped


def test_teacher_prompt_hindi_and_formats():
    p = Post(platform="youtube", post_type="video", topic="Variables",
             meta={"course_id": "x", "course_title": "Python programming", "lesson": 3, "lessons_total": 40,
                   "module": "Basics", "lang": "hi", "course_format": "lesson", "prev_title": "Installing Python"})
    sys_ = academy.system_prompt(p, "YouTube", 5000, 15, 100)
    assert "Devanagari" in sys_ and "lesson 3 of 40" in sys_ and "lesson_script" in sys_
    assert "#PythonProgramming" in sys_ and "Installing Python" in sys_
    p.meta = {**p.meta, "lang": "en", "course_format": "short"}
    sys2 = academy.system_prompt(p, "YouTube Shorts", 5000, 15, 100)
    assert "video_script" in sys2 and "Devanagari" not in sys2 and "lesson_script" not in sys2


async def test_course_outline_and_manual_lesson(client, teach):
    o = await new_org(client)
    yt = await mk_account(o["id"], "youtube", name="HRP AI YouTube")
    ig = await mk_account(o["id"], "instagram", name="HRP AI Instagram")
    li = await mk_account(o["id"], "linkedin_org", name="HRP page")

    r = await client.get("/academy/catalog", headers=o["h"])
    assert r.status_code == 200, r.text
    cat = r.json()
    assert any(i["id"] == "python" for g in cat["groups"] for i in g["items"])
    fits = {a["id"]: a["formats"] for a in cat["accounts"]}
    assert "lesson" in fits[str(yt.id)] and "lesson" not in fits[str(ig.id)]

    r = await client.post("/academy/courses", headers=o["h"], json={
        "title": "Python programming", "catalog_id": "python", "languages": ["en", "hi"],
        "formats": {"short": True, "lesson": True, "carousel": True},
        "account_ids": {"lesson": [str(yt.id), str(ig.id)], "short": [str(yt.id), str(ig.id)], "carousel": [str(li.id)]}})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["outline_status"] == "writing" and c["account_ids"]["lesson"] == [str(yt.id)]  # Instagram can't take lessons
    assert "Python from zero" in c["topic"]

    await run_inline("academy.outline", o["id"], {"course_id": c["id"]})
    c = (await client.get(f"/academy/courses/{c['id']}", headers=o["h"])).json()
    assert c["lessons_total"] == 5 and c["status"] == "active" and c["outline_status"] is None
    assert c["lessons"][2]["title"] == "Variables"

    r = await client.post(f"/academy/courses/{c['id']}/lessons/1", headers=o["h"], json={})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 8  # (lesson: yt + short: yt, ig + carousel: li) × 2 languages
    posts = await _posts(o["id"])
    assert len(posts) == 8
    assert all(p.mode == "manual" and p.scheduled_at is None and p.status == "planned" for p in posts)
    lesson_yt = [p for p in posts if p.meta["course_format"] == "lesson"]
    assert {p.meta["lang"] for p in lesson_yt} == {"en", "hi"}
    assert lesson_yt[0].meta["voice"] in ("en-US-AndrewMultilingualNeural", "hi-IN-SwaraNeural")
    assert lesson_yt[0].meta["next_title"] == "Installing Python" and lesson_yt[0].meta["prev_title"] is None

    # writing goes through the normal pipeline with the teacher prompt and lands as a draft
    lp = next(p for p in lesson_yt if p.meta["lang"] == "en")
    await run_inline("post.generate", o["id"], {"post_id": str(lp.id)})
    lp = await get_post(o["id"], lp.id)
    assert lp.status == "draft", lp.error
    assert len(lp.meta["lesson_script"]) == 8 and lp.meta["lesson_script"][3]["code"]["language"] == "python"
    assert lp.qa["similarity_method"] == "course"
    teacher_calls = [c for c in teach.calls if c[0] == "write" and c[2] and "free IT course" in c[2]]
    assert teacher_calls and "company facts" not in teacher_calls[0][2]

    sp = next(p for p in posts if p.meta["course_format"] == "short" and p.platform == "instagram")
    await run_inline("post.generate", o["id"], {"post_id": str(sp.id)})
    sp = await get_post(o["id"], sp.id)
    assert sp.status == "draft" and sp.meta["video_script"]

    # progress, lesson list, and choosing only some formats / languages
    c = (await client.get(f"/academy/courses/{c['id']}", headers=o["h"])).json()
    assert c["lessons_made"] == 1 and c["next_lesson"] == 2 and c["lessons"][0]["made_at"]
    lp_list = (await client.get(f"/academy/courses/{c['id']}/lessons/1", headers=o["h"])).json()
    assert len(lp_list["posts"]) == 8
    r = await client.post(f"/academy/courses/{c['id']}/lessons/2", headers=o["h"],
                          json={"formats": ["lesson"], "languages": ["hi"]})
    assert r.json()["count"] == 1
    r = await client.post(f"/academy/courses/{c['id']}/lessons/9", headers=o["h"], json={})
    assert r.status_code == 400 and "no lesson 9" in r.json()["detail"]
    r = await client.post(f"/academy/courses/{c['id']}/lessons/3", headers=o["h"],
                          json={"formats": ["carousel"], "languages": ["en"]})
    assert r.status_code == 200

    # outline edits; another organisation can't see the course
    r = await client.patch(f"/academy/courses/{c['id']}", headers=o["h"],
                           json={"outline": {"modules": [{"title": "Only", "lessons": [{"title": "One"}]}]}})
    assert r.json()["lessons_total"] == 1
    other = await new_org(client)
    assert (await client.get(f"/academy/courses/{c['id']}", headers=other["h"])).status_code == 404
    assert (await client.delete(f"/academy/courses/{c['id']}", headers=o["h"])).status_code == 204
    assert len(await _posts(o["id"])) == 10  # the posts already made stay


async def test_lesson_needs_accounts(client, teach):
    o = await new_org(client)
    r = await client.post("/academy/courses", headers=o["h"], json={"title": "Networking", "catalog_id": "networking"})
    cid = r.json()["id"]
    await run_inline("academy.outline", o["id"], {"course_id": cid})
    r = await client.post(f"/academy/courses/{cid}/lessons/1", headers=o["h"], json={})
    assert r.status_code == 400 and "no account chosen" in r.json()["detail"]


def test_slides_render_html():
    from app.services.media_templates import lesson as tpl
    from app.services.media_templates.base import BrandStyle
    from app.services.media_templates.kit import Kit

    k = Kit(1280, 720, BrandStyle.default() if hasattr(BrandStyle, "default") else BrandStyle(), theme="dark")
    ctx = {"course": "Python", "lesson_label": "Lesson 1/40", "lang": "hi", "module": "Basics", "index": 1, "total": 8}
    s = academy.clean_lesson_script(LESSON["lesson_script"])
    assert [tpl.steps_of(x) for x in s] == [1, 2, 1, 1, 3, 2, 1, 1]
    h = tpl.slide_html(k, s[1], 1, ctx)
    assert "Tells the computer" in h and "class='off'" in h  # second bullet hidden in step 1
    code = tpl.slide_html(k, s[3], 1, ctx)
    assert "Namaste" in code and "style=\"color" in code  # highlighted
    quiz = tpl.slide_html(k, s[5], 2, ctx)
    assert "opt ok" in quiz
    assert "ऐसे समझिए" in tpl.slide_html(k, s[2], 1, ctx)
    assert "Noto Sans Devanagari" in h


async def test_youtube_lesson_is_long_form(monkeypatch):
    from app.services.social import google

    sent = {}

    class Resp:
        def __init__(self, status=200, data=None, headers=None):
            self.status_code, self._d, self.headers, self.text = status, data or {}, headers or {}, ""

        def json(self):
            return self._d

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, json=None, headers=None):
            sent["meta"] = json
            return Resp(200, {}, {"location": "https://up"})

        async def put(self, url, content=None, headers=None):
            return Resp(200, {"id": "vid1"})

    async def tok(session):
        return "t"

    monkeypatch.setattr(google, "_token", tok)
    monkeypatch.setattr(google.net, "client", lambda *a, **k: C())
    monkeypatch.setattr(google.common, "read_bytes", lambda v: b"x")
    monkeypatch.setattr(google.common, "is_video", lambda m: True)
    monkeypatch.setattr(google.common, "raise_for", lambda r, what: None)
    monkeypatch.setattr(google, "ok_json", lambda r, what: r.json())

    class V:
        mime = "video/mp4"

    post = Post(id=uuid.uuid4(), platform="youtube", title="Python #1: What is Python?", text="Learn", hashtags=["Python"],
                meta={"course_id": "c", "course_format": "lesson", "lang": "hi"})
    vid, url = await google._publish_youtube(None, post, type("A", (), {"settings": {}})(), [V()])
    assert url == "https://www.youtube.com/watch?v=vid1"
    sn = sent["meta"]["snippet"]
    assert "#Shorts" not in sn["title"] and sn["categoryId"] == "27" and sn["defaultAudioLanguage"] == "hi"
    post.meta = {"course_id": "c", "course_format": "short", "lang": "en"}
    _vid, url = await google._publish_youtube(None, post, type("A", (), {"settings": {}})(), [V()])
    assert url.endswith("/shorts/vid1") and sent["meta"]["snippet"]["title"].endswith("#Shorts")
