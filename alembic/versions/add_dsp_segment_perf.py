"""add dsp_segment_perf table (device segment CTR multipliers)

Revision ID: dspengine0006
Revises: dspengine0005
Create Date: 2026-05-22 20:00:00.000000

#5 入札 ML ベースライン: device(platform) セグメント別の CTR 乗数を定期バッチで
事前計算して保持する dsp_segment_perf テーブルを追加する。
冪等性（教訓14/15/16）: テーブル存在を inspector で確認してから作成する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0006"
down_revision: Union[str, Sequence[str], None] = "dspengine0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if insp.has_table("dsp_segment_perf"):
        return  # 既存なら何もしない（冪等）

    op.create_table(
        "dsp_segment_perf",
        sa.Column("segment", sa.String(length=20), primary_key=True),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("multiplier", sa.Float(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("dsp_segment_perf")
