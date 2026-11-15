"""Academy: teaching courses (C, Python, ML, networking …) turned into a series of lessons. Used by hand: you pick a
lesson and the app writes its Shorts, full lesson videos and carousels in English and Hindi as drafts for you to review
and publish (see services/academy.py). Tenant-owned (RLS)."""

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class Course(IdMixin, TenantMixin, TimestampMixin, Base):
    """outline: {"modules": [{"title", "lessons": [{"title", "summary"}]}]} (generated, editable).
    languages: ["en", "hi"]; formats: {"short": bool, "lesson": bool, "carousel": bool};
    account_ids: {"short": [account ids], "lesson": [...], "carousel": [...]} — the accounts each format is made for.
    next_lesson: 1-based number of the lesson suggested next. settings["made"]: {"<n>": ISO time it was last made}.
    status: draft (outline not ready) | active | done."""

    __tablename__ = "courses"

    title: Mapped[str] = mapped_column(String(200))
    catalog_id: Mapped[str | None] = mapped_column(String(60))
    topic: Mapped[str] = mapped_column(Text, default="")
    audience: Mapped[str] = mapped_column(String(300), default="")
    level: Mapped[str] = mapped_column(String(20), default="beginner")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en", "hi"])
    formats: Mapped[dict] = mapped_column(JSON, default=lambda: {"short": True, "lesson": True, "carousel": True})
    account_ids: Mapped[dict] = mapped_column(JSON, default=dict)  # {"short": [ids], "lesson": [ids], "carousel": [ids]}
    outline: Mapped[dict] = mapped_column(JSON, default=dict)
    next_lesson: Mapped[int] = mapped_column(Integer, default=1)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


TENANT_TABLES_ACADEMY = ["courses"]
