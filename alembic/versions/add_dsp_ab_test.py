"""add A/B test + holdout tables for dsp_engine (#7)

Revision ID: dspengine0009
Revises: dspengine0008
Create Date: 2026-05-22 23:30:00.000000

#7 A/B テスト・holdout 基盤:
  - dsp_creatives        … クリエイティブ 1:N 化（campaign : creatives = 1:N）
  - dsp_ab_experiments   … A/B 実験のメタデータ（開始/終了/winner 宣言）
  - dsp_campaigns.holdout_rate … holdout 割合（入札時に意図的ノービッド）

既存キャンペーンのインライン素材を dsp_creatives へ backfill する
（id = dsp_campaigns.creative_id に揃え、creative 軸レポートと整合させる）。
冪等性（教訓14/16）: inspector でテーブル/カラム/インデックス存在を確認してから追加する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0009"
down_revision: Union[str, Sequence[str], None] = "dspengine0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(insp, table: str, name: str) -> bool:
    return name in {c["name"] for c in insp.get_columns(table)}


def _has_index(insp, table: str, name: str) -> bool:
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    # ── dsp_creatives（クリエイティブ 1:N） ──
    if not insp.has_table("dsp_creatives"):
        op.create_table(
            "dsp_creatives",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("campaign_id", sa.String(length=36),
                      sa.ForeignKey("dsp_campaigns.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("image_url", sa.String(length=500), nullable=True),
            sa.Column("click_url", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("weight", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    # ── dsp_ab_experiments（A/B 実験メタデータ） ──
    if not insp.has_table("dsp_ab_experiments"):
        op.create_table(
            "dsp_ab_experiments",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("campaign_id", sa.String(length=36),
                      sa.ForeignKey("dsp_campaigns.id"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("winner_creative_id", sa.String(length=36), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    # ── dsp_campaigns.holdout_rate ──
    if insp.has_table("dsp_campaigns") and not _has_column(insp, "dsp_campaigns", "holdout_rate"):
        op.add_column(
            "dsp_campaigns",
            sa.Column("holdout_rate", sa.Float(), nullable=False, server_default="0.0"),
        )

    # 上の DDL（create_table 等）を反映した最新状態で index/backfill を判定する。
    # 冒頭の inspector は DDL 前の状態をキャッシュしているため再取得が必須。
    insp = inspect(conn)
    if not _has_index(insp, "dsp_creatives", "ix_dsp_creatives_campaign_id"):
        op.create_index("ix_dsp_creatives_campaign_id", "dsp_creatives", ["campaign_id"])
    if not _has_index(insp, "dsp_ab_experiments", "ix_dsp_ab_experiments_campaign_id"):
        op.create_index(
            "ix_dsp_ab_experiments_campaign_id", "dsp_ab_experiments", ["campaign_id"]
        )

    # ── 既存キャンペーンのインライン素材を dsp_creatives へ backfill ──
    # id を dsp_campaigns.creative_id に揃える（本番は campaign 0 件で実質 no-op）。
    if insp.has_table("dsp_campaigns") and insp.has_table("dsp_creatives"):
        op.execute(
            """
            INSERT INTO dsp_creatives
                (id, campaign_id, name, title, body, image_url, click_url,
                 width, height, status, weight, created_at)
            SELECT creative_id, id, '主素材', creative_title, creative_body,
                   creative_image_url, creative_click_url, creative_width,
                   creative_height, 'active', 100, created_at
            FROM dsp_campaigns
            WHERE creative_id IS NOT NULL
              AND creative_id NOT IN (SELECT id FROM dsp_creatives)
            """
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)
    if insp.has_table("dsp_campaigns") and _has_column(insp, "dsp_campaigns", "holdout_rate"):
        op.drop_column("dsp_campaigns", "holdout_rate")
    if insp.has_table("dsp_ab_experiments"):
        op.drop_table("dsp_ab_experiments")
    if insp.has_table("dsp_creatives"):
        op.drop_table("dsp_creatives")
