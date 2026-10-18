"""Catching up after downtime: worker heartbeat, recorded downtime (laptop off, asleep or offline),
and per-organization catch-up settings.

Revision ID: 0017_downtime
Revises: 0016_schedule_interval
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_downtime"
down_revision = "0016_schedule_interval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Platform-level tables (not tenant data, so no row-level security).
    op.create_table(
        "system_state",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "downtime_periods",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("reason", sa.String(20), nullable=False),  # stopped | asleep | offline
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("organizations", sa.Column("publishing", sa.JSON(), nullable=False, server_default="{}"))
    # Posts are never "missed" any more: anything left in that state waits for approval instead.
    op.execute("UPDATE posts SET status = 'in_review', error = NULL WHERE status = 'missed'")


def downgrade() -> None:
    op.drop_column("organizations", "publishing")
    op.drop_table("downtime_periods")
    op.drop_table("system_state")
