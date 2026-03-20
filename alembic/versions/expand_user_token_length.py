"""expand user_token columns from 20 to 64 chars

Revision ID: a3b4c5d6e7f8
Revises: c0d1e2f3a4b5
Create Date: 2026-03-21 02:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, Sequence[str], None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE affiliate_conversions
        ALTER COLUMN user_token TYPE VARCHAR(64)
    """))
    conn.execute(sa.text("""
        ALTER TABLE user_points
        ALTER COLUMN user_token TYPE VARCHAR(64)
    """))
    conn.execute(sa.text("""
        ALTER TABLE android_devices
        ALTER COLUMN user_token TYPE VARCHAR(64)
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE affiliate_conversions
        ALTER COLUMN user_token TYPE VARCHAR(20)
    """))
    conn.execute(sa.text("""
        ALTER TABLE user_points
        ALTER COLUMN user_token TYPE VARCHAR(20)
    """))
    conn.execute(sa.text("""
        ALTER TABLE android_devices
        ALTER COLUMN user_token TYPE VARCHAR(20)
    """))
