"""AI search visibility: settings, tracked prompts, runs and analysed answers

Revision ID: 0009_aivis
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0009_aivis"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLES = ["aivis_settings", "aivis_prompts", "aivis_runs", "aivis_answers"]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id_organizations",
                                   ondelete="CASCADE")


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade() -> None:
    op.create_table(
        "aivis_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        *_ts(),
        _org_fk("aivis_settings"),
        sa.PrimaryKeyConstraint("id", name="pk_aivis_settings"),
        sa.UniqueConstraint("organization_id", name="uq_aivis_settings_organization_id"),
    )
    op.create_index("ix_aivis_settings_organization_id", "aivis_settings", ["organization_id"])

    op.create_table(
        "aivis_prompts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(80), nullable=False, server_default="General"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        *_ts(),
        _org_fk("aivis_prompts"),
        sa.PrimaryKeyConstraint("id", name="pk_aivis_prompts"),
    )
    op.create_index("ix_aivis_prompts_organization_id", "aivis_prompts", ["organization_id"])

    op.create_table(
        "aivis_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_key", sa.String(40)),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("engines", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("recommendations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error", sa.Text()),
        *_ts(),
        _org_fk("aivis_runs"),
        sa.PrimaryKeyConstraint("id", name="pk_aivis_runs"),
        sa.UniqueConstraint("organization_id", "run_key", name="uq_aivis_runs_organization_id"),
    )
    op.create_index("ix_aivis_runs_organization_id", "aivis_runs", ["organization_id"])
    op.create_index("ix_aivis_runs_status", "aivis_runs", ["status"])

    op.create_table(
        "aivis_answers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_id", sa.Uuid()),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(80), nullable=False, server_default="General"),
        sa.Column("engine_key", sa.String(300), nullable=False),
        sa.Column("engine_label", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("web", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("citations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("mentioned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer()),
        sa.Column("sentiment", sa.String(10)),
        sa.Column("competitors", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("brands", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("own_cited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("analysis", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("aivis_answers"),
        sa.ForeignKeyConstraint(["run_id"], ["aivis_runs.id"], name="fk_aivis_answers_run_id_aivis_runs", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prompt_id"], ["aivis_prompts.id"], name="fk_aivis_answers_prompt_id_aivis_prompts",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_aivis_answers"),
        sa.UniqueConstraint("run_id", "prompt_id", "engine_key", name="uq_aivis_answers_run_id"),
    )
    op.create_index("ix_aivis_answers_organization_id", "aivis_answers", ["organization_id"])
    op.create_index("ix_aivis_answers_run_id", "aivis_answers", ["run_id"])
    op.create_index("ix_aivis_answers_prompt_id", "aivis_answers", ["prompt_id"])
    op.create_index("ix_aivis_answers_mentioned", "aivis_answers", ["mentioned"])
    op.create_index("ix_aivis_answers_created_at", "aivis_answers", ["created_at"])

    for table in TABLES:
        enable_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
