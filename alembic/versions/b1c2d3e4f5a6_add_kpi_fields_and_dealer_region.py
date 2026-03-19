"""add KPI fields to mdm_impressions and region to dealers

Revision ID: b1c2d3e4f5a6
Revises: f495c04833bf
Create Date: 2026-03-18

"""
from alembic import op
import sqlalchemy as sa

revision = 'b1c2d3e4f5a6'
down_revision = 'f495c04833bf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # mdm_impressions に KPI 4カラム追加
    op.add_column('mdm_impressions', sa.Column('dwell_time_ms', sa.Integer(), nullable=True))
    op.add_column('mdm_impressions', sa.Column('screen_on_count_today', sa.SmallInteger(), nullable=True))
    op.add_column('mdm_impressions', sa.Column('dismiss_type', sa.String(length=20), nullable=True))
    op.add_column('mdm_impressions', sa.Column('hour_of_day', sa.SmallInteger(), nullable=True))
    op.create_index('ix_mdm_impressions_hour_of_day', 'mdm_impressions', ['hour_of_day'])
    # dealers に region カラム追加
    op.add_column('dealers', sa.Column('region', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_index('ix_mdm_impressions_hour_of_day', table_name='mdm_impressions')
    op.drop_column('mdm_impressions', 'hour_of_day')
    op.drop_column('mdm_impressions', 'dismiss_type')
    op.drop_column('mdm_impressions', 'screen_on_count_today')
    op.drop_column('mdm_impressions', 'dwell_time_ms')
    op.drop_column('dealers', 'region')
