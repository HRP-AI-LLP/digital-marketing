"""networks: link-in-bio pages with anonymous click counts, and design-tool (Canva) connections

Revision ID: 0013_networks
Revises: 0012_siteaudit
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0013_networks"
down_revision = "0012_siteaudit"
branch_labels = None
depends_on = None


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                   name=f"fk_{table}_organization_id_organizations", ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "link_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("title", sa.String(120)),
        sa.Column("tagline", sa.String(300)),
        sa.Column("show_logo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_socials", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_posts", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("posts_count", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("links", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("theme", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("link_pages"),
        sa.PrimaryKeyConstraint("id", name="pk_link_pages"),
        sa.UniqueConstraint("organization_id", name="uq_link_pages_organization_id"),
        sa.UniqueConstraint("slug", name="uq_link_pages_slug"),
    )
    op.create_index("ix_link_pages_organization_id", "link_pages", ["organization_id"])
    enable_tenant_rls("link_pages")

    op.create_table(
        "link_page_stats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("target", sa.String(80), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        _org_fk("link_page_stats"),
        sa.ForeignKeyConstraint(["page_id"], ["link_pages.id"], name="fk_link_page_stats_page_id_link_pages",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_link_page_stats"),
        sa.UniqueConstraint("page_id", "day", "target", name="uq_link_page_stats_page_day_target"),
    )
    for col in ("organization_id", "page_id", "day"):
        op.create_index(f"ix_link_page_stats_{col}", "link_page_stats", [col])
    enable_tenant_rls("link_page_stats")

    op.create_table(
        "design_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(200)),
        sa.Column("display_name", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False, server_default="connected"),
        sa.Column("status_message", sa.Text()),
        sa.Column("tokens_enc", sa.Text()),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("connected_by", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("design_connections"),
        sa.PrimaryKeyConstraint("id", name="pk_design_connections"),
        sa.UniqueConstraint("organization_id", "provider", name="uq_design_connections_org_provider"),
    )
    op.create_index("ix_design_connections_organization_id", "design_connections", ["organization_id"])
    enable_tenant_rls("design_connections")


def downgrade() -> None:
    op.drop_table("design_connections")
    op.drop_table("link_page_stats")
    op.drop_table("link_pages")
