"""social presence audit: runs and suggestions

Revision ID: 0015_presence
Revises: 0014_listening
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0015_presence"
down_revision = "0014_listening"
branch_labels = None
depends_on = None


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id_organizations",
                                   ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "presence_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(12), nullable=False, server_default="manual"),
        sa.Column("score", sa.Integer()),
        sa.Column("grade", sa.String(40)),
        sa.Column("parts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("profiles", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("coverage", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("narrative", sa.Text()),
        sa.Column("narrative_source", sa.String(10)),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("presence_runs"),
        sa.PrimaryKeyConstraint("id", name="pk_presence_runs"),
    )
    op.create_index("ix_presence_runs_organization_id", "presence_runs", ["organization_id"])
    op.create_index("ix_presence_runs_created_at", "presence_runs", ["created_at"])
    enable_tenant_rls("presence_runs")

    op.create_table(
        "presence_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(300), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("profile_key", sa.String(300)),
        sa.Column("platform", sa.String(30)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("priority", sa.String(10), nullable=False, server_default="medium"),
        sa.Column("category", sa.String(30), nullable=False, server_default="completeness"),
        sa.Column("points", sa.Float(), nullable=False, server_default="0"),
        sa.Column("action", sa.JSON()),
        sa.Column("source", sa.String(10), nullable=False, server_default="rules"),
        sa.Column("status", sa.String(12), nullable=False, server_default="open"),
        sa.Column("reopened", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status_changed_at", sa.DateTime(timezone=True)),
        sa.Column("status_changed_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("presence_suggestions"),
        sa.ForeignKeyConstraint(["run_id"], ["presence_runs.id"], name="fk_presence_suggestions_run_id_presence_runs",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_presence_suggestions"),
    )
    for col in ("organization_id", "run_id", "status"):
        op.create_index(f"ix_presence_suggestions_{col}", "presence_suggestions", [col])
    enable_tenant_rls("presence_suggestions")


def downgrade() -> None:
    op.drop_table("presence_suggestions")
    op.drop_table("presence_runs")
