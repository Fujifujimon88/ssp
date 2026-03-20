"""MDMプロファイル消失防止: iOSDeviceDB / DeviceDB / AndroidDeviceDB にフィールド追加

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-03-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # iOSDeviceDB: プロファイル状態管理
    op.add_column("ios_devices", sa.Column("profile_status", sa.String(20), nullable=False, server_default="unknown"))
    op.add_column("ios_devices", sa.Column("last_profile_check_at", sa.DateTime(), nullable=True))

    # DeviceDB: 再エンロール管理・token有効期限
    op.add_column("devices", sa.Column("re_enroll_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("devices", sa.Column("token_revoked_at", sa.DateTime(), nullable=True))
    op.add_column("devices", sa.Column("token_expires_at", sa.DateTime(), nullable=True))

    # AndroidDeviceDB: 機種変更引き継ぎ・fingerprint
    op.add_column("android_devices", sa.Column("previous_device_id", sa.String(64), nullable=True))
    op.add_column("android_devices", sa.Column("migrated_at", sa.DateTime(), nullable=True))
    op.add_column("android_devices", sa.Column("device_fingerprint", sa.String(64), nullable=True))
    op.add_column("android_devices", sa.Column("migration_suspicious", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("ios_devices", "profile_status")
    op.drop_column("ios_devices", "last_profile_check_at")

    op.drop_column("devices", "re_enroll_count")
    op.drop_column("devices", "token_revoked_at")
    op.drop_column("devices", "token_expires_at")

    op.drop_column("android_devices", "previous_device_id")
    op.drop_column("android_devices", "migrated_at")
    op.drop_column("android_devices", "device_fingerprint")
    op.drop_column("android_devices", "migration_suspicious")
