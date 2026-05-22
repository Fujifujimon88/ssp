"""add multi-dimension report columns to dsp event tables (#6)

Revision ID: dspengine0008
Revises: dspengine0007
Create Date: 2026-05-22 23:00:00.000000

#6 多次元レポート拡張: creative / publisher / app / placement / geo / deal_id の
6 軸を 3 イベントテーブル（spend/click/conversion）に非正規化記録するための列を追加。
dsp_campaigns には creative_id（レポート creative 軸）を追加し、既存行を id で backfill する。
冪等性（教訓14/16）: inspector でカラム/インデックス存在を確認してから追加する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0008"
down_revision: Union[str, Sequence[str], None] = "dspengine0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# イベント 3 テーブルに共通で足す 6 軸（カラム名 -> 長さ）
_DIM_COLUMNS = {
    "creative_id": 36,
    "publisher_id": 64,
    "app_id": 64,
    "placement": 64,
    "geo": 8,
    "deal_id": 64,
}
_EVENT_TABLES = ("dsp_spend_logs", "dsp_click_events", "dsp_conversion_events")


def _has_column(insp, table: str, name: str) -> bool:
    return name in {c["name"] for c in insp.get_columns(table)}


def _has_index(insp, table: str, name: str) -> bool:
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    # dsp_campaigns.creative_id（レポート creative 軸。1:N 化は #7）
    if insp.has_table("dsp_campaigns") and not _has_column(insp, "dsp_campaigns", "creative_id"):
        op.add_column(
            "dsp_campaigns",
            sa.Column("creative_id", sa.String(length=36), nullable=True),
        )
        # 既存キャンペーンは creative_id = id で backfill（本番は campaign 0 件で実質 no-op）
        op.execute(
            "UPDATE dsp_campaigns SET creative_id = id WHERE creative_id IS NULL"
        )

    # 3 イベントテーブルに 6 軸カラム + インデックスを追加
    for table in _EVENT_TABLES:
        if not insp.has_table(table):
            continue
        for col, length in _DIM_COLUMNS.items():
            if not _has_column(insp, table, col):
                op.add_column(
                    table,
                    sa.Column(col, sa.String(length=length), nullable=True),
                )
            idx = f"ix_{table}_{col}"
            if not _has_index(insp, table, idx):
                op.create_index(idx, table, [col])


def downgrade() -> None:
    for table in _EVENT_TABLES:
        for col in _DIM_COLUMNS:
            op.drop_index(f"ix_{table}_{col}", table_name=table)
            op.drop_column(table, col)
    op.drop_column("dsp_campaigns", "creative_id")
