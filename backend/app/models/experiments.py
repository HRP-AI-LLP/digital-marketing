"""A/B tests: two or three variants of a post, compared on one metric once their results are in.

Both tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_EXPERIMENTS).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin

TENANT_TABLES_EXPERIMENTS = ["experiments", "experiment_posts"]


class Experiment(IdMixin, TenantMixin, TimestampMixin, Base):
    """status: running (variants waiting to publish or for results) | done | cancelled
    mode: time (same account, comparable times) | accounts (same platform, different accounts)
    metric: engagement_rate | clicks | impressions
    result: {state, message, variants: [{post_id, label, value, impressions, engagements, clicks}], leader, verdict,
             difference_pct, notes: [...] }"""

    __tablename__ = "experiments"

    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    mode: Mapped[str] = mapped_column(String(20), default="time")
    metric: Mapped[str] = mapped_column(String(30), default="engagement_rate")
    evaluate_after_days: Mapped[int] = mapped_column(Integer, default=3)
    source_post_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="SET NULL"))
    winner_post_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ExperimentPost(IdMixin, TenantMixin, Base):
    """One variant. kind: original | manual | ai."""

    __tablename__ = "experiment_posts"
    __table_args__ = (UniqueConstraint("experiment_id", "post_id", name="uq_experiment_posts_experiment_post"),)

    experiment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("experiments.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(20), default="manual")
    change: Mapped[str | None] = mapped_column(String(300))  # what differs, e.g. "New hook"
