from app.models.base import Base
from app.models.identity import (
    AuditLog,
    Membership,
    MembershipRole,
    Organization,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
)

from app.models.marketing import (  # noqa: E402
    AnalyticsSnapshot, BrandProfile, Campaign, CompanyEntity, CompanyProfile, Competitor, ContentPillar,
    Job, KnowledgeChunk, KnowledgeDocument, KnowledgeSource, LlmUsage, MediaAsset, ModelStatus, Notification,
    NotificationPref, OAuthState, Opportunity, Post, PostActivity, PostMetric, PresenceAudit,
    ProviderConfig, Schedule, SeoArticle, SocialAccount, TaskRoute,
)

# Tables isolated by PostgreSQL row-level security on organization_id.
TENANT_TABLES = ["roles", "role_permissions", "memberships", "membership_roles", "audit_logs"]
TENANT_TABLES_V2 = [
    "provider_configs", "task_routes", "llm_usage", "social_accounts", "brand_profiles",
    "knowledge_sources", "knowledge_documents", "knowledge_chunks", "company_profiles",
    "company_entities", "competitors", "presence_audits", "opportunities", "content_pillars",
    "campaigns", "schedules", "posts", "post_activities", "media_assets", "post_metrics",
    "notifications", "notification_prefs", "seo_articles", "analytics_snapshots", "jobs",
]
TENANT_TABLES_V3 = ["model_status"]

from app.models.analytics_ext import (  # noqa: E402
    TENANT_TABLES_ANALYTICS, AccountMetric, AnalyticsReport, CompetitorMetric, Recommendation, ReportSchedule,
)
from app.models.inbox import InboxItem, InboxSettings  # noqa: E402

TENANT_TABLES_INBOX = ["inbox_items", "inbox_settings"]

__all__ = [
    "Base",
    "AuditLog",
    "Membership",
    "MembershipRole",
    "Organization",
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "User",
    "TENANT_TABLES",
]
