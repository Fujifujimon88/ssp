"""JANet ASP 連携フィールド追加

- affiliate_campaigns: janet_media_id, janet_original_id
- affiliate_clicks: device_id (Android ID でユーザー照合)

Revision ID: b2c3d4e5f6a7
Revises: a8b9c0d1e2f3
Create Date: 2026-03-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # affiliate_campaigns: JANet メディアID・原稿ID
    op.add_column("affiliate_campaigns", sa.Column("janet_media_id", sa.String(50), nullable=True))
    op.add_column("affiliate_campaigns", sa.Column("janet_original_id", sa.String(50), nullable=True))

    # affiliate_clicks: Android device_id を UserID として記録
    op.add_column("affiliate_clicks", sa.Column("device_id", sa.String(64), nullable=True))
    op.create_index("ix_affiliate_clicks_device_id", "affiliate_clicks", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_affiliate_clicks_device_id", table_name="affiliate_clicks")
    op.drop_column("affiliate_clicks", "device_id")
    op.drop_column("affiliate_campaigns", "janet_original_id")
    op.drop_column("affiliate_campaigns", "janet_media_id")
