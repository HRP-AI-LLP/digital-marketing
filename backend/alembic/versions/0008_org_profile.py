"""organization profile: legal name, description, contact details, address, registration, social links, logos

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

COLUMNS = [
    sa.Column("legal_name", sa.String(200)),
    sa.Column("tagline", sa.String(200)),
    sa.Column("description", sa.Text()),
    sa.Column("industry", sa.String(120)),
    sa.Column("company_size", sa.String(40)),
    sa.Column("founded_year", sa.Integer()),
    sa.Column("email", sa.String(254)),
    sa.Column("phone", sa.String(40)),
    sa.Column("country", sa.String(80)),
    sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
    sa.Column("address", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("registration", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("social_links", sa.JSON(), nullable=False, server_default="{}"),
    sa.Column("contacts", sa.JSON(), nullable=False, server_default="[]"),
    sa.Column("logo_asset_id", sa.Uuid()),
    sa.Column("logo_dark_asset_id", sa.Uuid()),
]


def upgrade() -> None:
    for c in COLUMNS:
        op.add_column("organizations", c)


def downgrade() -> None:
    for c in reversed(COLUMNS):
        op.drop_column("organizations", c.name)
