"""Academy: free IT courses for people outside IT, posted lesson by lesson.

A course (Python, C, networking, machine learning …) gets an AI-written outline: modules → lessons, from the basics
to advanced. When you click "Make this lesson", it becomes posts on the chosen accounts:
  - "short":    a Short / Reel under 60 s (one idea, an everyday analogy, maybe a few lines of code), post type video;
  - "lesson":   a full 5-10 minute lesson video, 16:9, slides + code + diagrams + 3D, for YouTube (long-form);
  - "carousel": a slide carousel (summary / cheat sheet) for LinkedIn, Instagram, Facebook.
… in every language of the course (English and Hindi), each as its own post. The posts go through the normal
pipeline (writing → checks → media) and land as drafts: Academy is used by hand, nothing is scheduled or published
until you do it from the post.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, Post, SocialAccount
from app.services.llm import router as llm_router

log = logging.getLogger("academy")

# ----------------------------------------------------------------------------- catalog
# (id, title, what the course covers) by group: the starting points offered in the app; any topic can be typed too.
CATALOG: dict[str, list[tuple[str, str, str]]] = {
    "Foundations": [
        ("how-computers-work", "How computers work", "bits and bytes, CPU, memory, storage, how a program runs, from switches to apps"),
        ("history-of-programming", "From machine code to Python", "the story of programming: machine code, assembly, Fortran/COBOL, C, object-oriented languages, scripting, modern languages and why each appeared"),
        ("hardware", "Computer hardware", "CPU, GPU, RAM, storage, motherboards, buses, how hardware and software talk"),
        ("operating-systems", "Operating systems", "processes, threads, memory, files, Linux and Windows basics, the command line"),
        ("internet-and-web", "How the internet and the web work", "IP addresses, DNS, HTTP/HTTPS, browsers, servers, what happens when you open a website"),
    ],
    "Programming languages": [
        ("c", "C programming", "C from zero: variables, control flow, functions, arrays, pointers, memory, structs, files"),
        ("cpp", "C++ programming", "C++ from zero: classes, objects, inheritance, templates, STL, memory management, modern C++"),
        ("java", "Java programming", "Java from zero: syntax, OOP, collections, exceptions, streams, JVM, building applications"),
        ("dotnet", "C# and .NET", "C# and .NET from zero: syntax, OOP, LINQ, async, ASP.NET Core web APIs"),
        ("python", "Python programming", "Python from zero: variables, loops, functions, data structures, files, modules, OOP, libraries"),
        ("r", "R for data analysis", "R from zero: vectors, data frames, statistics, plotting, data cleaning"),
        ("go", "Go programming", "Go from zero: syntax, structs, interfaces, goroutines, channels, building services"),
        ("scala", "Scala programming", "Scala from zero: functional and object-oriented programming, collections, Spark basics"),
        ("javascript", "JavaScript and TypeScript", "JavaScript and TypeScript from zero: the browser, DOM, async, Node.js, types"),
        ("sql", "SQL", "SQL from zero: tables, SELECT, filtering, joins, grouping, indexes, transactions"),
    ],
    "Mathematics": [
        ("math-for-programmers", "Mathematics for programmers", "number systems, logic, sets, functions, algebra, discrete maths, big-O"),
        ("probability-statistics", "Probability and statistics", "probability, distributions, mean/variance, Bayes, hypothesis testing, with everyday examples"),
        ("linear-algebra", "Linear algebra for AI", "vectors, matrices, dot products, transformations, eigenvectors, why AI needs them"),
        ("calculus-for-ml", "Calculus for machine learning", "rates of change, derivatives, gradients, gradient descent, intuition first"),
    ],
    "AI and data science": [
        ("ai-basics", "Artificial intelligence for everyone", "what AI is and isn't, history, types of AI, everyday uses, limits and ethics"),
        ("machine-learning", "Machine learning", "supervised and unsupervised learning, regression, classification, trees, evaluation, overfitting"),
        ("deep-learning", "Deep learning", "neurons, neural networks, training, CNNs, RNNs, transformers, intuition first"),
        ("generative-ai", "Generative AI", "how large language models and image generators work, prompting, tokens, embeddings, limits"),
        ("llm-apps-rag", "Building with LLMs and RAG", "APIs, prompts, embeddings, vector databases, retrieval-augmented generation, evaluation"),
        ("ai-agents", "AI agents", "what agents are, tools, planning, memory, multi-agent systems, building and testing agents safely"),
        ("data-science", "Data science", "the data science workflow: questions, data, cleaning, analysis, visualisation, storytelling"),
    ],
    "Backend and APIs": [
        ("backend", "Backend development", "servers, requests, databases, authentication, caching, queues, deploying a backend"),
        ("apis", "APIs: SOAP, REST, GraphQL, gRPC", "what an API is, SOAP and XML, REST and JSON, GraphQL, gRPC, versioning, security"),
        ("microservices", "Microservices and system design", "monoliths vs microservices, scaling, load balancing, messaging, reliability"),
        ("security", "Cybersecurity basics", "passwords, encryption, HTTPS, common attacks, safe coding, staying safe online"),
    ],
    "Infrastructure": [
        ("networking", "Computer networking", "LAN/WAN, OSI and TCP/IP models, IP, routing, TCP/UDP, DNS, Wi-Fi, firewalls"),
        ("cloud", "Cloud computing", "what the cloud is, AWS/Azure/GCP basics, compute, storage, serverless, cost"),
        ("devops", "DevOps, Docker and Kubernetes", "version control, CI/CD, containers, Docker, Kubernetes, monitoring"),
        ("git", "Git and GitHub", "version control from zero: commits, branches, merging, pull requests, teamwork"),
    ],
    "Data": [
        ("databases", "Databases", "what a database is, relational vs NoSQL, modelling, SQL, indexes, transactions, backups"),
        ("big-data", "Big data", "what makes data big, Hadoop, Spark, data lakes, warehouses, streaming"),
        ("data-engineering", "Data engineering", "pipelines, ETL/ELT, orchestration, data quality, modern data stack"),
    ],
}
CATALOG_BY_ID = {cid: (group, title, scope) for group, items in CATALOG.items() for cid, title, scope in items}

LANGS = {
    "en": {"name": "English", "voice": "en-US-AndrewMultilingualNeural",
           "rule": "Write in simple, warm English: short sentences, everyday words, no jargon without explaining it."},
    "hi": {"name": "Hindi", "voice": "hi-IN-SwaraNeural",
           "rule": ("Write in simple, everyday spoken Hindi in Devanagari script, the way a friendly teacher in India talks. "
                    "Keep technical terms in English and in Latin letters (for example variable, function, loop, API, "
                    "database, server), and explain them in Hindi. Code, hashtags and the title's course name stay in English.")},
}
FORMATS = {
    "short": {"label": "Shorts / Reels", "post_type": "video"},
    "lesson": {"label": "Full lesson video", "post_type": "video"},
    "carousel": {"label": "Carousel", "post_type": "carousel"},
}
LESSON_SCENES = (10, 24)


class AcademyError(Exception):
    pass


def format_platforms(fmt: str) -> set[str] | None:
    """Platforms a format can go to (None: any that supports its post type)."""
    return {"youtube"} if fmt == "lesson" else None


def account_fits(fmt: str, platform: str) -> bool:
    from app.services.platforms import supported_types

    allowed = format_platforms(fmt)
    if allowed is not None and platform not in allowed:
        return False
    return FORMATS[fmt]["post_type"] in supported_types(platform)


# ----------------------------------------------------------------------------- outline
def lessons(course: Course) -> list[dict]:
    """Flat list: [{"n", "module_no", "module", "title", "summary"}]."""
    out, n = [], 0
    for mi, m in enumerate((course.outline or {}).get("modules") or [], 1):
        for les in m.get("lessons") or []:
            n += 1
            out.append({"n": n, "module_no": mi, "module": m.get("title") or f"Module {mi}",
                        "title": les.get("title") or f"Lesson {n}", "summary": les.get("summary") or ""})
    return out


def lesson(course: Course, n: int) -> dict | None:
    items = lessons(course)
    return items[n - 1] if 1 <= n <= len(items) else None


def clean_outline(raw) -> dict:
    """Validate an outline (from the AI or an edit): modules with titled lessons."""
    if isinstance(raw, dict) and "outline" in raw and isinstance(raw["outline"], dict):
        raw = raw["outline"]
    mods = raw.get("modules") if isinstance(raw, dict) else raw
    out = []
    for m in mods or []:
        if not isinstance(m, dict):
            continue
        les = []
        for x in m.get("lessons") or []:
            if isinstance(x, str):
                x = {"title": x}
            if isinstance(x, dict) and str(x.get("title") or "").strip():
                les.append({"title": str(x["title"]).strip()[:160], "summary": str(x.get("summary") or "").strip()[:400]})
        if les and str(m.get("title") or "").strip():
            out.append({"title": str(m["title"]).strip()[:160], "lessons": les[:20]})
    return {"modules": out[:25]}


async def generate_outline(session: AsyncSession, course: Course) -> dict:
    """A complete course outline, from the very basics to advanced, as modules → lessons."""
    prompt = (
        f"Design a complete free video course: \"{course.title}\".\n"
        f"What it covers: {course.topic or course.title}.\n"
        f"Audience: {course.audience or 'people with no IT background (students, working adults, career changers)'}; "
        f"start level: {course.level}.\n"
        "Go from absolute zero to advanced, in a logical order where each lesson builds on the previous one. "
        "Each lesson teaches ONE idea that fits a 5-10 minute video. Include, where it fits the subject, a short history "
        "or 'why this exists' lesson, hands-on lessons, common mistakes, a few mini projects, and a final recap.\n"
        "Size: 6 to 14 modules, 4 to 10 lessons each (about 40 to 90 lessons in all).\n"
        'Return JSON: {"modules": [{"title": "<module title>", "lessons": [{"title": "<lesson title, under 70 '
        'characters>", "summary": "<one sentence: what the learner understands after it>"}]}]}'
    )
    raw = await llm_router.complete_json(session, "plan", prompt,
                                         system="You are an expert teacher and curriculum designer. Reply with JSON only.",
                                         temperature=0.4, max_tokens=8000)
    outline = clean_outline(raw)
    if sum(len(m["lessons"]) for m in outline["modules"]) < 5:
        raise AcademyError("The AI model returned an outline that's too short. Try again, or use a stronger model.")
    return outline


# ----------------------------------------------------------------------------- making a lesson
async def make_lesson(session: AsyncSession, course: Course, n: int, *, formats: list[str] | None = None,
                      languages: list[str] | None = None, account_ids: dict | None = None) -> list[Post]:
    """Create lesson n's posts as drafts (one per format × account × language) and queue their writing. Nothing is
    scheduled or published: each post lands in Posts as a draft for review. Formats / languages / accounts default to
    the course's own choices."""
    from app.services.jobs import enqueue

    all_lessons = lessons(course)
    info = all_lessons[n - 1] if 1 <= n <= len(all_lessons) else None
    if info is None:
        raise AcademyError(f"This course has no lesson {n}.")
    fmts = [f for f in (formats if formats is not None else [k for k, on in (course.formats or {}).items() if on])
            if f in FORMATS]
    langs = [x for x in (languages if languages is not None else (course.languages or ["en"])) if x in LANGS]
    chosen = account_ids if account_ids is not None else (course.account_ids or {})
    if not fmts or not langs:
        raise AcademyError("Choose at least one format and one language.")
    accounts = {a.id: a for a in (await session.scalars(select(SocialAccount))).all()}
    created: list[Post] = []
    skipped: list[str] = []
    for fmt in fmts:
        ids = chosen.get(fmt) or []
        if not ids:
            skipped.append(f"{FORMATS[fmt]['label']}: no account chosen")
        for aid in ids:
            try:
                acc = accounts.get(uuid.UUID(str(aid)))
            except ValueError:
                acc = None
            if acc is None:
                continue
            if not account_fits(fmt, acc.platform):
                skipped.append(f"{FORMATS[fmt]['label']} can't go to {acc.platform}")
                continue
            for lang in langs:
                post = Post(
                    id=uuid.uuid4(), organization_id=course.organization_id, social_account_id=acc.id,
                    platform=acc.platform, status="planned", mode="manual", scheduled_at=None,
                    post_type=FORMATS[fmt]["post_type"], topic=info["title"], hashtags=[], media_ids=[], qa={},
                    meta={"course_id": str(course.id), "course_title": course.title, "lesson": info["n"],
                          "lessons_total": len(all_lessons), "module": info["module"], "lesson_summary": info["summary"],
                          "lang": lang, "course_format": fmt, "post_type_fixed": True, "voice": LANGS[lang]["voice"],
                          "three_d": True, "next_title": all_lessons[n]["title"] if n < len(all_lessons) else None,
                          "prev_title": all_lessons[n - 2]["title"] if n > 1 else None},
                )
                session.add(post)
                await session.flush()
                await enqueue(session, "post.generate", course.organization_id, {"post_id": str(post.id)},
                              dedupe_key=f"gen:{post.id}")
                created.append(post)
    if not created:
        raise AcademyError("Nothing to make: " + ("; ".join(dict.fromkeys(skipped)) or "choose accounts for the formats") + ".")
    made = dict((course.settings or {}).get("made") or {})
    made[str(n)] = datetime.now(UTC).isoformat(timespec="seconds")
    course.settings = {**(course.settings or {}), "made": made}
    course.next_lesson = max(course.next_lesson or 1, n + 1)
    if course.status == "draft":
        course.status = "active"
    if course.next_lesson > len(all_lessons):
        course.status = "done"
    return created


# ----------------------------------------------------------------------------- writing
def is_course_post(post: Post) -> bool:
    return bool((post.meta or {}).get("course_id"))


def _hashtag(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s.title())[:30]


def system_prompt(post: Post, platform_label: str, max_chars: int, max_tags: int, title_chars: int | None) -> str:
    m = post.meta or {}
    lang = LANGS.get(m.get("lang") or "en", LANGS["en"])
    fmt = m.get("course_format") or "short"
    n, total = int(m.get("lesson") or 1), int(m.get("lessons_total") or 1)
    course = m.get("course_title") or "IT course"
    parts = [
        ("You are a friendly, patient teacher running a free IT course on social media for people who have never "
        "studied IT. Explain like to a curious 14-year-old: one idea at a time, everyday Indian examples (a chai "
        "stall, cricket, train tickets, UPI payments, a kitchen recipe), no unexplained jargon. Be technically "
        "correct: if something is simplified, say so; never invent version numbers, statistics or quotes. "
         "Write plain text, no markdown."),
        lang["rule"],
        (f"Course: {course}. This is lesson {n} of {total} (module: {m.get('module') or ''}). "
         f"Lesson: {post.topic}. Goal: {m.get('lesson_summary') or ''}"),
        (f"Platform: {platform_label}. The caption plus hashtags must fit in {max_chars} characters; at most "
        f"{max_tags} hashtags; include #{_hashtag(course)} and #LearnWithHRPAI when hashtags are allowed. "
        "End the caption with a line like 'Free course by HRP AI. Follow for the next lesson.' (in the post's language)."),
    ]
    if m.get("prev_title"):
        parts.append(f"Previous lesson (link back in one line): {m['prev_title']}")
    shape = ['"text": "<caption: what this lesson teaches and why it matters, then the follow line>"',
             '"hashtags": ["NoHashSign", ...]']
    if title_chars:
        shape.insert(0, f'"title": "<under {title_chars} characters, e.g. \'{course} #{n}: <lesson in a few words>\'>"')
    if fmt == "short":
        shape.append(
            '"video_script": [{"caption": "<on-screen text, max 8 words>", "voiceover": "<spoken, 1-3 short sentences>", '
            '"query": "<2-4 words for the background visual: objects, screens, technology, never people>", '
            '"code": {"language": "<e.g. python>", "text": "<optional: at most 6 short lines of real, runnable code>"}}] '
            "(5 to 8 scenes, under 55 seconds of speech: a hook question, the idea with an analogy, a tiny example, "
            "a one-line recap, 'follow for part " + str(n + 1) + "'; use code only where it helps)")
    elif fmt == "lesson":
        shape.append(
            '"lesson_script": [{"kind": "<title | explain | analogy | code | diagram | steps | quiz | recap | outro>", '
            '"heading": "<slide heading, max 8 words>", "bullets": ["<max 4 points, max 8 words each>"], '
            '"code": {"language": "...", "text": "<at most 14 lines of real code>"}, '
            '"diagram": {"nodes": ["<2-6 short labels in order>"], "flow": "<row | cycle>"}, '
            '"quiz": {"question": "...", "options": ["...", "...", "..."], "answer": <0-2>}, '
            '"voiceover": "<what the teacher says on this slide: 40 to 90 words, natural and warm>"}] '
            f"({LESSON_SCENES[0]} to {LESSON_SCENES[1]} slides, 5 to 9 minutes of speech in all: start with a title "
            "slide and why it matters, explain step by step with analogies, show code or a diagram where it helps, "
            "add a short quiz, recap, and an outro that names the next lesson). Only include the fields a slide uses.")
    else:
        shape.append('"slides": [{"heading": "<max 8 words>", "body": "<max 30 words; a code line is fine>"}] '
                     "(6 to 9 slides: title, the idea, an analogy, an example, common mistake, recap, next lesson)")
    shape.append('"image_brief": "<the visual for the cover: technology or objects, no people>"')
    parts.append("Reply with ONLY a JSON object: {" + ", ".join(shape) + "}")
    return "\n\n".join(parts)


def _code(v) -> dict | None:
    if isinstance(v, dict) and str(v.get("text") or "").strip():
        return {"language": str(v.get("language") or "text")[:20].lower(), "text": str(v["text"]).rstrip()[:1200]}
    return None


def clean_lesson_script(raw) -> list[dict]:
    out = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        vo = str(s.get("voiceover") or "").strip()
        head = str(s.get("heading") or "").strip()
        if not vo and not head:
            continue
        item: dict = {"kind": str(s.get("kind") or "explain").lower()[:12], "heading": head[:120], "voiceover": vo[:1200],
                      "bullets": [str(b).strip()[:90] for b in (s.get("bullets") or []) if str(b).strip()][:5]}
        if c := _code(s.get("code")):
            item["code"] = c
        d = s.get("diagram")
        if isinstance(d, dict) and isinstance(d.get("nodes"), list):
            nodes = [str(x).strip()[:30] for x in d["nodes"] if str(x).strip()][:6]
            if len(nodes) >= 2:
                item["diagram"] = {"nodes": nodes, "flow": "cycle" if d.get("flow") == "cycle" else "row"}
        q = s.get("quiz")
        if isinstance(q, dict) and q.get("question") and isinstance(q.get("options"), list):
            opts = [str(o).strip()[:80] for o in q["options"] if str(o).strip()][:4]
            if len(opts) >= 2:
                try:
                    ans = max(0, min(len(opts) - 1, int(q.get("answer") or 0)))
                except (TypeError, ValueError):
                    ans = 0
                item["quiz"] = {"question": str(q["question"])[:160], "options": opts, "answer": ans}
        out.append(item)
    return out[:30]


async def fix_caption(session: AsyncSession, post: Post, previous: dict, issues: list[str]) -> dict:
    """Fix only the caption / title / hashtags of a course draft (the scenes and slides stay as they are)."""
    from app.services.content import full_caption, normalize_draft, platform_of

    p = platform_of(post.platform)
    lang = LANGS.get((post.meta or {}).get("lang") or "en", LANGS["en"])
    prompt = ("Rewrite this lesson post's caption to fix these problems:\n- " + "\n- ".join(issues) +
              f"\n\nLimits: caption plus hashtags within {p.max_chars} characters, at most {p.max_hashtags} hashtags" +
              (f", title under {p.title_chars} characters" if p.title_chars else "") + f". {lang['rule']}\n\n"
              f"Title: {previous.get('title') or ''}\nCaption:\n{full_caption(previous.get('text'), previous.get('hashtags'))}\n\n"
              'Return JSON: {"title": "...", "text": "...", "hashtags": ["..."]}')
    raw = await llm_router.complete_json(session, "write", prompt, system="You edit social media captions. JSON only.",
                                         temperature=0.4, max_tokens=1500)
    fixed = normalize_draft(raw, post.platform, "text")
    return {**previous, "text": fixed["text"], "hashtags": fixed["hashtags"], "title": fixed.get("title") or previous.get("title")}


async def write_draft(session: AsyncSession, post: Post, *, fix_issues: list[str] | None = None,
                      previous: dict | None = None) -> dict:
    """The writer for course posts: teaching style, the lesson's language and format. Returns a normalised draft
    (text, hashtags, title, video_script/slides) plus lesson_script for full lessons."""
    from app.services.content import normalize_draft, platform_of

    if fix_issues and previous:
        return await fix_caption(session, post, previous, fix_issues)
    p = platform_of(post.platform)
    m = post.meta or {}
    fmt = m.get("course_format") or "short"
    system = system_prompt(post, "YouTube (long-form video)" if fmt == "lesson" else p.label, p.max_chars,
                           p.max_hashtags, p.title_chars)
    user = f"Write lesson {m.get('lesson')}: {post.topic}."
    if m.get("regenerate_note"):
        user += f" This is a new version, clearly different from the previous one: {m['regenerate_note']}"
    raw = await llm_router.complete_json(session, "write", user, system=system, temperature=0.6,
                                         max_tokens=9000 if fmt == "lesson" else 3000)
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        raw = raw[0]
    draft = normalize_draft(raw, post.platform, post.post_type)
    if fmt == "short" and isinstance(raw, dict):
        codes = [_code(s.get("code")) if isinstance(s, dict) else None for s in raw.get("video_script") or []]
        for s, c in zip(draft.get("video_script") or [], codes):
            if c:
                s["code"] = c
    if fmt == "lesson":
        script = clean_lesson_script(raw.get("lesson_script") if isinstance(raw, dict) else None)
        if len(script) < 6:
            raise AcademyError("The AI model didn't return a full lesson (too few slides). It will be retried.")
        draft["lesson_script"] = script
    return draft


async def judge(session: AsyncSession, post: Post, draft: dict) -> tuple[float | None, list[str]]:
    """Reviewer for lessons: correct, simple, well taught (instead of the company-post reviewer)."""
    from app.services.content import full_caption

    m = post.meta or {}
    body = full_caption(draft.get("text"), draft.get("hashtags"))
    scenes = draft.get("lesson_script") or draft.get("video_script") or draft.get("slides") or []
    said = "\n".join(str(s.get("voiceover") or s.get("body") or "") for s in scenes if isinstance(s, dict))[:5000]
    prompt = (f"Review this lesson for beginners with no IT background. Course: {m.get('course_title')}; "
              f"lesson {m.get('lesson')}: {post.topic}; language: {LANGS.get(m.get('lang') or 'en', LANGS['en'])['name']}.\n\n"
              f"Caption:\n{body}\n\nWhat is said / shown:\n{said or '(none)'}\n\n"
              "Score 0-10 for technical correctness, simplicity, clear teaching and engagement. List concrete issues "
              '(wrong facts or code, unexplained jargon, confusing order). Return JSON: {"score": <number>, "issues": ["..."]}')
    try:
        data = await llm_router.complete_json(session, "qa", prompt, system="You are a strict teacher and editor. JSON only.",
                                              temperature=0.1, max_tokens=600)
    except Exception as e:  # noqa: BLE001
        log.info("lesson judge skipped: %s", e)
        return None, []
    if not isinstance(data, dict):
        return None, []
    try:
        score = max(0.0, min(10.0, float(data.get("score"))))
    except (TypeError, ValueError):
        score = None
    return score, [str(i)[:300] for i in (data.get("issues") or []) if isinstance(i, (str, int, float))][:8]
