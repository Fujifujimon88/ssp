"""add dsp_configs and dsp_win_logs tables (BKD-06)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-17 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create dsp_configs and dsp_win_logs tables."""

    # ── dsp_configs ────────────────────────────────────────────
    op.create_table(
        'dsp_configs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('endpoint_url', sa.String(length=500), nullable=False),
        sa.Column('timeout_ms', sa.Integer(), nullable=False, server_default='200'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('take_rate', sa.Float(), nullable=False, server_default='0.15'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_dsp_configs_name'), 'dsp_configs', ['name'], unique=True
    )

    # ── dsp_win_logs ────────────────────────────────────────────
    op.create_table(
        'dsp_win_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('impression_id', sa.String(length=36), nullable=False),
        sa.Column('dsp_name', sa.String(length=100), nullable=False),
        sa.Column('bid_price_usd', sa.Float(), nullable=False),
        sa.Column('clearing_price_usd', sa.Float(), nullable=False),
        sa.Column('platform_revenue_jpy', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_dsp_win_logs_impression_id'),
        'dsp_win_logs',
        ['impression_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_dsp_win_logs_dsp_name'),
        'dsp_win_logs',
        ['dsp_name'],
        unique=False,
    )
    op.create_index(
        op.f('ix_dsp_win_logs_created_at'),
        'dsp_win_logs',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Drop dsp_configs and dsp_win_logs tables."""
    op.drop_index(op.f('ix_dsp_win_logs_created_at'), table_name='dsp_win_logs')
    op.drop_index(op.f('ix_dsp_win_logs_dsp_name'), table_name='dsp_win_logs')
    op.drop_index(op.f('ix_dsp_win_logs_impression_id'), table_name='dsp_win_logs')
    op.drop_table('dsp_win_logs')
    op.drop_index(op.f('ix_dsp_configs_name'), table_name='dsp_configs')
    op.drop_table('dsp_configs')
