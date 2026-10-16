"""Analytics extension tables: growth snapshots, competitor benchmarks, recommendations and reports.

All tables are tenant-owned (organization_id + row-level security, see TENANT_TABLES_ANALYTICS and
migration 0005). Metric columns are nullable on purpose: None means "the platform doesn't share this
number", which the UI shows differently from a real zero.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class AccountMetric(IdMixin, TenantMixin, Base):
    """One snapshot per connected social account per local day (the latest collection of the day wins)."""

    __tablename__ = "account_metrics"
    __table_args__ = (UniqueConstraint("organization_id", "social_account_id", "day", name="uq_account_metrics_account_day"),)

    social_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("social_accounts.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    day: Mapped[date] = mapped_column(Date, index=True)
    followers: Mapped[int | None] = mapped_column(BigInteger)
    following: Mapped[int | None] = mapped_column(BigInteger)
    posts_count: Mapped[int | None] = mapped_column(BigInteger)
    reach: Mapped[int | None] = mapped_column(BigInteger)
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    profile_views: Mapped[int | None] = mapped_column(BigInteger)
    engagement: Mapped[int | None] = mapped_column(BigInteger)
    views: Mapped[int | None] = mapped_column(BigInteger)  # lifetime views where the platform reports them (YouTube)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[list] = mapped_column(JSON, default=list)  # what wasn't available and why
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompetitorMetric(IdMixin, TenantMixin, Base):
    """Public numbers for one competitor on one channel (instagram | youtube | bluesky | mastodon | website) per day."""

    __tablename__ = "competitor_metrics"
    __table_args__ = (UniqueConstraint("organization_id", "competitor_id", "channel", "day",
                                       name="uq_competitor_metrics_channel_day"),)

    competitor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("competitors.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    handle: Mapped[str | None] = mapped_column(String(300))
    day: Mapped[date] = mapped_column(Date, index=True)
    followers: Mapped[int | None] = mapped_column(BigInteger)
    posts_count: Mapped[int | None] = mapped_column(BigInteger)
    posts_30d: Mapped[int | None] = mapped_column(Integer)
    avg_engagement: Mapped[float | None] = mapped_column(Float)  # likes + comments (+ reposts) per recent post
    views: Mapped[int | None] = mapped_column(BigInteger)
    score: Mapped[int | None] = mapped_column(Integer)  # website: PageSpeed mobile performance
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Recommendation(IdMixin, TenantMixin, TimestampMixin, Base):
    """A growth tip from the advisor. action = optional one-click change {type, params, label}."""

    __tablename__ = "analytics_recommendations"

    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(Text)
    why: Mapped[str | None] = mapped_column(Text)
    action_text: Mapped[str | None] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(String(10), default="medium")  # high | medium | low
    effort: Mapped[str] = mapped_column(String(10), default="medium")  # low | medium | high
    category: Mapped[str] = mapped_column(String(30), default="content")
    action: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | rules
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)  # new | done | dismissed
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_changed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    result: Mapped[str | None] = mapped_column(Text)  # what the one-click action did


class ReportSchedule(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "report_schedules"

    name: Mapped[str] = mapped_column(String(160))
    frequency: Mapped[str] = mapped_column(String(10), default="weekly")  # weekly | monthly
    weekday: Mapped[int] = mapped_column(Integer, default=0)  # Mon=0, weekly only
    month_day: Mapped[int] = mapped_column(Integer, default=1)  # 1–28, monthly only
    time: Mapped[str] = mapped_column(String(5), default="09:00")  # HH:MM in the org's time zone
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class AnalyticsReport(IdMixin, TenantMixin, Base):
    __tablename__ = "analytics_reports"

    title: Mapped[str] = mapped_column(String(300))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued | ready | failed
    trigger: Mapped[str] = mapped_column(String(12), default="manual")  # manual | schedule | send_now
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("report_schedules.id", ondelete="SET NULL"))
    path: Mapped[str | None] = mapped_column(Text)  # relative to storage/
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_error: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


TENANT_TABLES_ANALYTICS = [
    "account_metrics", "competitor_metrics", "analytics_recommendations", "report_schedules", "analytics_reports",
]
