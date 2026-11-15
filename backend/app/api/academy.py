"""Academy API (/academy): free IT courses made lesson by lesson, by hand.

GET  /academy/catalog                    course ideas by group, languages, formats, accounts that fit each format
GET  /academy/courses                    courses with progress
POST /academy/courses                    create (and plan its outline in the background)
GET/PATCH/DELETE /academy/courses/{id}
POST /academy/courses/{id}/outline       plan the outline again (AI)
POST /academy/courses/{id}/lessons/{n}   make lesson n: its posts are written as drafts to review and publish
GET  /academy/courses/{id}/lessons/{n}   the posts made for lesson n
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Context, require
from app.models import Course, Post, SocialAccount
from app.services import academy, audit
from app.services.jobs import enqueue

router = APIRouter(prefix="/academy", tags=["academy"])

LEVELS = ("beginner", "intermediate", "advanced")


def _bad(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(code, msg)


async def _course(ctx: Context, cid: uuid.UUID) -> Course:
    c = await ctx.session.get(Course, cid)
    if c is None:
        raise _bad("Course not found.", 404)
    return c


async def _post_counts(ctx: Context, cids: list[uuid.UUID]) -> dict[str, dict[str, dict[str, int]]]:
    """course id → lesson n → status → count."""
    if not cids:
        return {}
    ids = {str(c) for c in cids}
    rows = (await ctx.session.execute(select(Post.meta, Post.status).where(Post.meta["course_id"].as_string().in_(ids)))).all()
    out: dict[str, dict[str, dict[str, int]]] = {}
    for meta, st in rows:
        m = meta or {}
        d = out.setdefault(str(m.get("course_id")), {}).setdefault(str(m.get("lesson")), {})
        d[st] = d.get(st, 0) + 1
    return out


def _out(c: Course, counts: dict | None = None, full: bool = False) -> dict:
    items = academy.lessons(c)
    st = c.settings or {}
    made = st.get("made") or {}
    counts = counts or {}
    published = sum(1 for n in made if (counts.get(n) or {}).get("published"))
    d = {"id": str(c.id), "title": c.title, "catalog_id": c.catalog_id, "topic": c.topic, "audience": c.audience,
         "level": c.level, "status": c.status, "languages": c.languages or [], "formats": c.formats or {},
         "account_ids": c.account_ids or {}, "next_lesson": c.next_lesson, "lessons_total": len(items),
         "modules_total": len((c.outline or {}).get("modules") or []), "lessons_made": len(made),
         "lessons_published": published, "outline_status": st.get("outline_status"), "outline_error": st.get("outline_error"),
         "created_at": c.created_at.isoformat() if c.created_at else None}
    if full:
        d["outline"] = c.outline or {"modules": []}
        d["lessons"] = [{**x, "made_at": made.get(str(x["n"])), "posts": counts.get(str(x["n"])) or {}} for x in items]
    return d


@router.get("/catalog")
async def catalog(ctx: Context = Depends(require("content.view"))):
    accounts = (await ctx.session.scalars(select(SocialAccount).order_by(SocialAccount.platform, SocialAccount.name))).all()
    acc = [{"id": str(a.id), "platform": a.platform, "name": a.name or a.handle or a.platform, "status": a.status,
            "formats": [f for f in academy.FORMATS if academy.account_fits(f, a.platform)]} for a in accounts]
    return {"groups": [{"name": g, "items": [{"id": i, "title": t, "topic": s} for i, t, s in items]}
                       for g, items in academy.CATALOG.items()],
            "languages": [{"id": k, "name": v["name"], "voice": v["voice"]} for k, v in academy.LANGS.items()],
            "formats": [{"id": k, "label": v["label"], "youtube_only": academy.format_platforms(k) is not None}
                        for k, v in academy.FORMATS.items()],
            "levels": list(LEVELS), "accounts": acc}


class CourseIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    catalog_id: str | None = Field(None, max_length=60)
    topic: str = Field("", max_length=2000)
    audience: str = Field("", max_length=300)
    level: str = "beginner"
    languages: list[str] = Field(default_factory=lambda: ["en", "hi"])
    formats: dict[str, bool] = Field(default_factory=lambda: {"short": True, "lesson": True, "carousel": True})
    account_ids: dict[str, list[str]] = Field(default_factory=dict)


class CoursePatch(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=200)
    topic: str | None = Field(None, max_length=2000)
    audience: str | None = Field(None, max_length=300)
    level: str | None = None
    languages: list[str] | None = None
    formats: dict[str, bool] | None = None
    account_ids: dict[str, list[str]] | None = None
    outline: dict | None = None
    next_lesson: int | None = Field(None, ge=1)
    status: str | None = None


async def _clean_accounts(ctx: Context, raw: dict[str, list[str]]) -> dict[str, list[str]]:
    accounts = {str(a.id): a for a in (await ctx.session.scalars(select(SocialAccount))).all()}
    out: dict[str, list[str]] = {}
    for fmt, ids in (raw or {}).items():
        if fmt not in academy.FORMATS:
            continue
        good = [i for i in dict.fromkeys(ids or []) if i in accounts and academy.account_fits(fmt, accounts[i].platform)]
        out[fmt] = good
    return out


def _check(level: str | None, languages: list[str] | None, formats: dict | None) -> None:
    if level is not None and level not in LEVELS:
        raise _bad("Level must be beginner, intermediate or advanced.")
    if languages is not None and (not languages or any(x not in academy.LANGS for x in languages)):
        raise _bad("Choose English, Hindi or both.")
    if formats is not None and (any(k not in academy.FORMATS for k in formats) or not any(formats.values())):
        raise _bad("Choose at least one format.")


@router.get("/courses")
async def list_courses(ctx: Context = Depends(require("content.view"))):
    courses = list((await ctx.session.scalars(select(Course).order_by(Course.created_at.desc()))).all())
    counts = await _post_counts(ctx, [c.id for c in courses])
    return {"items": [_out(c, counts.get(str(c.id))) for c in courses]}


@router.post("/courses", status_code=201)
async def create_course(body: CourseIn, ctx: Context = Depends(require("content.create"))):
    _check(body.level, body.languages, body.formats)
    topic = body.topic.strip()
    if not topic and body.catalog_id in academy.CATALOG_BY_ID:
        topic = academy.CATALOG_BY_ID[body.catalog_id][2]
    c = Course(id=uuid.uuid4(), organization_id=ctx.org_id, title=body.title.strip(), catalog_id=body.catalog_id,
               topic=topic, audience=body.audience.strip(), level=body.level, status="draft",
               languages=list(dict.fromkeys(body.languages)), formats={k: bool(v) for k, v in body.formats.items()},
               account_ids=await _clean_accounts(ctx, body.account_ids), outline={}, next_lesson=1,
               settings={"outline_status": "writing"})
    ctx.session.add(c)
    await ctx.session.flush()
    await enqueue(ctx.session, "academy.outline", ctx.org_id, {"course_id": str(c.id)}, dedupe_key=f"outline:{c.id}",
                  created_by=ctx.user.id)
    audit.record(ctx.session, action="academy.course_created", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="course", entity_id=c.id, summary=f"Created the course “{c.title}”", ip=ctx.ip)
    await ctx.session.commit()
    return _out(c, {}, full=True)


@router.get("/courses/{cid}")
async def get_course(cid: uuid.UUID, ctx: Context = Depends(require("content.view"))):
    c = await _course(ctx, cid)
    counts = await _post_counts(ctx, [c.id])
    return _out(c, counts.get(str(c.id)), full=True)


@router.patch("/courses/{cid}")
async def update_course(cid: uuid.UUID, body: CoursePatch, ctx: Context = Depends(require("content.create"))):
    c = await _course(ctx, cid)
    _check(body.level, body.languages, body.formats)
    for f in ("title", "topic", "audience", "level", "next_lesson"):
        v = getattr(body, f)
        if v is not None:
            setattr(c, f, v.strip() if isinstance(v, str) else v)
    if body.languages is not None:
        c.languages = list(dict.fromkeys(body.languages))
    if body.formats is not None:
        c.formats = {k: bool(v) for k, v in body.formats.items()}
    if body.account_ids is not None:
        c.account_ids = await _clean_accounts(ctx, body.account_ids)
    if body.outline is not None:
        outline = academy.clean_outline(body.outline)
        if not outline["modules"]:
            raise _bad("The outline needs at least one module with a lesson.")
        c.outline = outline
    if body.status is not None:
        if body.status not in ("active", "done"):
            raise _bad("Status must be active or done.")
        c.status = body.status
    await ctx.session.commit()
    counts = await _post_counts(ctx, [c.id])
    return _out(c, counts.get(str(c.id)), full=True)


@router.delete("/courses/{cid}", status_code=204)
async def delete_course(cid: uuid.UUID, ctx: Context = Depends(require("content.create"))):
    c = await _course(ctx, cid)
    audit.record(ctx.session, action="academy.course_deleted", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="course", entity_id=c.id, summary=f"Deleted the course “{c.title}”", ip=ctx.ip)
    await ctx.session.delete(c)  # posts already made stay in Posts
    await ctx.session.commit()


@router.post("/courses/{cid}/outline")
async def replan_outline(cid: uuid.UUID, ctx: Context = Depends(require("content.create"))):
    c = await _course(ctx, cid)
    c.settings = {**(c.settings or {}), "outline_status": "writing", "outline_error": None}
    job = await enqueue(ctx.session, "academy.outline", ctx.org_id, {"course_id": str(c.id)},
                        dedupe_key=f"outline:{c.id}:{uuid.uuid4().hex[:8]}", created_by=ctx.user.id)
    await ctx.session.commit()
    return {"job_id": str(job.id), "outline_status": "writing"}


class MakeIn(BaseModel):
    formats: list[str] | None = None
    languages: list[str] | None = None


@router.post("/courses/{cid}/lessons/{n}")
async def make_lesson(cid: uuid.UUID, n: int, body: MakeIn | None = None, ctx: Context = Depends(require("content.create"))):
    c = await _course(ctx, cid)
    body = body or MakeIn()
    if body.formats is not None and any(f not in academy.FORMATS for f in body.formats):
        raise _bad("Unknown format.")
    if body.languages is not None and any(x not in academy.LANGS for x in body.languages):
        raise _bad("Unknown language.")
    try:
        posts = await academy.make_lesson(ctx.session, c, n, formats=body.formats, languages=body.languages)
    except academy.AcademyError as e:
        raise _bad(str(e)) from None
    audit.record(ctx.session, action="academy.lesson_made", organization_id=ctx.org_id, actor=ctx.user,
                 entity_type="course", entity_id=c.id, summary=f"Made lesson {n} of “{c.title}” ({len(posts)} posts)", ip=ctx.ip)
    await ctx.session.commit()
    return {"lesson": n, "post_ids": [str(p.id) for p in posts], "count": len(posts), "next_lesson": c.next_lesson}


@router.get("/courses/{cid}/lessons/{n}")
async def lesson_posts(cid: uuid.UUID, n: int, ctx: Context = Depends(require("content.view"))):
    c = await _course(ctx, cid)
    info = academy.lesson(c, n)
    if info is None:
        raise _bad(f"This course has no lesson {n}.", 404)
    rows = (await ctx.session.scalars(select(Post).where(Post.meta["course_id"].as_string() == str(c.id))
                                      .order_by(Post.created_at))).all()
    posts = [p for p in rows if str((p.meta or {}).get("lesson")) == str(n)]
    return {"lesson": info, "posts": [{"id": str(p.id), "platform": p.platform, "status": p.status, "post_type": p.post_type,
                                       "format": (p.meta or {}).get("course_format"), "lang": (p.meta or {}).get("lang"),
                                       "title": p.title, "error": p.error, "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
                                       "published_at": p.published_at.isoformat() if getattr(p, "published_at", None) else None}
                                      for p in posts]}
