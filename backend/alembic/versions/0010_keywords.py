"""keywords: research reports, lists, gap, rank tracking, provider cache and spend

Revision ID: 0010_keywords
Revises: 0009_aivis
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0010_keywords"
down_revision = "0009_aivis"
branch_labels = None
depends_on = None

TABLES = ["keyword_settings", "keyword_cache", "provider_spend", "keyword_reports", "keyword_lists",
          "tracked_keywords", "rank_checks"]


def _base(table: str) -> list:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id_organizations",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
    ]


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def _org_index(table: str) -> None:
    op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])


def upgrade() -> None:
    op.create_table(
        "keyword_settings", *_base("keyword_settings"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        *_ts(),
        sa.UniqueConstraint("organization_id", name="uq_keyword_settings_organization_id"),
    )
    _org_index("keyword_settings")

    op.create_table(
        "keyword_cache", *_base("keyword_cache"),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("response", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "cache_key", name="uq_keyword_cache_organization_id"),
    )
    _org_index("keyword_cache")
    op.create_index("ix_keyword_cache_expires_at", "keyword_cache", ["expires_at"])

    op.create_table(
        "provider_spend", *_base("provider_spend"),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False, server_default="research"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _org_index("provider_spend")
    op.create_index("ix_provider_spend_provider", "provider_spend", ["provider"])
    op.create_index("ix_provider_spend_day", "provider_spend", ["day"])
    op.create_index("ix_provider_spend_created_at", "provider_spend", ["created_at"])

    op.create_table(
        "keyword_reports", *_base("keyword_reports"),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("rows", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("meta", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid()),
        *_ts(),
    )
    _org_index("keyword_reports")
    op.create_index("ix_keyword_reports_kind", "keyword_reports", ["kind"])

    op.create_table(
        "keyword_lists", *_base("keyword_lists"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("clusters", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("cluster_meta", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Uuid()),
        *_ts(),
    )
    _org_index("keyword_lists")

    op.create_table(
        "tracked_keywords", *_base("tracked_keywords"),
        sa.Column("keyword", sa.String(200), nullable=False),
        sa.Column("target_url", sa.Text()),
        sa.Column("location_code", sa.Integer(), nullable=False, server_default="2356"),
        sa.Column("language_code", sa.String(10), nullable=False, server_default="en"),
        sa.Column("device", sa.String(10), nullable=False, server_default="desktop"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("volume", sa.Integer()),
        sa.Column("position", sa.Integer()),
        sa.Column("previous_position", sa.Integer()),
        sa.Column("best_position", sa.Integer()),
        sa.Column("url", sa.Text()),
        sa.Column("gsc_position", sa.Float()),
        sa.Column("source", sa.String(20)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        *_ts(),
        sa.UniqueConstraint("organization_id", "keyword", "location_code", "language_code", "device",
                            name="uq_tracked_keywords_organization_id"),
    )
    _org_index("tracked_keywords")

    op.create_table(
        "rank_checks", *_base("rank_checks"),
        sa.Column("tracked_keyword_id", sa.Uuid(), nullable=False),
        sa.Column("check_date", sa.String(10), nullable=False),
        sa.Column("position", sa.Integer()),
        sa.Column("url", sa.Text()),
        sa.Column("gsc_position", sa.Float()),
        sa.Column("source", sa.String(20), nullable=False, server_default="dataforseo"),
        sa.Column("serp_features", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("top_urls", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tracked_keyword_id"], ["tracked_keywords.id"],
                                name="fk_rank_checks_tracked_keyword_id_tracked_keywords", ondelete="CASCADE"),
        sa.UniqueConstraint("tracked_keyword_id", "check_date", name="uq_rank_checks_tracked_keyword_id"),
    )
    _org_index("rank_checks")
    op.create_index("ix_rank_checks_tracked_keyword_id", "rank_checks", ["tracked_keyword_id"])
    op.create_index("ix_rank_checks_check_date", "rank_checks", ["check_date"])

    for table in TABLES:
        enable_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
