"""analytics: account growth snapshots, competitor benchmarks, recommendations, report schedules and reports

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = ["account_metrics", "competitor_metrics", "analytics_recommendations", "report_schedules", "analytics_reports"]


def _org(table: str) -> list:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id_organizations",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
    ]


def upgrade() -> None:
    op.create_table(
        "account_metrics", *_org("account_metrics"),
        sa.Column("social_account_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        *[sa.Column(c, sa.BigInteger()) for c in
          ("followers", "following", "posts_count", "reach", "impressions", "profile_views", "engagement", "views")],
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["social_account_id"], ["social_accounts.id"],
                                name="fk_account_metrics_social_account_id_social_accounts", ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "social_account_id", "day", name="uq_account_metrics_account_day"),
    )
    op.create_index("ix_account_metrics_organization_id", "account_metrics", ["organization_id"])
    op.create_index("ix_account_metrics_social_account_id", "account_metrics", ["social_account_id"])
    op.create_index("ix_account_metrics_day", "account_metrics", ["day"])

    op.create_table(
        "competitor_metrics", *_org("competitor_metrics"),
        sa.Column("competitor_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("handle", sa.String(300)),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("followers", sa.BigInteger()),
        sa.Column("posts_count", sa.BigInteger()),
        sa.Column("posts_30d", sa.Integer()),
        sa.Column("avg_engagement", sa.Float()),
        sa.Column("views", sa.BigInteger()),
        sa.Column("score", sa.Integer()),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"], name="fk_competitor_metrics_competitor_id_competitors",
                                ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "competitor_id", "channel", "day", name="uq_competitor_metrics_channel_day"),
    )
    op.create_index("ix_competitor_metrics_organization_id", "competitor_metrics", ["organization_id"])
    op.create_index("ix_competitor_metrics_competitor_id", "competitor_metrics", ["competitor_id"])
    op.create_index("ix_competitor_metrics_day", "competitor_metrics", ["day"])

    op.create_table(
        "analytics_recommendations", *_org("analytics_recommendations"),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("why", sa.Text()),
        sa.Column("action_text", sa.Text()),
        sa.Column("impact", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("effort", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("category", sa.String(30), nullable=False, server_default="content"),
        sa.Column("action", sa.JSON()),
        sa.Column("source", sa.String(10), nullable=False, server_default="ai"),
        sa.Column("status", sa.String(12), nullable=False, server_default="new"),
        sa.Column("status_changed_at", sa.DateTime(timezone=True)),
        sa.Column("status_changed_by", sa.Uuid()),
        sa.Column("result", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_analytics_recommendations_organization_id", "analytics_recommendations", ["organization_id"])
    op.create_index("ix_analytics_recommendations_batch_id", "analytics_recommendations", ["batch_id"])
    op.create_index("ix_analytics_recommendations_status", "analytics_recommendations", ["status"])

    op.create_table(
        "report_schedules", *_org("report_schedules"),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("frequency", sa.String(10), nullable=False, server_default="weekly"),
        sa.Column("weekday", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("month_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("time", sa.String(5), nullable=False, server_default="09:00"),
        sa.Column("recipients", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.Text()),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_report_schedules_organization_id", "report_schedules", ["organization_id"])
    op.create_index("ix_report_schedules_next_run_at", "report_schedules", ["next_run_at"])

    op.create_table(
        "analytics_reports", *_org("analytics_reports"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("trigger", sa.String(12), nullable=False, server_default="manual"),
        sa.Column("schedule_id", sa.Uuid()),
        sa.Column("path", sa.Text()),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recipients", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("email_error", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["schedule_id"], ["report_schedules.id"], name="fk_analytics_reports_schedule_id_report_schedules",
                                ondelete="SET NULL"),
    )
    op.create_index("ix_analytics_reports_organization_id", "analytics_reports", ["organization_id"])
    op.create_index("ix_analytics_reports_created_at", "analytics_reports", ["created_at"])

    for t in TABLES:
        enable_tenant_rls(t)


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_table(t)
