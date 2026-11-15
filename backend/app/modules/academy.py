"""Academy jobs: writing a course outline (a long AI answer, so it runs in the background and waits out rate limits)."""

from __future__ import annotations

import uuid

from app.models import Course
from app.services import academy
from app.services.jobs import JobContext, PermanentError, ai_wait, handler


@handler("academy.outline")
async def write_outline(ctx: JobContext) -> dict:
    s = ctx.session
    cid = uuid.UUID(str((ctx.payload or {}).get("course_id")))
    course = await s.get(Course, cid)
    if course is None:
        return {"skipped": "The course no longer exists."}
    await ctx.progress(f"Planning the lessons of {course.title}…")
    try:
        outline = await academy.generate_outline(s, course)
    except academy.AcademyError as e:
        course.settings = {**(course.settings or {}), "outline_status": "error", "outline_error": str(e)}
        await s.commit()
        raise PermanentError(str(e)) from None
    except Exception as e:
        if ai_wait(e):
            raise  # rate limits only: the job runner tries again once a provider is free
        if ctx.attempt >= 3:
            course.settings = {**(course.settings or {}), "outline_status": "error", "outline_error": str(e)[:500]}
            await s.commit()
            raise PermanentError(f"Couldn't plan the course: {e}") from e
        raise
    course.outline = outline
    course.settings = {k: v for k, v in (course.settings or {}).items() if k not in ("outline_status", "outline_error")}
    if course.status == "draft":
        course.status = "active"
    await s.commit()
    return {"course_id": str(course.id), "lessons": len(academy.lessons(course))}
