"""unified inbox (comments, replies, mentions) and social listening settings

Revision ID: 0006
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0006"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbox_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="account"),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="comment"),
        sa.Column("dedupe_key", sa.String(600), nullable=False),
        sa.Column("social_account_id", sa.Uuid()),
        sa.Column("post_id", sa.Uuid()),
        sa.Column("external_post_id", sa.String(300)),
        sa.Column("external_post_url", sa.Text()),
        sa.Column("post_snippet", sa.Text()),
        sa.Column("external_id", sa.String(500)),
        sa.Column("parent_external_id", sa.String(500)),
        sa.Column("thread_key", sa.String(500)),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("url", sa.Text()),
        sa.Column("author_name", sa.String(300)),
        sa.Column("author_handle", sa.String(300)),
        sa.Column("author_avatar_url", sa.Text()),
        sa.Column("author_url", sa.Text()),
        sa.Column("author_external_id", sa.String(300)),
        sa.Column("text", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("language", sa.String(20)),
        sa.Column("is_own", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("assigned_to", sa.Uuid()),
        sa.Column("sentiment", sa.String(20)),
        sa.Column("sentiment_source", sa.String(20)),
        sa.Column("priority", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("keyword", sa.String(200)),
        sa.Column("engagement", sa.Integer()),
        sa.Column("ai_suggestions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reply_text", sa.Text()),
        sa.Column("replied_at", sa.DateTime(timezone=True)),
        sa.Column("replied_by", sa.Uuid()),
        sa.Column("reply_external_id", sa.String(500)),
        sa.Column("reply_error", sa.Text()),
        sa.Column("liked_at", sa.DateTime(timezone=True)),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name="fk_inbox_items_organization_id_organizations", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["social_account_id"], ["social_accounts.id"],
                                name="fk_inbox_items_social_account_id_social_accounts", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], name="fk_inbox_items_post_id_posts", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_id"], ["inbox_items.id"], name="fk_inbox_items_parent_id_inbox_items",
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], name="fk_inbox_items_assigned_to_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["replied_by"], ["users.id"], name="fk_inbox_items_replied_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_inbox_items"),
        sa.UniqueConstraint("organization_id", "dedupe_key", name="uq_inbox_items_org_dedupe"),
    )
    for col in ("organization_id", "source", "platform", "kind", "social_account_id", "post_id", "thread_key",
                "published_at", "status", "assigned_to"):
        op.create_index(f"ix_inbox_items_{col}", "inbox_items", [col])
    op.create_index("ix_inbox_items_org_status_created", "inbox_items", ["organization_id", "status", "created_at"])
    enable_tenant_rls("inbox_items")

    op.create_table(
        "inbox_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("excluded", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("languages", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sources", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("listening_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name="fk_inbox_settings_organization_id_organizations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_inbox_settings"),
        sa.UniqueConstraint("organization_id", name="uq_inbox_settings_org"),
    )
    op.create_index("ix_inbox_settings_organization_id", "inbox_settings", ["organization_id"])
    enable_tenant_rls("inbox_settings")


def downgrade() -> None:
    op.drop_table("inbox_settings")
    op.drop_table("inbox_items")
