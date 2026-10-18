"""Social presence audit: one row per run (scores and the collected profile data) and its suggestions.

Both tables are tenant-owned (organization_id + row-level security, migration 0015_presence).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class PresenceRun(IdMixin, TenantMixin, Base):
    """A finished social presence audit. profiles = per-profile data, checks and sub-scores (see services/presence)."""

    __tablename__ = "presence_runs"

    trigger: Mapped[str] = mapped_column(String(12), default="manual")  # manual | weekly
    score: Mapped[int | None] = mapped_column(Integer)
    grade: Mapped[str | None] = mapped_column(String(40))
    parts: Mapped[list] = mapped_column(JSON, default=list)  # overall sub-scores [{key, label, weight, score, detail}]
    profiles: Mapped[list] = mapped_column(JSON, default=list)
    coverage: Mapped[list] = mapped_column(JSON, default=list)  # [{platform, label, weight, present}]
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # counts, website, resolved suggestions, notes
    narrative: Mapped[str | None] = mapped_column(Text)
    narrative_source: Mapped[str | None] = mapped_column(String(10))  # ai | rules
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PresenceSuggestion(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "presence_suggestions"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("presence_runs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(300))  # stable across runs, so "done" can carry over
    rank: Mapped[int] = mapped_column(Integer, default=0)
    profile_key: Mapped[str | None] = mapped_column(String(300))
    platform: Mapped[str | None] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default="medium")  # high | medium | low
    category: Mapped[str] = mapped_column(String(30), default="completeness")
    points: Mapped[float] = mapped_column(Float, default=0)  # rough score gain if fixed (for ordering)
    action: Mapped[dict | None] = mapped_column(JSON)  # {to | href, label}
    source: Mapped[str] = mapped_column(String(10), default="rules")  # rules | ai
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)  # open | done
    reopened: Mapped[bool] = mapped_column(Boolean, default=False)  # marked done before, but the check still fails
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_changed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


TENANT_TABLES_PRESENCE = ["presence_runs", "presence_suggestions"]
