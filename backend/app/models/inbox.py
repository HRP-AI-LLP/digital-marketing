"""Unified inbox: comments, replies and mentions from connected accounts, plus social-listening mentions.

Both tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_INBOX).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin

INBOX_KINDS = ("comment", "reply", "mention")
INBOX_STATUSES = ("new", "read", "replied", "archived")
SENTIMENTS = ("positive", "neutral", "negative", "question")


class InboxItem(IdMixin, TenantMixin, TimestampMixin, Base):
    """One comment, reply or mention.

    source: "account" (collected from a connected account: comments on our posts, replies, direct mentions)
            or "listening" (keyword monitoring: web, news, Bluesky / Mastodon / YouTube / Instagram hashtag search).
    dedupe_key: "<social_account_id>:<external_id>" for account items, "listen:<platform>:<id or url>" for listening.
    thread_key: groups a conversation (the top-level comment's external id, or the root post for Bluesky threads).
    """

    __tablename__ = "inbox_items"
    __table_args__ = (UniqueConstraint("organization_id", "dedupe_key", name="uq_inbox_items_org_dedupe"),)

    source: Mapped[str] = mapped_column(String(20), default="account", index=True)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="comment", index=True)
    dedupe_key: Mapped[str] = mapped_column(String(600))
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("social_accounts.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="SET NULL"), index=True)
    external_post_id: Mapped[str | None] = mapped_column(String(300))
    external_post_url: Mapped[str | None] = mapped_column(Text)
    post_snippet: Mapped[str | None] = mapped_column(Text)

    external_id: Mapped[str | None] = mapped_column(String(500))
    parent_external_id: Mapped[str | None] = mapped_column(String(500))
    thread_key: Mapped[str | None] = mapped_column(String(500), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("inbox_items.id", ondelete="SET NULL"))
    url: Mapped[str | None] = mapped_column(Text)

    author_name: Mapped[str | None] = mapped_column(String(300))
    author_handle: Mapped[str | None] = mapped_column(String(300))
    author_avatar_url: Mapped[str | None] = mapped_column(Text)
    author_url: Mapped[str | None] = mapped_column(Text)
    author_external_id: Mapped[str | None] = mapped_column(String(300))
    text: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)  # listening results (web/news/video title)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    language: Mapped[str | None] = mapped_column(String(20))

    is_own: Mapped[bool] = mapped_column(Boolean, default=False)  # written by us (our reply)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)  # new|read|replied|archived
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    sentiment: Mapped[str | None] = mapped_column(String(20))  # positive|neutral|negative|question
    sentiment_source: Mapped[str | None] = mapped_column(String(20))  # ai|heuristic|manual
    priority: Mapped[bool] = mapped_column(Boolean, default=False)  # urgent: needs a fast answer
    keyword: Mapped[str | None] = mapped_column(String(200))  # listening: which tracked keyword matched
    engagement: Mapped[int | None] = mapped_column(Integer)  # likes + reposts + replies, or reach where offered
    ai_suggestions: Mapped[list] = mapped_column(JSON, default=list)

    reply_text: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    reply_external_id: Mapped[str | None] = mapped_column(String(500))
    reply_error: Mapped[str | None] = mapped_column(Text)
    liked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class InboxSettings(IdMixin, TenantMixin, TimestampMixin, Base):
    """Listening settings and sync state for one organization.

    state: {"accounts": {"<account_id>": {"last_synced_at", "ok", "message", "missing_permission", "reconnect",
                                          "granted": [...]}},
            "sources": {"<source>": {"last_run_at", "ok", "message", "found"}},
            "last_digest_at": iso, "ig_hashtags": {"tag": "id"}}
    """

    __tablename__ = "inbox_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_inbox_settings_org"),)

    keywords: Mapped[list] = mapped_column(JSON, default=list)
    excluded: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[dict] = mapped_column(JSON, default=dict)  # {"web": true, "news": true, ...}
    listening_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
