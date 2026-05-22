"""add dsp_bid_logs table (bid decision log with nbr)

Revision ID: dspengine0005
Revises: dspengine0004
Create Date: 2026-05-22 18:00:00.000000

#4 入札ログ完全化: handle_bid_request の全判定（入札成立 / 各 no-bid 理由）を
no-bid 理由コード nbr 付きで記録する dsp_bid_logs テーブルを追加する。
冪等性（教訓14/15/16）: テーブル存在を inspector で確認してから作成する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0005"
down_revision: Union[str, Sequence[str], None] = "dspengine0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if insp.has_table("dsp_bid_logs"):
        return  # 既存なら何もしない（冪等）

    op.create_table(
        "dsp_bid_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False,
                  server_default="ssp-node"),
        sa.Column("imp_id", sa.String(length=36), nullable=True),
        sa.Column("bidfloor_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(length=10), nullable=False,
                  server_default="no_bid"),
        sa.Column("nbr", sa.Integer(), nullable=True),
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("bid_price_usd", sa.Float(), nullable=True),
        sa.Column("bid_cpm_jpy", sa.Float(), nullable=True),
        sa.Column("shaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paced_out_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Index("ix_dsp_bid_logs_request_id", "request_id"),
        sa.Index("ix_dsp_bid_logs_source", "source"),
        sa.Index("ix_dsp_bid_logs_nbr", "nbr"),
        sa.Index("ix_dsp_bid_logs_campaign_id", "campaign_id"),
        sa.Index("ix_dsp_bid_logs_logged_at", "logged_at"),
    )


def downgrade() -> None:
    op.drop_table("dsp_bid_logs")
