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

from app.models.research import IdeaBrief, ResearchRun, ResearchSettings, ResearchSource  # noqa: E402

TENANT_TABLES_RESEARCH = ["research_settings", "research_runs", "research_sources", "idea_briefs"]

from app.models.site import SitePublication, SiteRedirect  # noqa: E402

TENANT_TABLES_SITE = ["site_publications", "site_redirects"]
from app.models.presence import TENANT_TABLES_PRESENCE, PresenceRun, PresenceSuggestion  # noqa: E402
from app.models.aivis import AivisAnswer, AivisPrompt, AivisRun, AivisSettings  # noqa: E402

TENANT_TABLES_AIVIS = ["aivis_settings", "aivis_prompts", "aivis_runs", "aivis_answers"]
from app.models.listening import (  # noqa: E402
    TENANT_TABLES_LISTENING, ListeningMention, ListeningQuery, ListeningSettings,
)
from app.models.siteaudit import SiteAuditIssue, SiteAuditPage, SiteAuditRun, SiteAuditSettings  # noqa: E402

TENANT_TABLES_SITEAUDIT = ["site_audit_settings", "site_audit_runs", "site_audit_pages", "site_audit_issues"]
from app.models.keywords import (  # noqa: E402
    TENANT_TABLES_KEYWORDS, KeywordCache, KeywordList, KeywordReport, KeywordSettings, ProviderSpend, RankCheck,
    TrackedKeyword,
)
from app.models.networks import DesignConnection, LinkPage, LinkPageStat  # noqa: E402

TENANT_TABLES_NETWORKS = ["link_pages", "link_page_stats", "design_connections"]
from app.models.system import DowntimePeriod, SystemState  # noqa: E402,F401
