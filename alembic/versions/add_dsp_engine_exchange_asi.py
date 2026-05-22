"""add exchange_asi column to dsp_configs

Revision ID: dspengine0007
Revises: dspengine0006
Create Date: 2026-05-22 22:00:00.000000

レビュー指摘1 の修正: schain 最終ノード照合用に、エクスチェンジ自身の
asi ドメインを保持する exchange_asi カラムを dsp_configs に追加する。
接続名(name)は任意文字列のため asi に流用できない。
冪等性（教訓14/15/16）: inspector でカラム存在を確認してから追加する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0007"
down_revision: Union[str, Sequence[str], None] = "dspengine0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if not insp.has_table("dsp_configs"):
        return
    cols = {c["name"] for c in insp.get_columns("dsp_configs")}
    if "exchange_asi" not in cols:
        op.add_column(
            "dsp_configs", sa.Column("exchange_asi", sa.String(length=255), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("dsp_configs", "exchange_asi")
