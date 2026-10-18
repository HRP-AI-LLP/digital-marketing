"""site audit: settings, runs, pages and issues

Revision ID: 0012_siteaudit
Revises: 0011_sitepublish
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0012_siteaudit"
down_revision = "0011_sitepublish"
branch_labels = None
depends_on = None

TABLES = ["site_audit_settings", "site_audit_runs", "site_audit_pages", "site_audit_issues"]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id_organizations",
                                   ondelete="CASCADE")


def _run_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["run_id"], ["site_audit_runs.id"], name=f"fk_{table}_run_id_site_audit_runs",
                                   ondelete="CASCADE")


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def _counts() -> list[sa.Column]:
    return [sa.Column(c, sa.Integer(), nullable=False, server_default="0") for c in ("errors", "warnings", "notices")]


def upgrade() -> None:
    op.create_table(
        "site_audit_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        *_ts(),
        _org_fk("site_audit_settings"),
        sa.PrimaryKeyConstraint("id", name="pk_site_audit_settings"),
        sa.UniqueConstraint("organization_id", name="uq_site_audit_settings_organization_id"),
    )
    op.create_index("ix_site_audit_settings_organization_id", "site_audit_settings", ["organization_id"])

    op.create_table(
        "site_audit_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_key", sa.String(40)),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("start_url", sa.Text()),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("score", sa.Integer()),
        sa.Column("pages_crawled", sa.Integer(), nullable=False, server_default="0"),
        *_counts(),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("site", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("performance", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        *_ts(),
        _org_fk("site_audit_runs"),
        sa.PrimaryKeyConstraint("id", name="pk_site_audit_runs"),
        sa.UniqueConstraint("organization_id", "run_key", name="uq_site_audit_runs_organization_id"),
    )
    op.create_index("ix_site_audit_runs_organization_id", "site_audit_runs", ["organization_id"])
    op.create_index("ix_site_audit_runs_status", "site_audit_runs", ["status"])

    op.create_table(
        "site_audit_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text()),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_type", sa.String(120)),
        sa.Column("depth", sa.Integer()),
        sa.Column("response_ms", sa.Integer()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("word_count", sa.Integer()),
        sa.Column("title", sa.Text()),
        sa.Column("indexable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("in_sitemap", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("inlinks", sa.Integer(), nullable=False, server_default="0"),
        *_counts(),
        sa.Column("text_ratio", sa.Float()),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("site_audit_pages"),
        _run_fk("site_audit_pages"),
        sa.PrimaryKeyConstraint("id", name="pk_site_audit_pages"),
    )
    op.create_index("ix_site_audit_pages_organization_id", "site_audit_pages", ["organization_id"])
    op.create_index("ix_site_audit_pages_run_id", "site_audit_pages", ["run_id"])

    op.create_table(
        "site_audit_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("check_id", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_new", sa.Boolean(), nullable=False, server_default=sa.false()),
        _org_fk("site_audit_issues"),
        _run_fk("site_audit_issues"),
        sa.PrimaryKeyConstraint("id", name="pk_site_audit_issues"),
    )
    op.create_index("ix_site_audit_issues_organization_id", "site_audit_issues", ["organization_id"])
    op.create_index("ix_site_audit_issues_run_id", "site_audit_issues", ["run_id"])
    op.create_index("ix_site_audit_issues_check_id", "site_audit_issues", ["check_id"])

    for table in TABLES:
        enable_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
