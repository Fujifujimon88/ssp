"""MDM アフィリエイト アトリビューション v2

- android_commands: campaign_id, store_id
- affiliate_campaigns: cv_trigger, postback_url_template
- install_events: cv_method, app_open_at, store_id, dealer_id
- android_devices: store_id, dealer_id
- dealers: default_cv_trigger

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-03-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # android_commands: サーバー主権の campaign_id + 店舗追跡
    op.add_column("android_commands", sa.Column("campaign_id", sa.String(36), nullable=True))
    op.add_column("android_commands", sa.Column("store_id", sa.String(36), nullable=True))
    op.create_index("ix_android_commands_campaign_id", "android_commands", ["campaign_id"])
    op.create_index("ix_android_commands_store_id", "android_commands", ["store_id"])

    # affiliate_campaigns: CV計測方法 + 直接ASPポストバックURL
    op.add_column(
        "affiliate_campaigns",
        sa.Column("cv_trigger", sa.String(20), nullable=False, server_default="install"),
    )
    op.add_column("affiliate_campaigns", sa.Column("postback_url_template", sa.Text(), nullable=True))

    # install_events: CV方法 + app_open記録 + 店舗・代理店トレース
    op.add_column(
        "install_events",
        sa.Column("cv_method", sa.String(20), nullable=False, server_default="install"),
    )
    op.add_column("install_events", sa.Column("app_open_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("install_events", sa.Column("store_id", sa.String(36), nullable=True))
    op.add_column("install_events", sa.Column("dealer_id", sa.String(36), nullable=True))
    op.create_index("ix_install_events_store_id", "install_events", ["store_id"])
    op.create_index("ix_install_events_dealer_id", "install_events", ["dealer_id"])

    # android_devices: store_id + dealer_id 追加
    op.add_column("android_devices", sa.Column("store_id", sa.String(36), nullable=True))
    op.add_column("android_devices", sa.Column("dealer_id", sa.String(36), nullable=True))
    op.create_index("ix_android_devices_store_id", "android_devices", ["store_id"])
    op.create_index("ix_android_devices_dealer_id", "android_devices", ["dealer_id"])

    # dealers: 代理店デフォルトcv_trigger
    op.add_column("dealers", sa.Column("default_cv_trigger", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("dealers", "default_cv_trigger")

    op.drop_index("ix_android_devices_dealer_id", table_name="android_devices")
    op.drop_index("ix_android_devices_store_id", table_name="android_devices")
    op.drop_column("android_devices", "dealer_id")
    op.drop_column("android_devices", "store_id")

    op.drop_index("ix_install_events_dealer_id", table_name="install_events")
    op.drop_index("ix_install_events_store_id", table_name="install_events")
    op.drop_column("install_events", "dealer_id")
    op.drop_column("install_events", "store_id")
    op.drop_column("install_events", "app_open_at")
    op.drop_column("install_events", "cv_method")

    op.drop_column("affiliate_campaigns", "postback_url_template")
    op.drop_column("affiliate_campaigns", "cv_trigger")

    op.drop_index("ix_android_commands_store_id", table_name="android_commands")
    op.drop_index("ix_android_commands_campaign_id", table_name="android_commands")
    op.drop_column("android_commands", "store_id")
    op.drop_column("android_commands", "campaign_id")
