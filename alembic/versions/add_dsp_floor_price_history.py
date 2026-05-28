"""add dsp_floor_price_history table (#11 Phase 1)

Revision ID: dspengine0013
Revises: dspengine0012
Create Date: 2026-05-28 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "dspengine0013"
down_revision: Union[str, Sequence[str], None] = "dspengine0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(insp, table: str, name: str) -> bool:
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if not insp.has_table("dsp_floor_price_history"):
        op.create_table(
            "dsp_floor_price_history",
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column("publisher_id", sa.String(64), nullable=False),
            sa.Column("floor_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("floor_jpy", sa.Float(), nullable=False, server_default="0"),
            sa.Column("win_rate", sa.Float(), nullable=False, server_default="0"),
            sa.Column("bid_density", sa.Float(), nullable=False, server_default="0"),
            sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        )
    # 教訓19: DDL 後に inspector を再取得
    insp = inspect(conn)
    if insp.has_table("dsp_floor_price_history"):
        if not _has_index(insp, "dsp_floor_price_history", "ix_dsp_floor_hist_pub_computed"):
            op.create_index(
                "ix_dsp_floor_hist_pub_computed",
                "dsp_floor_price_history",
                ["publisher_id", "computed_at"],
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if insp.has_table("dsp_floor_price_history"):
        if _has_index(insp, "dsp_floor_price_history", "ix_dsp_floor_hist_pub_computed"):
            op.drop_index(
                "ix_dsp_floor_hist_pub_computed",
                table_name="dsp_floor_price_history",
            )
        op.drop_table("dsp_floor_price_history")
