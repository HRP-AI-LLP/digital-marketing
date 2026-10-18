"""schedules: repeat every N days (e.g. every 2 days)

Revision ID: 0016_schedule_interval
Revises: 0015_presence
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_schedule_interval"
down_revision = "0015_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("interval_days", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("schedules", sa.Column("interval_start", sa.String(10)))  # YYYY-MM-DD, local date of the first slot


def downgrade() -> None:
    op.drop_column("schedules", "interval_start")
    op.drop_column("schedules", "interval_days")
