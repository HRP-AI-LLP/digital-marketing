"""Permission catalog. Synced into the database on startup.

Add new permissions here as modules are built; the system Administrator role of every
organization receives them automatically.
"""

PERMISSIONS: list[tuple[str, str, str]] = [
    # code, group, description
    ("org.settings.view", "Organization", "View organization settings"),
    ("org.settings.manage", "Organization", "Change organization settings"),
    ("user.view", "Users & roles", "View users"),
    ("user.manage", "Users & roles", "Add, edit and deactivate users"),
    ("role.view", "Users & roles", "View roles and permissions"),
    ("role.manage", "Users & roles", "Create and edit roles"),
    ("audit.view", "Users & roles", "View the activity log"),
    ("intel.view", "Company intelligence", "View company profile, competitors and audits"),
    ("intel.manage", "Company intelligence", "Run crawls and confirm the company profile"),
    ("content.view", "Content", "View posts and the content calendar"),
    ("content.create", "Content", "Create, edit and regenerate posts"),
    ("content.approve", "Content", "Approve or reject posts"),
    ("content.publish", "Content", "Publish posts to social accounts"),
    ("schedule.view", "Content", "View posting schedules"),
    ("schedule.manage", "Content", "Change posting schedules"),
    ("media.manage", "Content", "Generate and manage images, GIFs and videos"),
    ("integration.view", "Integrations", "View connected accounts and AI providers"),
    ("integration.manage", "Integrations", "Connect accounts and configure AI providers"),
    ("seo.view", "SEO & analytics", "View SEO articles and reports"),
    ("seo.manage", "SEO & analytics", "Generate and edit SEO articles"),
    ("analytics.view", "SEO & analytics", "View performance analytics"),
    ("analytics.manage", "SEO & analytics", "Refresh growth data, edit competitor handles, act on growth tips and manage reports"),
    ("notification.manage", "Notifications", "Configure notification channels"),
    ("inbox.view", "Inbox", "View comments, replies and mentions"),
    ("inbox.reply", "Inbox", "Reply to, assign and update comments and mentions"),
    ("inbox.manage", "Inbox", "Change listening keywords and refresh the inbox"),
    ("research.view", "Research & ideas", "View daily research, idea briefs and upcoming occasions"),
    ("research.manage", "Research & ideas", "Run research, triage idea briefs and change research settings"),
]

ALL_CODES = [p[0] for p in PERMISSIONS]

ADMIN_ROLE = "Administrator"

DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    "Content manager": (
        "Plans, creates and publishes content",
        [
            "org.settings.view", "user.view", "intel.view", "content.view", "content.create",
            "content.approve", "content.publish", "schedule.view", "schedule.manage",
            "media.manage", "integration.view", "seo.view", "seo.manage", "analytics.view", "analytics.manage",
            "inbox.view", "inbox.reply", "inbox.manage", "research.view", "research.manage",
        ],
    ),
    "Reviewer": (
        "Reviews and approves posts before they go out",
        ["content.view", "content.approve", "schedule.view", "analytics.view", "seo.view"],
    ),
    "Viewer": (
        "Read-only access",
        [c for c in ALL_CODES if c.endswith(".view")],
    ),
}
