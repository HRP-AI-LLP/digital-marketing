"""Tracked short links (/r/<code>) with anonymous daily click counts.

Both tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_LINKS). Codes are unique across all
organizations so the public redirect can find a link without knowing the organization first.
No IP addresses, cookies or full user agents are stored: only a day, a coarse referrer domain, the app the
click came from (detected from the user agent, e.g. "linkedin") and mobile/desktop.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class TrackedLink(IdMixin, TenantMixin, TimestampMixin, Base):
    """utm: {utm_source, utm_medium, utm_campaign, utm_content, utm_term} added to target_url on redirect
    (parameters already in target_url win). source: "auto" (wrapped at publish time) | "manual"."""

    __tablename__ = "tracked_links"

    code: Mapped[str] = mapped_column(String(16), unique=True)
    target_url: Mapped[str] = mapped_column(Text)
    utm: Mapped[dict] = mapped_column(JSON, default=dict)
    post_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="SET NULL"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    platform: Mapped[str | None] = mapped_column(String(30))
    label: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(10), default="manual")
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    clicks_total: Mapped[int] = mapped_column(Integer, default=0)
    last_click_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LinkClick(IdMixin, TenantMixin, Base):
    """One counter row per link, day, referrer domain, app and device."""

    __tablename__ = "link_clicks"
    __table_args__ = (UniqueConstraint("link_id", "day", "referrer", "app", "device", name="uq_link_clicks_bucket"),)

    link_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tracked_links.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    referrer: Mapped[str] = mapped_column(String(80), default="direct")
    app: Mapped[str] = mapped_column(String(20), default="browser")
    device: Mapped[str] = mapped_column(String(10), default="desktop")
    count: Mapped[int] = mapped_column(Integer, default=0)
