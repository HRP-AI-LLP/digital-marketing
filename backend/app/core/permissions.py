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
    ("seo.publish", "SEO & analytics", "Publish SEO articles to the company website"),
    ("analytics.view", "SEO & analytics", "View performance analytics"),
    ("analytics.manage", "SEO & analytics", "Refresh growth data, edit competitor handles, act on growth tips and manage reports"),
    ("notification.manage", "Notifications", "Configure notification channels"),
    ("inbox.view", "Inbox", "View comments, replies and mentions"),
    ("inbox.reply", "Inbox", "Reply to, assign and update comments and mentions"),
    ("inbox.manage", "Inbox", "Change listening keywords and refresh the inbox"),
    ("research.view", "Research & ideas", "View daily research, idea briefs and upcoming occasions"),
    ("research.manage", "Research & ideas", "Run research, triage idea briefs and change research settings"),
    ("presence.view", "Social presence", "View the social presence score, profile checks and suggestions"),
    ("presence.manage", "Social presence", "Run the social presence check and mark suggestions as done"),
    ("aivis.view", "AI visibility", "View how often AI assistants mention the company, answers, competitors and llms.txt"),
    ("aivis.manage", "AI visibility", "Edit tracked prompts, run visibility checks, change engines and edit llms.txt"),
    ("listening.view", "Listening", "View web and social listening mentions and insights"),
    ("listening.manage", "Listening", "Change listening topics and settings, run listening and triage mentions"),
    ("siteaudit.view", "SEO & analytics", "View site audits, issues and page details"),
    ("siteaudit.manage", "SEO & analytics", "Run site audits and change audit settings"),
    ("keywords.view", "Keywords & rankings", "View keyword research, keyword gaps, lists and rankings"),
    ("keywords.manage", "Keywords & rankings", "Run keyword research and gap reports, edit lists, track keywords and change settings"),
    ("linkinbio.manage", "Content", "Edit and publish the link-in-bio page and see its click counts"),
]

ALL_CODES = [p[0] for p in PERMISSIONS]

ADMIN_ROLE = "Administrator"

DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    "Content manager": (
        "Plans, creates and publishes content",
        [
            "org.settings.view", "user.view", "intel.view", "content.view", "content.create",
            "content.approve", "content.publish", "schedule.view", "schedule.manage",
            "media.manage", "integration.view", "seo.view", "seo.manage", "seo.publish", "analytics.view", "analytics.manage",
            "inbox.view", "inbox.reply", "inbox.manage", "research.view", "research.manage",
            "presence.view", "presence.manage", "aivis.view", "aivis.manage", "listening.view", "listening.manage",
            "siteaudit.view", "siteaudit.manage", "keywords.view", "keywords.manage", "linkinbio.manage",
        ],
    ),
    "Reviewer": (
        "Reviews and approves posts before they go out",
        ["content.view", "content.approve", "schedule.view", "analytics.view", "seo.view", "presence.view",
         "siteaudit.view", "keywords.view", "aivis.view", "listening.view"],
    ),
    "Viewer": (
        "Read-only access",
        [c for c in ALL_CODES if c.endswith(".view")],
    ),
}
