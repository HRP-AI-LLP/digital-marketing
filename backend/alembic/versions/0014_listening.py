"""web and social listening: topics (queries), mentions and settings

Revision ID: 0014_listening
Revises: 0013_networks
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0014_listening"
down_revision = "0013_networks"
branch_labels = None
depends_on = None


def _base(table: str) -> list:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name=f"fk_{table}_organization_id_organizations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
    ]


def upgrade() -> None:
    op.create_table(
        "listening_queries", *_base("listening_queries"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="custom"),
        sa.Column("terms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("context_terms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("exclude", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("languages", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("countries", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sources", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("competitor_id", sa.Uuid()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("state", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"],
                                name="fk_listening_queries_competitor_id_competitors", ondelete="SET NULL"),
    )
    op.create_index("ix_listening_queries_organization_id", "listening_queries", ["organization_id"])
    op.create_index("ix_listening_queries_kind", "listening_queries", ["kind"])
    enable_tenant_rls("listening_queries")

    op.create_table(
        "listening_mentions", *_base("listening_mentions"),
        sa.Column("query_id", sa.Uuid()),
        sa.Column("query_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(500)),
        sa.Column("dedupe_key", sa.String(120), nullable=False),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("url", sa.Text()),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("domain", sa.String(253)),
        sa.Column("title", sa.Text()),
        sa.Column("snippet", sa.Text()),
        sa.Column("author_name", sa.String(300)),
        sa.Column("author_handle", sa.String(300)),
        sa.Column("author_url", sa.Text()),
        sa.Column("author_followers", sa.Integer()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("language", sa.String(20)),
        sa.Column("score", sa.Integer()),
        sa.Column("comments", sa.Integer()),
        sa.Column("reach", sa.Integer()),
        sa.Column("matched_terms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("relevant", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("relevance", sa.Float()),
        sa.Column("sentiment", sa.String(20)),
        sa.Column("sentiment_source", sa.String(20)),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("needs_response", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("response_reason", sa.String(30)),
        sa.Column("competitor_id", sa.Uuid()),
        sa.Column("is_competitor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enriched_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("reviewed_by", sa.Uuid()),
        sa.Column("idea_id", sa.Uuid()),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["query_id"], ["listening_queries.id"],
                                name="fk_listening_mentions_query_id_listening_queries", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["competitor_id"], ["competitors.id"],
                                name="fk_listening_mentions_competitor_id_competitors", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"],
                                name="fk_listening_mentions_reviewed_by_users", ondelete="SET NULL"),
        sa.UniqueConstraint("organization_id", "dedupe_key", name="uq_listening_mentions_org_dedupe"),
    )
    for col in ("organization_id", "query_id", "source", "content_hash", "domain", "published_at", "relevant",
                "sentiment", "needs_response", "status"):
        op.create_index(f"ix_listening_mentions_{col}", "listening_mentions", [col])
    op.create_index("ix_listening_mentions_org_published", "listening_mentions", ["organization_id", "published_at"])
    enable_tenant_rls("listening_mentions")

    op.create_table(
        "listening_settings", *_base("listening_settings"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("interval_hours", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("sources", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("digest", sa.String(10), nullable=False, server_default="weekly"),
        sa.Column("spike_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("use_ai", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mastodon_instance", sa.String(200)),
        sa.Column("state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("organization_id", name="uq_listening_settings_org"),
    )
    op.create_index("ix_listening_settings_organization_id", "listening_settings", ["organization_id"])
    enable_tenant_rls("listening_settings")


def downgrade() -> None:
    op.drop_table("listening_settings")
    op.drop_table("listening_mentions")
    op.drop_table("listening_queries")
