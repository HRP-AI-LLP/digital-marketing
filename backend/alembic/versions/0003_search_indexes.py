"""search indexes

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_fts ON knowledge_chunks "
        "USING gin (to_tsvector('english', text))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_org_status_sched ON posts (organization_id, status, scheduled_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_posts_org_status_sched")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_fts")
