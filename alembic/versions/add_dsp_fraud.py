"""add fraud/IVT/brand-safety columns for dsp_engine (#8)

Revision ID: dspengine0010
Revises: dspengine0009
Create Date: 2026-05-22 23:45:00.000000

#8 fraud / IVT / brand safety 監視:
  - dsp_campaigns.bcat_block  … ブロックする IAB カテゴリ（JSON 配列文字列）
  - dsp_campaigns.badv_block  … ブロックする広告主ドメイン（JSON 配列文字列）

冪等性（教訓14/16）: inspector でカラム存在を確認してから add_column する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0010"
down_revision: Union[str, Sequence[str], None] = "dspengine0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, name: str) -> bool:
    return name in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    if insp.has_table("dsp_campaigns"):
        if not _has_column(insp, "dsp_campaigns", "bcat_block"):
            op.add_column(
                "dsp_campaigns",
                sa.Column("bcat_block", sa.Text(), nullable=True, server_default="[]"),
            )
        if not _has_column(insp, "dsp_campaigns", "badv_block"):
            op.add_column(
                "dsp_campaigns",
                sa.Column("badv_block", sa.Text(), nullable=True, server_default="[]"),
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    if insp.has_table("dsp_campaigns"):
        if _has_column(insp, "dsp_campaigns", "badv_block"):
            op.drop_column("dsp_campaigns", "badv_block")
        if _has_column(insp, "dsp_campaigns", "bcat_block"):
            op.drop_column("dsp_campaigns", "bcat_block")
