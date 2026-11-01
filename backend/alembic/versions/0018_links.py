"""links: tracked short links (/r/<code>) with anonymous daily click counts

Revision ID: 0018_links
Revises: 0017_downtime
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0018_links"
down_revision = "0017_downtime"
branch_labels = None
depends_on = None


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                   name=f"fk_{table}_organization_id_organizations", ondelete="CASCADE")


def upgrade() -> None:
    op.create_table(
        "tracked_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("target_url", sa.Text(), nullable=False),
        sa.Column("utm", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("post_id", sa.Uuid()),
        sa.Column("campaign_id", sa.Uuid()),
        sa.Column("platform", sa.String(30)),
        sa.Column("label", sa.String(200)),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("clicks_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_click_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _org_fk("tracked_links"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], name="fk_tracked_links_post_id_posts", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_tracked_links"),
        sa.UniqueConstraint("code", name="uq_tracked_links_code"),
    )
    for col in ("organization_id", "post_id", "campaign_id"):
        op.create_index(f"ix_tracked_links_{col}", "tracked_links", [col])
    enable_tenant_rls("tracked_links")

    op.create_table(
        "link_clicks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("referrer", sa.String(80), nullable=False, server_default="direct"),
        sa.Column("app", sa.String(20), nullable=False, server_default="browser"),
        sa.Column("device", sa.String(10), nullable=False, server_default="desktop"),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        _org_fk("link_clicks"),
        sa.ForeignKeyConstraint(["link_id"], ["tracked_links.id"], name="fk_link_clicks_link_id_tracked_links",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_link_clicks"),
        sa.UniqueConstraint("link_id", "day", "referrer", "app", "device", name="uq_link_clicks_bucket"),
    )
    for col in ("organization_id", "link_id", "day"):
        op.create_index(f"ix_link_clicks_{col}", "link_clicks", [col])
    enable_tenant_rls("link_clicks")


def downgrade() -> None:
    op.drop_table("link_clicks")
    op.drop_table("tracked_links")
