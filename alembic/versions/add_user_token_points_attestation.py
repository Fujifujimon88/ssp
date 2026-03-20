"""add user_token, points, attestation columns

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-03-20 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # android_devices: user_token（ASPに渡す不透明UUID）
    op.add_column("android_devices", sa.Column("user_token", sa.String(20), nullable=True))
    op.create_index("ix_android_devices_user_token", "android_devices", ["user_token"], unique=True)

    # affiliate_campaigns: ポイント付与設定
    op.add_column("affiliate_campaigns", sa.Column("enable_points", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("affiliate_campaigns", sa.Column("point_rate", sa.Float(), nullable=False, server_default="1.0"))

    # affiliate_conversions: 2段階通知・ASP固有CV ID・user_token
    op.add_column("affiliate_conversions", sa.Column("attestation_status", sa.String(20), nullable=True))
    op.add_column("affiliate_conversions", sa.Column("asp_action_id", sa.String(128), nullable=True))
    op.add_column("affiliate_conversions", sa.Column("user_token", sa.String(20), nullable=True))
    op.create_index("ix_affiliate_conversions_asp_action_id", "affiliate_conversions", ["asp_action_id"])

    # user_points: ポイント付与履歴テーブル（新規作成）
    op.create_table(
        "user_points",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_token", sa.String(20), nullable=False, index=True),
        sa.Column("conversion_id", sa.String(36), sa.ForeignKey("affiliate_conversions.id"), nullable=False, unique=True),
        sa.Column("points", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("awarded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_points_user_token", "user_points", ["user_token"])
    op.create_index("ix_user_points_conversion_id", "user_points", ["conversion_id"], unique=True)


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
