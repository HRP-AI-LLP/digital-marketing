"""Connections: outgoing webhooks, their delivery log and personal API tokens.

Revision ID: 0018_hooks
Revises: 0018_links
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0018_hooks"
down_revision = "0018_links"
branch_labels = None
depends_on = None


def _org(table: str) -> list:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name=f"fk_{table}_organization_id_organizations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
    ]


def _ts() -> list:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "webhooks", *_org("webhooks"), *_ts(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("secret_enc", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_status", sa.String(20)),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True)),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disabled_reason", sa.Text()),
        sa.Column("created_by", sa.Uuid()),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_webhooks_created_by_users", ondelete="SET NULL"),
    )
    op.create_index("ix_webhooks_organization_id", "webhooks", ["organization_id"])
    enable_tenant_rls("webhooks")

    op.create_table(
        "webhook_deliveries", *_org("webhook_deliveries"),
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status_code", sa.Integer()),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("response_body", sa.Text()),
        sa.Column("redelivery", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], name="fk_webhook_deliveries_webhook_id_webhooks",
                                ondelete="CASCADE"),
    )
    for col in ("organization_id", "webhook_id", "event", "created_at"):
        op.create_index(f"ix_webhook_deliveries_{col}", "webhook_deliveries", [col])
    enable_tenant_rls("webhook_deliveries")

    op.create_table(
        "api_tokens", *_org("api_tokens"), *_ts(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_ip", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.Uuid()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_api_tokens_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], name="fk_api_tokens_revoked_by_users", ondelete="SET NULL"),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_token_hash"),
        sa.UniqueConstraint("prefix", name="uq_api_tokens_prefix"),
    )
    op.create_index("ix_api_tokens_organization_id", "api_tokens", ["organization_id"])
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])
    enable_tenant_rls("api_tokens")


def downgrade() -> None:
    op.drop_table("api_tokens")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
