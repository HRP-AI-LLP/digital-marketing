"""website publishing: site publications and redirects

Revision ID: 0011_sitepublish
Revises: 0010_keywords
"""
import sqlalchemy as sa
from alembic import op

from app.db_rls import enable_tenant_rls

revision = "0011_sitepublish"
down_revision = "0010_keywords"
branch_labels = None
depends_on = None

TABLES = ["site_publications", "site_redirects"]


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=f"fk_{table}_organization_id_organizations",
                                   ondelete="CASCADE")


def _ts() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade() -> None:
    op.create_table(
        "site_publications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("target", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="none"),
        sa.Column("remote_id", sa.String(100)),
        sa.Column("remote_url", sa.Text()),
        sa.Column("published_slug", sa.String(200)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("media_remote_id", sa.String(100)),
        sa.Column("media_source_id", sa.Uuid()),
        sa.Column("job_id", sa.Uuid()),
        sa.Column("error", sa.Text()),
        sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
        *_ts(),
        _org_fk("site_publications"),
        sa.ForeignKeyConstraint(["article_id"], ["seo_articles.id"], name="fk_site_publications_article_id_seo_articles",
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_configs.id"],
                                name="fk_site_publications_provider_id_provider_configs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_site_publications"),
        sa.UniqueConstraint("article_id", "provider_id", name="uq_site_publications_article_id"),
    )
    op.create_index("ix_site_publications_organization_id", "site_publications", ["organization_id"])
    op.create_index("ix_site_publications_article_id", "site_publications", ["article_id"])
    op.create_index("ix_site_publications_provider_id", "site_publications", ["provider_id"])

    op.create_table(
        "site_redirects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid()),
        sa.Column("provider_id", sa.Uuid()),
        sa.Column("from_path", sa.Text(), nullable=False),
        sa.Column("to_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("remote_id", sa.String(100)),
        sa.Column("error", sa.Text()),
        sa.Column("pushed_at", sa.DateTime(timezone=True)),
        *_ts(),
        _org_fk("site_redirects"),
        sa.ForeignKeyConstraint(["article_id"], ["seo_articles.id"], name="fk_site_redirects_article_id_seo_articles",
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_configs.id"],
                                name="fk_site_redirects_provider_id_provider_configs", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_site_redirects"),
    )
    op.create_index("ix_site_redirects_organization_id", "site_redirects", ["organization_id"])
    op.create_index("ix_site_redirects_article_id", "site_redirects", ["article_id"])

    for table in TABLES:
        enable_tenant_rls(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
