"""add api_secret column to dsp_configs (exchange authentication)

Revision ID: dspengine0003
Revises: dspengine0002
Create Date: 2026-05-22 02:00:00.000000

外部エクスチェンジのなりすまし防止用に共有シークレット列を追加する。
冪等性（教訓14/16）: inspector でカラム存在を確認してから追加する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0003"
down_revision: Union[str, Sequence[str], None] = "dspengine0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if not insp.has_table("dsp_configs"):
        return
    cols = {c["name"] for c in insp.get_columns("dsp_configs")}
    if "api_secret" not in cols:
        op.add_column("dsp_configs", sa.Column("api_secret", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("dsp_configs", "api_secret")
