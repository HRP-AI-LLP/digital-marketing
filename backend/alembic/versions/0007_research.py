"""deep research: settings, runs, sources and idea briefs

Revision ID: 0007
Revises: 0004
"""
import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0007"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = ["research_settings", "research_runs", "research_sources", "idea_briefs"]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id_organizations",
                                   ondelete="CASCADE")


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade() -> None:
    op.create_table(
        "research_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("run_time", sa.String(5), nullable=False, server_default="07:00"),
        sa.Column("depth", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("ideas_per_day", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        *_ts(),
        _org_fk("research_settings"),
        sa.PrimaryKeyConstraint("id", name="pk_research_settings"),
        sa.UniqueConstraint("organization_id", name="uq_research_settings_organization_id"),
    )
    op.create_index("ix_research_settings_organization_id", "research_settings", ["organization_id"])

    op.create_table(
        "research_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_key", sa.String(40)),
        sa.Column("run_date", sa.String(10), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("depth", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("stats", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        *_ts(),
        _org_fk("research_runs"),
        sa.PrimaryKeyConstraint("id", name="pk_research_runs"),
        sa.UniqueConstraint("organization_id", "run_key", name="uq_research_runs_organization_id"),
    )
    op.create_index("ix_research_runs_organization_id", "research_runs", ["organization_id"])
    op.create_index("ix_research_runs_run_date", "research_runs", ["run_date"])
    op.create_index("ix_research_runs_status", "research_runs", ["status"])

    op.create_table(
        "research_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_key", sa.String(300), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("publisher", sa.String(200)),
        sa.Column("published_at", sa.String(10)),
        sa.Column("snippet", sa.Text()),
        sa.Column("theme", sa.String(60)),
        sa.Column("query", sa.Text()),
        sa.Column("fetched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("excerpt", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("research_sources"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], name="fk_research_sources_run_id_research_runs",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_research_sources"),
    )
    op.create_index("ix_research_sources_organization_id", "research_sources", ["organization_id"])
    op.create_index("ix_research_sources_run_id", "research_sources", ["run_id"])
    op.create_index("ix_research_sources_url_key", "research_sources", ["url_key"])
    op.create_index("ix_research_sources_created_at", "research_sources", ["created_at"])

    op.create_table(
        "idea_briefs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("run_date", sa.String(10), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("angle", sa.Text()),
        sa.Column("why_now", sa.Text()),
        sa.Column("theme", sa.String(60), nullable=False, server_default="company"),
        sa.Column("audience", sa.Text()),
        sa.Column("facts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("formats", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("article", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cta", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("hashtags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("scores", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("occasion", sa.JSON()),
        sa.Column("pillar_id", sa.Uuid()),
        sa.Column("opportunity_id", sa.Uuid()),
        sa.Column("used", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(), nullable=True),
        sa.Column("embedding_model", sa.String(160)),
        *_ts(),
        _org_fk("idea_briefs"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], name="fk_idea_briefs_run_id_research_runs",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_idea_briefs"),
    )
    op.create_index("ix_idea_briefs_organization_id", "idea_briefs", ["organization_id"])
    op.create_index("ix_idea_briefs_run_id", "idea_briefs", ["run_id"])
    op.create_index("ix_idea_briefs_run_date", "idea_briefs", ["run_date"])
    op.create_index("ix_idea_briefs_score", "idea_briefs", ["score"])
    op.create_index("ix_idea_briefs_status", "idea_briefs", ["status"])

    for table in TABLES:
        enable_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
