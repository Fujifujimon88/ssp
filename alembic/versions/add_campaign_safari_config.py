"""add safari_config and updated_at to campaigns

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-03-18 12:00:00.000000

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str]] = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS safari_config TEXT"))
    conn.execute(sa.text("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE campaigns DROP COLUMN IF EXISTS safari_config"))
    conn.execute(sa.text("ALTER TABLE campaigns DROP COLUMN IF EXISTS updated_at"))
