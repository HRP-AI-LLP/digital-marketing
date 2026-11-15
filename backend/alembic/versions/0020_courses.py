"""Academy courses: teaching series made lesson by lesson, by hand (Shorts, full lessons, carousels; English and Hindi).

Revision ID: 0020_courses
Revises: 0019_prefs
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0020_courses"
down_revision = "0019_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("catalog_id", sa.String(60)),
        sa.Column("topic", sa.Text(), nullable=False, server_default=""),
        sa.Column("audience", sa.String(300), nullable=False, server_default=""),
        sa.Column("level", sa.String(20), nullable=False, server_default="beginner"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("languages", sa.JSON(), nullable=False, server_default='["en", "hi"]'),
        sa.Column("formats", sa.JSON(), nullable=False, server_default='{"short": true, "lesson": true, "carousel": true}'),
        sa.Column("account_ids", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("outline", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("next_lesson", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_courses_organization_id_organizations",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_courses"),
    )
    op.create_index("ix_courses_organization_id", "courses", ["organization_id"])
    enable_tenant_rls("courses")


def downgrade() -> None:
    op.drop_table("courses")
