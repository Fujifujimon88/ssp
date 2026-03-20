"""Add portal login credentials to agencies and dealers

Revision ID: f0a1b2c3d4e5
Revises: e5f6a7b8c9d0
Create Date: 2026-03-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agencies", sa.Column("login_id", sa.String(64), nullable=True))
    op.create_index("ix_agencies_login_id", "agencies", ["login_id"], unique=True)
    op.add_column("agencies", sa.Column("hashed_password", sa.String(255), nullable=True))

    op.add_column("dealers", sa.Column("login_id", sa.String(64), nullable=True))
    op.create_index("ix_dealers_login_id", "dealers", ["login_id"], unique=True)
    op.add_column("dealers", sa.Column("hashed_password", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_dealers_login_id", table_name="dealers")
    op.drop_column("dealers", "hashed_password")
    op.drop_column("dealers", "login_id")
    op.drop_index("ix_agencies_login_id", table_name="agencies")
    op.drop_column("agencies", "hashed_password")
    op.drop_column("agencies", "login_id")
