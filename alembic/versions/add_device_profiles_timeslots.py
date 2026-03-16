"""add device_profiles and time_slot_multipliers tables (BKD-07, BKD-08)

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-03-17 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create device_profiles and time_slot_multipliers tables; seed default multipliers."""

    # ── device_profiles (BKD-07) ──────────────────────────────
    op.create_table(
        'device_profiles',
        sa.Column('device_id', sa.String(length=255), nullable=False),
        sa.Column('manufacturer', sa.String(length=100), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('os_version', sa.String(length=20), nullable=True),
        sa.Column('carrier', sa.String(length=100), nullable=True),
        sa.Column('mcc_mnc', sa.String(length=10), nullable=True),
        sa.Column('region', sa.String(length=20), nullable=True),
        sa.Column('screen_width', sa.Integer(), nullable=True),
        sa.Column('screen_height', sa.Integer(), nullable=True),
        sa.Column('ram_gb', sa.Integer(), nullable=True),
        sa.Column('storage_free_mb', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('device_id'),
    )

    # ── time_slot_multipliers (BKD-08) ───────────────────────
    op.create_table(
        'time_slot_multipliers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('hour_start', sa.Integer(), nullable=False),
        sa.Column('hour_end', sa.Integer(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=True),
        sa.Column('multiplier', sa.Float(), nullable=False),
        sa.Column('label', sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── デフォルト乗数シード ──────────────────────────────────
    # 朝プレミアム (07:00–08:59): 3.0x
    # 昼休み      (12:00–12:59): 1.5x
    # 夜プレミアム (21:00–22:59): 2.0x
    op.bulk_insert(
        sa.table(
            'time_slot_multipliers',
            sa.column('hour_start', sa.Integer),
            sa.column('hour_end', sa.Integer),
            sa.column('day_of_week', sa.Integer),
            sa.column('multiplier', sa.Float),
            sa.column('label', sa.String),
        ),
        [
            {'hour_start': 7,  'hour_end': 8,  'day_of_week': None, 'multiplier': 3.0, 'label': '朝プレミアム'},
            {'hour_start': 12, 'hour_end': 12, 'day_of_week': None, 'multiplier': 1.5, 'label': '昼休み'},
            {'hour_start': 21, 'hour_end': 22, 'day_of_week': None, 'multiplier': 2.0, 'label': '夜プレミアム'},
        ],
    )


def downgrade() -> None:
    """Reverse: drop time_slot_multipliers and device_profiles tables."""
    op.drop_table('time_slot_multipliers')
    op.drop_table('device_profiles')
