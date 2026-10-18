"""Link-in-bio pages (with anonymous daily click counts) and design-tool connections (Canva).

All three tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_NETWORKS).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class LinkPage(IdMixin, TenantMixin, TimestampMixin, Base):
    """One public link-in-bio page per organization, served at /l/<slug>.

    links: ordered [{id, title, url, icon, thumbnail_asset_id, highlight, enabled}]
    theme: {background, accent, text, button_style ("filled"|"outline"|"soft"), font}
    """

    __tablename__ = "link_pages"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_link_pages_organization_id"),)

    slug: Mapped[str] = mapped_column(String(60), unique=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(String(120))
    tagline: Mapped[str | None] = mapped_column(String(300))
    show_logo: Mapped[bool] = mapped_column(Boolean, default=True)
    show_socials: Mapped[bool] = mapped_column(Boolean, default=True)
    show_posts: Mapped[bool] = mapped_column(Boolean, default=False)
    posts_count: Mapped[int] = mapped_column(Integer, default=6)
    links: Mapped[list] = mapped_column(JSON, default=list)
    theme: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class LinkPageStat(IdMixin, TenantMixin, Base):
    """Anonymous counters: one row per page, day and target ("view", a link id, "post:<id>" or "social:<key>").
    No IP addresses, cookies or user agents are stored."""

    __tablename__ = "link_page_stats"
    __table_args__ = (UniqueConstraint("page_id", "day", "target", name="uq_link_page_stats_page_day_target"),)

    page_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("link_pages.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    target: Mapped[str] = mapped_column(String(80))
    count: Mapped[int] = mapped_column(Integer, default=0)


class DesignConnection(IdMixin, TenantMixin, TimestampMixin, Base):
    """A signed-in design tool account (Canva) used to import designs into the media library."""

    __tablename__ = "design_connections"
    __table_args__ = (UniqueConstraint("organization_id", "provider", name="uq_design_connections_org_provider"),)

    provider: Mapped[str] = mapped_column(String(30))  # canva
    external_id: Mapped[str | None] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected | expired
    status_message: Mapped[str | None] = mapped_column(Text)
    tokens_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
