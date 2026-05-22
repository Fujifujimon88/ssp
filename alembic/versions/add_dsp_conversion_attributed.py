"""add attributed column to dsp_conversion_events (#9)

Revision ID: dspengine0011
Revises: dspengine0010
Create Date: 2026-05-22 00:00:00.000000

#9 アトリビューション窓（lookback window）:
  - dsp_conversion_events.attributed … 窓内=True（ROAS算入）/ 窓外=False（ROAS非算入）
    既存行は全て attributed=True（後方互換）

冪等性（教訓14/16/19）: inspector でカラム存在を確認してから add_column する。
本番 DB への upgrade は禁止（dev/test のみ）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0011"
down_revision: Union[str, Sequence[str], None] = "dspengine0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, name: str) -> bool:
    return name in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    if insp.has_table("dsp_conversion_events"):
        if not _has_column(insp, "dsp_conversion_events", "attributed"):
            op.add_column(
                "dsp_conversion_events",
                sa.Column(
                    "attributed",
                    sa.Boolean(),
                    nullable=False,
                    server_default="1",
                ),
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    if insp.has_table("dsp_conversion_events"):
        if _has_column(insp, "dsp_conversion_events", "attributed"):
            op.drop_column("dsp_conversion_events", "attributed")
