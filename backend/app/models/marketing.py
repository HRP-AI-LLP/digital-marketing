"""Tables for every functional module. All are tenant-owned (organization_id + RLS)."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantMixin, TimestampMixin, utcnow


# ============================================================ integrations (Phase 1)
class ProviderConfig(IdMixin, TenantMixin, TimestampMixin, Base):
    """A configured external service: LLM, image, video, TTS, stock media, search, media host,
    social app credentials, Google, SMTP, Telegram. Secrets are Fernet-encrypted."""

    __tablename__ = "provider_configs"

    kind: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str | None] = mapped_column(String(300))
    secrets_enc: Mapped[str | None] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    monthly_budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 2))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_message: Mapped[str | None] = mapped_column(Text)


class TaskRoute(IdMixin, TenantMixin, TimestampMixin, Base):
    """Which provider/model handles a task, with fallbacks: chain = [{provider_id, model}, ...]."""

    __tablename__ = "task_routes"
    __table_args__ = (UniqueConstraint("organization_id", "task"),)

    task: Mapped[str] = mapped_column(String(40))
    chain: Mapped[list] = mapped_column(JSON, default=list)


class LlmUsage(IdMixin, TenantMixin, Base):
    __tablename__ = "llm_usage"

    provider_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("provider_configs.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(160))
    task: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20), default="chat")  # chat | embed | image | tts | video
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)  # images, seconds of audio, ...
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ModelStatus(IdMixin, TenantMixin, Base):
    """Health of one model on one configured provider: rate-limit cooldowns, key/billing problems, removed models.

    state: ok | cooldown (limit reached, rests until cooldown_until) | auth | billing | unavailable (model removed)
    """

    __tablename__ = "model_status"
    __table_args__ = (UniqueConstraint("organization_id", "provider_id", "model"),)

    provider_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("provider_configs.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(20), default="ok")
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(300))
    last_error: Mapped[str | None] = mapped_column(Text)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fail_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ============================================================ social accounts
class SocialAccount(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "social_accounts"

    platform: Mapped[str] = mapped_column(String(30), index=True)
    external_id: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    handle: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    profile_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected|expired|error|manual
    status_message: Mapped[str | None] = mapped_column(Text)
    tokens_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    connected_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))


class OAuthState(Base):
    """Short-lived OAuth state; looked up from the callback through a platform session."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    platform: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ============================================================ brand & knowledge (Phase 2)
class BrandProfile(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "brand_profiles"
    __table_args__ = (UniqueConstraint("organization_id"),)

    voice: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(Text)
    do_list: Mapped[list] = mapped_column(JSON, default=list)
    dont_list: Mapped[list] = mapped_column(JSON, default=list)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    ctas: Mapped[list] = mapped_column(JSON, default=list)
    banned_words: Mapped[list] = mapped_column(JSON, default=list)
    colors: Mapped[dict] = mapped_column(JSON, default=dict)
    fonts: Mapped[dict] = mapped_column(JSON, default=dict)
    logo_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    disclaimer: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(20), default="en")
    emoji_policy: Mapped[str] = mapped_column(String(20), default="light")  # none|light|rich
    blackout_dates: Mapped[list] = mapped_column(JSON, default=list)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeSource(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_sources"

    kind: Mapped[str] = mapped_column(String(20))  # website | upload | competitor | note
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|ready|error
    status_message: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeDocument(IdMixin, TenantMixin, Base):
    __tablename__ = "knowledge_documents"

    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(IdMixin, TenantMixin, Base):
    __tablename__ = "knowledge_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(160))


class CompanyProfile(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_profiles"
    __table_args__ = (UniqueConstraint("organization_id"),)

    summary: Mapped[str | None] = mapped_column(Text)
    positioning: Mapped[str | None] = mapped_column(Text)
    industries: Mapped[list] = mapped_column(JSON, default=list)
    audiences: Mapped[list] = mapped_column(JSON, default=list)
    differentiators: Mapped[list] = mapped_column(JSON, default=list)
    geography: Mapped[str | None] = mapped_column(Text)
    social_links: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class CompanyEntity(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "company_entities"

    type: Mapped[str] = mapped_column(String(30), index=True)  # product|studio|service|use_case|client|industry|case_study|team
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="website")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    public_mention_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Competitor(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "competitors"

    name: Mapped[str] = mapped_column(String(300))
    website: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(20), default="suggested")  # suggested|confirmed|rejected
    overlap_score: Mapped[float] = mapped_column(Float, default=0)
    reason: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PresenceAudit(IdMixin, TenantMixin, Base):
    __tablename__ = "presence_audits"

    score: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Opportunity(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "opportunities"

    title: Mapped[str] = mapped_column(Text)
    angle: Mapped[str | None] = mapped_column(Text)
    pillar_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source: Mapped[str] = mapped_column(String(20), default="idea")  # idea|gap|gsc|competitor|trend|manual
    score: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new|used|dismissed
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


# ============================================================ content (Phase 3)
class ContentPillar(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "content_pillars"

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[int] = mapped_column(Integer, default=10)
    color: Mapped[str] = mapped_column(String(20), default="#0f8dde")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Campaign(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(200))
    brief: Mapped[str | None] = mapped_column(Text)
    starts_on: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD in org time zone
    ends_on: Mapped[str | None] = mapped_column(String(10))
    boost: Mapped[int] = mapped_column(Integer, default=50)  # % of slots in range that use the campaign
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Schedule(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "schedules"

    social_account_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("social_accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    days_of_week: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])  # Mon=0
    times: Mapped[list] = mapped_column(JSON, default=lambda: ["10:00"])
    post_types: Mapped[dict] = mapped_column(JSON, default=lambda: {"text": 1, "image": 2})
    pillar_ids: Mapped[list] = mapped_column(JSON, default=list)  # empty = all active pillars
    mode: Mapped[str] = mapped_column(String(20), default="approve_first")  # manual|approve_first|auto_with_window|full_auto
    approval_window_hours: Mapped[int] = mapped_column(Integer, default=4)
    lead_hours: Mapped[int] = mapped_column(Integer, default=24)
    catch_up_grace_hours: Mapped[int] = mapped_column(Integer, default=3)
    include_link: Mapped[bool] = mapped_column(Boolean, default=False)
    link_url: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)


class Post(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "posts"

    schedule_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("schedules.id", ondelete="SET NULL"), index=True)
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("social_accounts.id", ondelete="SET NULL"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    pillar_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    seo_article_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    topic: Mapped[str | None] = mapped_column(Text)
    post_type: Mapped[str] = mapped_column(String(20), default="text")  # text|image|carousel|gif|video|animation|link|document
    status: Mapped[str] = mapped_column(String(20), default="planned", index=True)
    mode: Mapped[str] = mapped_column(String(20), default="approve_first")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    approval_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    link_url: Mapped[str | None] = mapped_column(Text)
    first_comment: Mapped[str | None] = mapped_column(Text)
    media_ids: Mapped[list] = mapped_column(JSON, default=list)
    qa: Mapped[dict] = mapped_column(JSON, default=dict)
    similarity: Mapped[float | None] = mapped_column(Float)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    external_id: Mapped[str | None] = mapped_column(Text)
    external_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    embedding = mapped_column(Vector(), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(160))


class PostActivity(IdMixin, TenantMixin, Base):
    __tablename__ = "post_activities"

    post_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor: Mapped[str | None] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MediaAsset(IdMixin, TenantMixin, Base):
    __tablename__ = "media_assets"

    kind: Mapped[str] = mapped_column(String(20))  # image|gif|video|audio|document
    mime: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(Text)  # relative to storage/
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(60))  # template:<name> | ai:<provider> | stock:<provider> | upload
    prompt: Mapped[str | None] = mapped_column(Text)
    public_url: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PostMetric(IdMixin, TenantMixin, Base):
    __tablename__ = "post_metrics"

    post_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    reach: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    video_views: Mapped[int] = mapped_column(Integer, default=0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


# ============================================================ notifications (Phase 4)
class Notification(IdMixin, TenantMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NotificationPref(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "notification_prefs"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", "channel"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(20))  # inapp | email | telegram
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    target: Mapped[str | None] = mapped_column(String(254))  # email address or telegram chat id
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    link_code: Mapped[str | None] = mapped_column(String(20))
    quiet_start: Mapped[str | None] = mapped_column(String(5))
    quiet_end: Mapped[str | None] = mapped_column(String(5))


# ============================================================ SEO & analytics (Phase 7)
class SeoArticle(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "seo_articles"

    title: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String(200))
    keyword: Mapped[str] = mapped_column(String(200))
    secondary_keywords: Mapped[list] = mapped_column(JSON, default=list)
    audience: Mapped[str | None] = mapped_column(Text)
    outline: Mapped[list] = mapped_column(JSON, default=list)
    body_md: Mapped[str | None] = mapped_column(Text)
    meta_title: Mapped[str | None] = mapped_column(String(200))
    meta_description: Mapped[str | None] = mapped_column(String(400))
    faq: Mapped[list] = mapped_column(JSON, default=list)
    schema_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # queued|writing|draft|ready|exported|error
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    seo_score: Mapped[int] = mapped_column(Integer, default=0)
    seo_checks: Mapped[list] = mapped_column(JSON, default=list)
    cover_media_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    export_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class AnalyticsSnapshot(IdMixin, TenantMixin, Base):
    __tablename__ = "analytics_snapshots"

    source: Mapped[str] = mapped_column(String(20), index=True)  # ga4|gsc|pagespeed|platform
    key: Mapped[str] = mapped_column(String(200))
    period_start: Mapped[str | None] = mapped_column(String(10))
    period_end: Mapped[str | None] = mapped_column(String(10))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ============================================================ background jobs
class Job(IdMixin, Base):
    """Postgres-backed job queue (claimed with SKIP LOCKED by the worker).
    organization_id is NULL for platform jobs; RLS lets tenants see only their own."""

    __tablename__ = "jobs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|failed|cancelled
    priority: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    progress: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
