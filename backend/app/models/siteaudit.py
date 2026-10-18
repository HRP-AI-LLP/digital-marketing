"""Deep technical site audit: settings, runs, crawled pages and issue occurrences (tenant-owned, RLS)."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class SiteAuditSettings(IdMixin, TenantMixin, TimestampMixin, Base):
    """One row per organization. Missing row = defaults (app/services/siteaudit/settings.py)."""

    __tablename__ = "site_audit_settings"
    __table_args__ = (UniqueConstraint("organization_id"),)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # weekly schedule
    # start_url, max_pages, max_depth, include [], exclude [], user_agent, psi_pages, weekday, run_time,
    # drop_threshold, check_external
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class SiteAuditRun(IdMixin, TenantMixin, TimestampMixin, Base):
    """One audit. `run_key` is "weekly:YYYY-Www" for scheduled runs so each week runs once."""

    __tablename__ = "site_audit_runs"
    __table_args__ = (UniqueConstraint("organization_id", "run_key"),)

    run_key: Mapped[str | None] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual | scheduled
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|failed
    start_url: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score: Mapped[int | None] = mapped_column(Integer)
    pages_crawled: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[int] = mapped_column(Integer, default=0)
    notices: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # effective settings used
    # summary: {check_id: {"count": n, "new": n, "fixed": n}}; stats: crawl stats, new/fixed totals
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    # site: robots, sitemaps, llms_txt, ai_access, https, hosts; performance: PageSpeed results
    site: Mapped[dict] = mapped_column(JSON, default=dict)
    performance: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SiteAuditPage(IdMixin, TenantMixin, Base):
    """A URL fetched during a run (HTML pages, redirects and errors alike)."""

    __tablename__ = "site_audit_pages"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("site_audit_runs.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str | None] = mapped_column(String(120))
    depth: Mapped[int | None] = mapped_column(Integer)
    response_ms: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    indexable: Mapped[bool] = mapped_column(Boolean, default=False)
    in_sitemap: Mapped[bool] = mapped_column(Boolean, default=False)
    inlinks: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[int] = mapped_column(Integer, default=0)
    notices: Mapped[int] = mapped_column(Integer, default=0)
    text_ratio: Mapped[float | None] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # every fact the crawler recorded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SiteAuditIssue(IdMixin, TenantMixin, Base):
    """One occurrence of a check on one URL (site-wide checks use the site's home URL)."""

    __tablename__ = "site_audit_issues"

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("site_audit_runs.id", ondelete="CASCADE"), index=True)
    check_id: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(10))  # error | warning | notice
    url: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    is_new: Mapped[bool] = mapped_column(Boolean, default=False)
