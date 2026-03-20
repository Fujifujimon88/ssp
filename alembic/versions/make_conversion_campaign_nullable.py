"""make affiliate_conversions.campaign_id nullable for direct ASP postback

Revision ID: c0d1e2f3a4b5
Revises: a2b3c4d5e6f7
Create Date: 2026-03-21 01:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Drop FK constraint first (PostgreSQL requires this to change nullability)
    # Then make the column nullable and re-add FK
    conn.execute(sa.text("""
        ALTER TABLE affiliate_conversions
        ALTER COLUMN campaign_id DROP NOT NULL
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE affiliate_conversions
        ALTER COLUMN campaign_id SET NOT NULL
    """))
