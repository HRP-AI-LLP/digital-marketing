"""A/B tests (experiments and their variant posts).

Revision ID: 0018_bulk
Revises: 0018_collab
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0018_bulk"
down_revision = "0018_collab"
branch_labels = None
depends_on = None


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                   name=f"fk_{table}_organization_id_organizations", ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="time"),
        sa.Column("metric", sa.String(30), nullable=False, server_default="engagement_rate"),
        sa.Column("evaluate_after_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("source_post_id", sa.Uuid()),
        sa.Column("winner_post_id", sa.Uuid()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("experiments"),
        sa.ForeignKeyConstraint(["source_post_id"], ["posts.id"], name="fk_experiments_source_post_id_posts",
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["winner_post_id"], ["posts.id"], name="fk_experiments_winner_post_id_posts",
                                ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_experiments"),
    )
    op.create_index("ix_experiments_organization_id", "experiments", ["organization_id"])
    op.create_index("ix_experiments_status", "experiments", ["status"])
    enable_tenant_rls("experiments")

    op.create_table(
        "experiment_posts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(4), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("change", sa.String(300)),
        _org_fk("experiment_posts"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], name="fk_experiment_posts_experiment_id_experiments",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], name="fk_experiment_posts_post_id_posts", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_experiment_posts"),
        sa.UniqueConstraint("experiment_id", "post_id", name="uq_experiment_posts_experiment_post"),
    )
    for col in ("organization_id", "experiment_id", "post_id"):
        op.create_index(f"ix_experiment_posts_{col}", "experiment_posts", [col])
    enable_tenant_rls("experiment_posts")


def downgrade() -> None:
    op.drop_table("experiment_posts")
    op.drop_table("experiments")
