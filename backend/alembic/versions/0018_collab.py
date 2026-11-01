"""team collaboration: saved inbox replies, internal post comments with @mentions, post version history

Revision ID: 0018_collab
Revises: 0018_sched
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0018_collab"
down_revision = "0018_sched"
branch_labels = None
depends_on = None


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                   name=f"fk_{table}_organization_id_organizations", ondelete="CASCADE")


def _user_fk(table: str, col: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([col], ["users.id"], name=f"fk_{table}_{col}_users", ondelete="SET NULL")


def _post_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["post_id"], ["posts.id"], name=f"fk_{table}_post_id_posts", ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "saved_replies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("platforms", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("saved_replies"),
        _user_fk("saved_replies", "created_by"),
        sa.PrimaryKeyConstraint("id", name="pk_saved_replies"),
    )
    op.create_index("ix_saved_replies_organization_id", "saved_replies", ["organization_id"])
    enable_tenant_rls("saved_replies")

    op.create_table(
        "post_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mentions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_by", sa.Uuid()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("post_comments"),
        _post_fk("post_comments"),
        _user_fk("post_comments", "user_id"),
        _user_fk("post_comments", "resolved_by"),
        sa.PrimaryKeyConstraint("id", name="pk_post_comments"),
    )
    for col in ("organization_id", "post_id"):
        op.create_index(f"ix_post_comments_{col}", "post_comments", [col])
    enable_tenant_rls("post_comments")

    op.create_table(
        "post_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("post_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("changed_by", sa.Uuid()),
        sa.Column("reason", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("post_versions"),
        _post_fk("post_versions"),
        _user_fk("post_versions", "changed_by"),
        sa.PrimaryKeyConstraint("id", name="pk_post_versions"),
        sa.UniqueConstraint("post_id", "version", name="uq_post_versions_post_version"),
    )
    for col in ("organization_id", "post_id"):
        op.create_index(f"ix_post_versions_{col}", "post_versions", [col])
    enable_tenant_rls("post_versions")


def downgrade() -> None:
    op.drop_table("post_versions")
    op.drop_table("post_comments")
    op.drop_table("saved_replies")
