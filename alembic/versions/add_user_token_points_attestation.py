"""add user_token, points, attestation columns

Revision ID: e5f6a7b8c9d0
Revises: f4a5b6c7d8e9
Create Date: 2026-03-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # android_devices: user_token（ASPに渡す不透明UUID）
    conn.execute(sa.text("""
        ALTER TABLE android_devices
        ADD COLUMN IF NOT EXISTS user_token VARCHAR(20)
    """))
    conn.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_android_devices_user_token
        ON android_devices (user_token)
    """))

    # affiliate_campaigns: ポイント付与設定
    conn.execute(sa.text("""
        ALTER TABLE affiliate_campaigns
        ADD COLUMN IF NOT EXISTS enable_points BOOLEAN NOT NULL DEFAULT false
    """))
    conn.execute(sa.text("""
        ALTER TABLE affiliate_campaigns
        ADD COLUMN IF NOT EXISTS point_rate FLOAT NOT NULL DEFAULT 1.0
    """))

    # affiliate_conversions: 2段階通知・ASP固有CV ID・user_token
    conn.execute(sa.text("""
        ALTER TABLE affiliate_conversions
        ADD COLUMN IF NOT EXISTS attestation_status VARCHAR(20)
    """))
    conn.execute(sa.text("""
        ALTER TABLE affiliate_conversions
        ADD COLUMN IF NOT EXISTS asp_action_id VARCHAR(128)
    """))
    conn.execute(sa.text("""
        ALTER TABLE affiliate_conversions
        ADD COLUMN IF NOT EXISTS user_token VARCHAR(20)
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_affiliate_conversions_asp_action_id
        ON affiliate_conversions (asp_action_id)
    """))

    # user_points: ポイント付与履歴テーブル（新規作成）
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS user_points (
            id VARCHAR(36) PRIMARY KEY,
            user_token VARCHAR(20) NOT NULL,
            conversion_id VARCHAR(36) NOT NULL UNIQUE REFERENCES affiliate_conversions(id),
            points FLOAT NOT NULL DEFAULT 0.0,
            awarded_at TIMESTAMP
        )
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_user_points_user_token
        ON user_points (user_token)
    """))
    conn.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_user_points_conversion_id
        ON user_points (conversion_id)
    """))


def downgrade() -> None:
    op.drop_table("user_points")
    op.drop_index("ix_affiliate_conversions_asp_action_id", table_name="affiliate_conversions")
    op.drop_column("affiliate_conversions", "user_token")
    op.drop_column("affiliate_conversions", "asp_action_id")
    op.drop_column("affiliate_conversions", "attestation_status")
    op.drop_column("affiliate_campaigns", "point_rate")
    op.drop_column("affiliate_campaigns", "enable_points")
    op.drop_index("ix_android_devices_user_token", table_name="android_devices")
    op.drop_column("android_devices", "user_token")
