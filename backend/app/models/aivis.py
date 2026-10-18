"""AI search visibility: tracked prompts, runs against answer engines, analysed answers, plus llms.txt.

All tables are tenant-owned (organization_id + RLS).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class AivisSettings(IdMixin, TenantMixin, TimestampMixin, Base):
    """One row per organization. Missing row = defaults (see app/services/aivis.py DEFAULTS)."""

    __tablename__ = "aivis_settings"
    __table_args__ = (UniqueConstraint("organization_id"),)

    # enabled, weekday (0=Mon), run_time "HH:MM", engines [{key, provider_id, model, web, label}], aliases [],
    # own_domains [], drop_alert_points
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # llms {text, full_text, generated_at, edited_at, source}, site_check {...}, last_alert_run_id
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class AivisPrompt(IdMixin, TenantMixin, TimestampMixin, Base):
    """A question buyers ask AI assistants, tracked on every run."""

    __tablename__ = "aivis_prompts"

    text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(80), default="General")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # {reason}


class AivisRun(IdMixin, TenantMixin, TimestampMixin, Base):
    """One visibility check. `run_key` is "weekly:YYYY-Www" for scheduled runs so each week runs once."""

    __tablename__ = "aivis_runs"
    __table_args__ = (UniqueConstraint("organization_id", "run_key"),)

    run_key: Mapped[str | None] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # scheduled | manual
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|partial|failed
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    engines: Mapped[list] = mapped_column(JSON, default=list)  # snapshot of the engines used
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # computed when the run finishes
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{at, message, level}]
    error: Mapped[str | None] = mapped_column(Text)


class AivisAnswer(IdMixin, TenantMixin, Base):
    """One engine's answer to one prompt in one run, with its analysis."""

    __tablename__ = "aivis_answers"
    __table_args__ = (UniqueConstraint("run_id", "prompt_id", "engine_key"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aivis_runs.id", ondelete="CASCADE"), index=True)
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("aivis_prompts.id", ondelete="SET NULL"), index=True)
    prompt_text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(80), default="General")
    engine_key: Mapped[str] = mapped_column(String(300))
    engine_label: Mapped[str] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(160))
    web: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | failed
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)  # [{url, domain, title, own}]
    mentioned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    position: Mapped[int | None] = mapped_column(Integer)  # 1-based rank among brands in the answer
    sentiment: Mapped[str | None] = mapped_column(String(10))  # positive | neutral | negative
    competitors: Mapped[list] = mapped_column(JSON, default=list)  # names of competitors mentioned
    brands: Mapped[list] = mapped_column(JSON, default=list)  # every brand in order of appearance
    own_cited: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)  # {method, snippet, sentiment_reason, extracted}
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
