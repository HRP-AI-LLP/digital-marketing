"""Team collaboration: saved inbox replies, internal comments on posts (with @mentions) and post version history.

All three tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_COLLAB).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow

TENANT_TABLES_COLLAB = ["saved_replies", "post_comments", "post_versions"]


class SavedReply(IdMixin, TenantMixin, TimestampMixin, Base):
    """A reusable inbox reply. text may hold variables: {author_name}, {company}, {website}, {my_name},
    {email}, {phone}. platforms: empty = every platform."""

    __tablename__ = "saved_replies"

    title: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class PostComment(IdMixin, TenantMixin, TimestampMixin, Base):
    """An internal team comment on a post. Never published. mentions: list of user ids (strings)."""

    __tablename__ = "post_comments"

    post_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list] = mapped_column(JSON, default=list)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostVersion(IdMixin, TenantMixin, Base):
    """A snapshot of a post's content (title, text, hashtags, link, first comment, media) after a change."""

    __tablename__ = "post_versions"
    __table_args__ = (UniqueConstraint("post_id", "version", name="uq_post_versions_post_version"),)

    post_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
