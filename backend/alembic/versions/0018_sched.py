"""Smart scheduling: best-time schedules (time_mode, posts_per_day) and evergreen posts.

Revision ID: 0018_sched
Revises: 0018_hooks
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_sched"
down_revision = "0018_hooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schedules", sa.Column("time_mode", sa.String(10), nullable=False, server_default="fixed"))
    op.add_column("schedules", sa.Column("posts_per_day", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("posts", sa.Column("evergreen", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_posts_evergreen", "posts", ["organization_id"], postgresql_where=sa.text("evergreen"))


def downgrade() -> None:
    op.drop_index("ix_posts_evergreen", table_name="posts")
    op.drop_column("posts", "evergreen")
    op.drop_column("schedules", "posts_per_day")
    op.drop_column("schedules", "time_mode")
