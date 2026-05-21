"""add supply chain verification columns to dsp_configs

Revision ID: dspengine0004
Revises: dspengine0003
Create Date: 2026-05-22 12:00:00.000000

外部エクスチェンジのサプライチェーン検証（schain / sellers.json）用カラムを
dsp_configs に追加する。
冪等性（教訓14/16）: inspector でカラム存在を確認してから追加する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0004"
down_revision: Union[str, Sequence[str], None] = "dspengine0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if not insp.has_table("dsp_configs"):
        return
    cols = {c["name"] for c in insp.get_columns("dsp_configs")}
    if "schain_required" not in cols:
        op.add_column("dsp_configs", sa.Column("schain_required", sa.Boolean(), nullable=True))
    if "allowed_asi_domains" not in cols:
        op.add_column("dsp_configs", sa.Column("allowed_asi_domains", sa.Text(), nullable=True))
    if "sellers_json_url" not in cols:
        op.add_column("dsp_configs", sa.Column("sellers_json_url", sa.String(length=500), nullable=True))
    if "sellers_json_cache" not in cols:
        op.add_column("dsp_configs", sa.Column("sellers_json_cache", sa.Text(), nullable=True))
    if "sellers_json_cached_at" not in cols:
        op.add_column(
            "dsp_configs",
            sa.Column("sellers_json_cached_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("dsp_configs", "sellers_json_cached_at")
    op.drop_column("dsp_configs", "sellers_json_cache")
    op.drop_column("dsp_configs", "sellers_json_url")
    op.drop_column("dsp_configs", "allowed_asi_domains")
    op.drop_column("dsp_configs", "schain_required")
