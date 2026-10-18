"""Web and social listening: tracked topics (queries), the mentions found for them, and per-organization settings.

All three tables are tenant-owned (organization_id + RLS, see TENANT_TABLES_LISTENING).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin

QUERY_KINDS = ("brand", "competitor", "industry", "custom")
MENTION_STATUSES = ("new", "reviewed", "hidden")
MENTION_SENTIMENTS = ("positive", "neutral", "negative")


class ListeningQuery(IdMixin, TenantMixin, TimestampMixin, Base):
    """One topic to listen for.

    terms:          a mention must contain at least one of these words or phrases
    context_terms:  ...and, when given, also one of these (e.g. "HRP" AND ("HRMS" OR "payroll"))
    exclude:        ...and none of these
    languages:      ISO 639-1 codes (empty = any); countries: ISO 3166-1 alpha-2 codes (empty = the org's country)
    sources:        per-query source switches overriding the settings ({"reddit": false}); missing = on
    state:          {"last_run_at", "found_last_run", "sources": {"<source>": {"last_run_at", "ok", "message", "found"}}}
    """

    __tablename__ = "listening_queries"

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="custom", index=True)  # brand|competitor|industry|custom
    terms: Mapped[list] = mapped_column(JSON, default=list)
    context_terms: Mapped[list] = mapped_column(JSON, default=list)
    exclude: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[dict] = mapped_column(JSON, default=dict)
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("competitors.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class ListeningMention(IdMixin, TenantMixin, TimestampMixin, Base):
    """A post, article, comment or video that matched a query.

    dedupe_key:   "u:<sha1 of the canonical URL>" (or "x:<source>:<external id>" when there is no URL)
    content_hash: sha1 of the normalized title/text; catches the same story syndicated under different URLs
    query_ids:    every query that matched (query_id is the first); share of voice counts these
    relevant:     False when the AI (or the keyword prefilter) decided it's about something else
    """

    __tablename__ = "listening_mentions"
    __table_args__ = (UniqueConstraint("organization_id", "dedupe_key", name="uq_listening_mentions_org_dedupe"),)

    query_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("listening_queries.id", ondelete="SET NULL"), index=True)
    query_ids: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(30), index=True)
    external_id: Mapped[str | None] = mapped_column(String(500))
    dedupe_key: Mapped[str] = mapped_column(String(120))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(253), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    author_name: Mapped[str | None] = mapped_column(String(300))
    author_handle: Mapped[str | None] = mapped_column(String(300))
    author_url: Mapped[str | None] = mapped_column(Text)
    author_followers: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    language: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[int | None] = mapped_column(Integer)  # upvotes / points / likes
    comments: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)  # views or followers, when the source offers it
    matched_terms: Mapped[list] = mapped_column(JSON, default=list)

    relevant: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    relevance: Mapped[float | None] = mapped_column(Float)
    sentiment: Mapped[str | None] = mapped_column(String(20), index=True)  # positive|neutral|negative
    sentiment_source: Mapped[str | None] = mapped_column(String(20))  # ai|heuristic|manual
    tags: Mapped[list] = mapped_column(JSON, default=list)
    needs_response: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    response_reason: Mapped[str | None] = mapped_column(String(30))  # question|complaint|recommendation_request
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("competitors.id", ondelete="SET NULL"))
    is_competitor: Mapped[bool] = mapped_column(Boolean, default=False)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(20), default="new", index=True)  # new|reviewed|hidden
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    idea_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class ListeningSettings(IdMixin, TenantMixin, TimestampMixin, Base):
    """One row per organization.

    sources: {"google_news": true, "reddit": false, ...}; missing = the source's default
    digest:  off | daily | weekly
    state:   {"last_run_at", "running", "sources": {"<source>": {"last_run_at", "ok", "message", "found"}},
              "last_digest_at", "spikes": {"volume": iso, "negative": iso}}
    summary: cached "what people are saying" {"generated_at", "days", "summary", "themes": [...], "count"}
    """

    __tablename__ = "listening_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_listening_settings_org"),)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_hours: Mapped[int] = mapped_column(Integer, default=3)
    sources: Mapped[dict] = mapped_column(JSON, default=dict)
    digest: Mapped[str] = mapped_column(String(10), default="weekly")
    spike_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    use_ai: Mapped[bool] = mapped_column(Boolean, default=True)
    mastodon_instance: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


TENANT_TABLES_LISTENING = ["listening_queries", "listening_mentions", "listening_settings"]
