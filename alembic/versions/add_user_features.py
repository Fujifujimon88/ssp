"""add user_features table (ML-01)

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-03-17 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_features table for ML-01 feature pipeline.

    Stores per-device aggregated behavioural features computed from the last
    30 days of mdm_impressions.  Rows are upserted daily at 02:00 JST.

    Privacy note: device_id is a pseudonymous UUID — no PII (name / phone /
    email) is stored in this table.  Only devices with consent_given=True are
    included by the pipeline.
    """
    op.create_table(
        'user_features',
        sa.Column('device_id', sa.String(length=255), nullable=False),
        # 過去30日impression集計
        sa.Column('impression_count_30d', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('click_count_30d', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('ctr_30d', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('avg_dwell_ms', sa.Float(), nullable=True),
        # CTR最大の時間帯（0-23）
        sa.Column('preferred_hour', sa.Integer(), nullable=True),
        # 最頻出 dismiss タイプ
        sa.Column('dominant_dismiss_type', sa.String(length=20), nullable=True),
        # デバイスプロファイルスナップショット
        sa.Column('carrier', sa.String(length=100), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('region', sa.String(length=20), nullable=True),
        sa.Column('feature_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('device_id'),
    )


def downgrade() -> None:
    """Drop user_features table."""
    op.drop_table('user_features')
