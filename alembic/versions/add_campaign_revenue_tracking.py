"""add dealer_revenue_rate, user_point_rate, tracking_url, partner_id filters

Revision ID: f0a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-03-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE affiliate_campaigns
        ADD COLUMN IF NOT EXISTS dealer_revenue_rate FLOAT NOT NULL DEFAULT 0.0
    """))
    conn.execute(sa.text("""
        ALTER TABLE affiliate_campaigns
        ADD COLUMN IF NOT EXISTS user_point_rate FLOAT NOT NULL DEFAULT 0.0
    """))
    conn.execute(sa.text("""
        ALTER TABLE affiliate_campaigns
        ADD COLUMN IF NOT EXISTS tracking_url TEXT
    """))
    conn.execute(sa.text("""
        ALTER TABLE affiliate_campaigns
        ADD COLUMN IF NOT EXISTS blacklist_partner_ids TEXT
    """))
    conn.execute(sa.text("""
        ALTER TABLE affiliate_campaigns
        ADD COLUMN IF NOT EXISTS whitelist_partner_ids TEXT
    """))


def downgrade() -> None:
    op.drop_column("affiliate_campaigns", "whitelist_partner_ids")
    op.drop_column("affiliate_campaigns", "blacklist_partner_ids")
    op.drop_column("affiliate_campaigns", "tracking_url")
    op.drop_column("affiliate_campaigns", "user_point_rate")
    op.drop_column("affiliate_campaigns", "dealer_revenue_rate")
