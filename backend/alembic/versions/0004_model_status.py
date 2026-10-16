"""model health: rate-limit cooldowns, key/billing problems, removed models

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_status",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(300)),
        sa.Column("last_error", sa.Text()),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_ok_at", sa.DateTime(timezone=True)),
        sa.Column("last_fail_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_model_status_organization_id_organizations",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_configs.id"], name="fk_model_status_provider_id_provider_configs",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_model_status"),
        sa.UniqueConstraint("organization_id", "provider_id", "model", name="uq_model_status_organization_id"),
    )
    op.create_index("ix_model_status_organization_id", "model_status", ["organization_id"])
    op.create_index("ix_model_status_provider_id", "model_status", ["provider_id"])
    op.create_index("ix_llm_usage_org_model", "llm_usage", ["organization_id", "provider", "model"])
    enable_tenant_rls("model_status")


def downgrade() -> None:
    op.drop_index("ix_llm_usage_org_model", table_name="llm_usage")
    op.drop_table("model_status")
