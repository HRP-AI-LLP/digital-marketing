"""Personal appearance and language preferences, and the shared cache of AI-translated interface text.

Revision ID: 0019_prefs
Revises: 0018_secure
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_prefs"
down_revision = "0018_secure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("preferences", postgresql.JSONB(), nullable=False, server_default="{}"))
    # Platform-level (interface text is the same for everyone), so no row-level security.
    op.create_table(
        "ui_translations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("language", sa.String(80), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("language", "source_hash", name="uq_ui_translations_lang_hash"),
    )


def downgrade() -> None:
    op.drop_table("ui_translations")
    op.drop_column("users", "preferences")
