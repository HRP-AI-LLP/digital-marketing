"""Publishing SEO articles to the company website (WordPress or a signed webhook) and the redirects that
slug changes need. Both tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_SITE)."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin


class SitePublication(IdMixin, TenantMixin, TimestampMixin, Base):
    """Where an article lives on one website connection (a `website` ProviderConfig)."""

    __tablename__ = "site_publications"
    __table_args__ = (UniqueConstraint("article_id", "provider_id"),)

    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("seo_articles.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("provider_configs.id", ondelete="CASCADE"), index=True)
    target: Mapped[str] = mapped_column(String(20))  # wordpress | webhook
    # none | publishing | draft | published | scheduled | unpublished | error
    status: Mapped[str] = mapped_column(String(20), default="none")
    remote_id: Mapped[str | None] = mapped_column(String(100))
    remote_url: Mapped[str | None] = mapped_column(Text)
    published_slug: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media_remote_id: Mapped[str | None] = mapped_column(String(100))  # WordPress media id of the featured image
    media_source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # MediaAsset that was uploaded
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    error: Mapped[str | None] = mapped_column(Text)
    # options {status, categories, tags, author, indexnow}, warnings [{code, message, help}], seo_plugin,
    # check {ok, status_code, title, canonical, ...}, indexnow {ok, message, at}
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class SiteRedirect(IdMixin, TenantMixin, TimestampMixin, Base):
    """old path → new path after a published article's slug changed."""

    __tablename__ = "site_redirects"

    article_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("seo_articles.id", ondelete="SET NULL"), index=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("provider_configs.id", ondelete="SET NULL"))
    from_path: Mapped[str] = mapped_column(Text)
    to_path: Mapped[str] = mapped_column(Text)
    # pending (not pushed yet) | pushed (Redirection plugin) | sent (in the webhook payload) | manual | done | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    remote_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
