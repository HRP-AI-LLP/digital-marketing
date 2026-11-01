"""Security and resilience: two-step sign-in (authenticator apps), session details, and database backups.

Revision ID: 0018_secure
Revises: 0018_bulk
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "0018_secure"
down_revision = "0018_bulk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_enc", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("recovery_codes", JSONB(), nullable=False, server_default="[]"))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("totp_failed_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("totp_locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("known_devices", JSONB(), nullable=False, server_default="[]"))
    op.add_column("refresh_tokens", sa.Column("signed_in_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("refresh_tokens", sa.Column("ip_address", sa.String(64), nullable=True))
    op.add_column("refresh_tokens", sa.Column("revoked_reason", sa.String(20), nullable=True))
    # Platform-level table (not tenant data, so no row-level security).
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="daily"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running", index=True),
        sa.Column("method", sa.String(20), nullable=True),
        sa.Column("file_name", sa.String(200), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("media_file", sa.String(200), nullable=True),
        sa.Column("media_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("copied_to", sa.String(500), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backup_runs")
    op.drop_column("refresh_tokens", "revoked_reason")
    op.drop_column("refresh_tokens", "ip_address")
    op.drop_column("refresh_tokens", "signed_in_at")
    for c in ("known_devices", "totp_locked_until", "totp_failed_count", "totp_last_step", "recovery_codes",
              "totp_enabled_at", "totp_enabled", "totp_secret_enc"):
        op.drop_column("users", c)
