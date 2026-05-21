"""add dsp_click_events table (click tracking)

Revision ID: dspengine0002
Revises: dspengine0001
Create Date: 2026-05-22 00:00:00.000000

クリック計測用に dsp_click_events テーブルを新設する。クリックトラッカーが
呼ばれるたびに1行記録し、CTR・日別クリックを clicked_at 基準で集計する。
冪等性（教訓14/16）: inspector でテーブル/インデックス存在を確認してから作成する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0002"
down_revision: Union[str, Sequence[str], None] = "dspengine0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(insp, table: str, name: str) -> bool:
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    if not insp.has_table("dsp_click_events"):
        op.create_table(
            "dsp_click_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("campaign_id", sa.String(length=36), nullable=False),
            sa.Column("click_token", sa.String(length=64), nullable=False),
            sa.Column("impression_id", sa.String(length=36), nullable=True),
            sa.Column("platform", sa.String(length=10), nullable=False, server_default="unknown"),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="ssp-node"),
            sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    for name, col in [
        ("ix_dsp_click_events_campaign_id", "campaign_id"),
        ("ix_dsp_click_events_click_token", "click_token"),
        ("ix_dsp_click_events_impression_id", "impression_id"),
        ("ix_dsp_click_events_clicked_at", "clicked_at"),
    ]:
        if not _has_index(insp, "dsp_click_events", name):
            op.create_index(name, "dsp_click_events", [col])


def downgrade() -> None:
    op.drop_table("dsp_click_events")
