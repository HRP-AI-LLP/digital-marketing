"""Deep research, daily idea briefs and occasion settings. All tables are tenant-owned (organization_id + RLS)."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class ResearchSettings(IdMixin, TenantMixin, TimestampMixin, Base):
    """One row per organization. Missing row = defaults (see app/services/research.py DEFAULTS)."""

    __tablename__ = "research_settings"
    __table_args__ = (UniqueConstraint("organization_id"),)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    run_time: Mapped[str] = mapped_column(String(5), default="07:00")  # HH:MM in the org's time zone
    depth: Mapped[str] = mapped_column(String(20), default="standard")  # quick | standard | deep
    ideas_per_day: Mapped[int] = mapped_column(Integer, default=8)
    # themes {key: bool}, custom_themes [{label, focus}], keywords [], blocked_domains [], trusted_domains [],
    # excluded_topics [], auto_topics {enabled, min_score, max_per_day}, occasions {...}
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # verified variable occasion dates: {"<key>:<year>": {date, status, source_url, checked_at}}
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ResearchRun(IdMixin, TenantMixin, TimestampMixin, Base):
    """One research run. `run_key` is "daily:YYYY-MM-DD" for scheduled runs so each day runs once."""

    __tablename__ = "research_runs"
    __table_args__ = (UniqueConstraint("organization_id", "run_key"),)

    run_key: Mapped[str | None] = mapped_column(String(40))
    run_date: Mapped[str] = mapped_column(String(10), index=True)  # local date YYYY-MM-DD
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # scheduled | manual
    depth: Mapped[str] = mapped_column(String(20), default="standard")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|partial|failed
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{at, stage, message, level}]
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # intermediate stage results (resume after a retry)
    error: Mapped[str | None] = mapped_column(Text)


class ResearchSource(IdMixin, TenantMixin, Base):
    """A web page found (and possibly read) during a run. Used as evidence and to prefer fresh sources."""

    __tablename__ = "research_sources"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_runs.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_key: Mapped[str] = mapped_column(String(300), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[str | None] = mapped_column(String(10))
    snippet: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[str | None] = mapped_column(String(60))
    query: Mapped[str | None] = mapped_column(Text)
    fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class IdeaBrief(IdMixin, TenantMixin, TimestampMixin, Base):
    """A researched content idea with evidence, formats and scores."""

    __tablename__ = "idea_briefs"

    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_runs.id", ondelete="SET NULL"), index=True)
    run_date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(Text)
    angle: Mapped[str | None] = mapped_column(Text)
    why_now: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(String(60), default="company")
    audience: Mapped[str | None] = mapped_column(Text)
    facts: Mapped[list] = mapped_column(JSON, default=list)  # [{fact, source_url, publisher, date}]
    formats: Mapped[list] = mapped_column(JSON, default=list)  # [{format, note}]
    article: Mapped[dict] = mapped_column(JSON, default=dict)  # {keyword, secondary_keywords, outline}
    cta: Mapped[dict] = mapped_column(JSON, default=dict)  # {text, url}
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)  # relevance, timeliness, novelty, business_value, evidence
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)  # new|saved|used|dismissed
    occasion: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))  # {key, name, date, category, region, confidence}
    pillar_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    used: Mapped[list] = mapped_column(JSON, default=list)  # [{kind: post|article|topic, id, at}]
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(160))
