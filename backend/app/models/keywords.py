"""Keyword research, competitor keyword gap and daily rank tracking. All tables are tenant-owned (RLS)."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


class KeywordSettings(IdMixin, TenantMixin, TimestampMixin, Base):
    """One row per organization. Missing row = defaults (see app/services/keywords/service.py DEFAULTS)."""

    __tablename__ = "keyword_settings"
    __table_args__ = (UniqueConstraint("organization_id"),)

    # location_code, language_code, device, daily_budget_usd, max_tracked, check_time, alerts {...},
    # cache_days, research_limit, gap_limit, our_domain
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # recent seeds, last daily check, ...


class KeywordCache(IdMixin, TenantMixin, Base):
    """Cached provider responses (saves money: the same request inside `expires_at` is served from here)."""

    __tablename__ = "keyword_cache"
    __table_args__ = (UniqueConstraint("organization_id", "cache_key"),)

    provider: Mapped[str] = mapped_column(String(40))
    endpoint: Mapped[str] = mapped_column(String(120))
    cache_key: Mapped[str] = mapped_column(String(64))  # sha256 of provider + endpoint + request
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    response: Mapped[dict | list] = mapped_column(JSON, default=dict)  # the task "result" list as returned
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProviderSpend(IdMixin, TenantMixin, Base):
    """Money spent on keyword data calls (one row per paid call; cache hits are not recorded)."""

    __tablename__ = "provider_spend"

    provider: Mapped[str] = mapped_column(String(40), index=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(40), default="research")  # research | gap | rank | cluster | ...
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    day: Mapped[str] = mapped_column(String(10), index=True)  # org-local date YYYY-MM-DD
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class KeywordReport(IdMixin, TenantMixin, TimestampMixin, Base):
    """The result of one research or gap run (kept so the page can reopen it without paying again)."""

    __tablename__ = "keyword_reports"

    kind: Mapped[str] = mapped_column(String(20), index=True)  # research | gap
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | running | done | failed
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    rows: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # source, estimated, cost_usd, cached, counts, notes
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class KeywordList(IdMixin, TenantMixin, TimestampMixin, Base):
    """A saved list of keywords with their metrics and (optionally) topic clusters."""

    __tablename__ = "keyword_lists"

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # [{keyword, volume, kd, cpc, competition, intent, trend:[...], serp_features:[...], source, estimated, added_at}]
    items: Mapped[list] = mapped_column(JSON, default=list)
    # [{id, name, pillar, keywords:[...], volume, kd, method}]
    clusters: Mapped[list] = mapped_column(JSON, default=list)
    cluster_meta: Mapped[dict] = mapped_column(JSON, default=dict)  # method, at, cost_usd, notes
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class TrackedKeyword(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "tracked_keywords"
    __table_args__ = (UniqueConstraint("organization_id", "keyword", "location_code", "language_code", "device"),)

    keyword: Mapped[str] = mapped_column(String(200))
    target_url: Mapped[str | None] = mapped_column(Text)
    location_code: Mapped[int] = mapped_column(Integer, default=2356)
    language_code: Mapped[str] = mapped_column(String(10), default="en")
    device: Mapped[str] = mapped_column(String(10), default="desktop")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    volume: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int | None] = mapped_column(Integer)  # latest (None = not in the top 100)
    previous_position: Mapped[int | None] = mapped_column(Integer)
    best_position: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)  # our ranking URL at the last check
    gsc_position: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(20))  # dataforseo | gsc
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class RankCheck(IdMixin, TenantMixin, Base):
    """One day's position for one tracked keyword."""

    __tablename__ = "rank_checks"
    __table_args__ = (UniqueConstraint("tracked_keyword_id", "check_date"),)

    tracked_keyword_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tracked_keywords.id", ondelete="CASCADE"), index=True)
    check_date: Mapped[str] = mapped_column(String(10), index=True)  # org-local date
    position: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)
    gsc_position: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="dataforseo")
    serp_features: Mapped[list] = mapped_column(JSON, default=list)
    top_urls: Mapped[list] = mapped_column(JSON, default=list)  # first 10 organic URLs (clustering, competitor view)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


TENANT_TABLES_KEYWORDS = ["keyword_settings", "keyword_cache", "provider_spend", "keyword_reports", "keyword_lists",
                          "tracked_keywords", "rank_checks"]
