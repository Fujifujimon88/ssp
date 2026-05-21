"""add dsp_engine tables (dsp_campaigns / dsp_spend_logs / dsp_conversion_events)
and extend dsp_configs for the SSP integration screen.

Revision ID: dspengine0001
Revises: a3b4c5d6e7f8
Create Date: 2026-05-21 00:00:00.000000

冪等性（教訓14）: テーブル・カラム・インデックスの存在を inspector で確認してから
作成するため、SQLite / PostgreSQL いずれでも安全に再実行できる。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "dspengine0001"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_index(insp, table: str, index: str) -> bool:
    return index in {i["name"] for i in insp.get_indexes(table)}


def _create_index_if_missing(insp, name: str, table: str, columns: list, unique: bool = False) -> None:
    if not _has_index(insp, table, name):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    # ── dsp_campaigns ──────────────────────────────────────────
    if not _has_table(insp, "dsp_campaigns"):
        op.create_table(
            "dsp_campaigns",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("advertiser_name", sa.String(length=200), nullable=False),
            sa.Column("campaign_name", sa.String(length=200), nullable=False),
            sa.Column("objective", sa.String(length=20), nullable=False, server_default="roas"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("daily_budget_jpy", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_budget_jpy", sa.Float(), nullable=False, server_default="0"),
            sa.Column("target_roas", sa.Float(), nullable=False, server_default="300"),
            sa.Column("margin_rate", sa.Float(), nullable=False, server_default="0.2"),
            sa.Column("bid_floor_jpy", sa.Float(), nullable=False, server_default="100"),
            sa.Column("bid_cap_jpy", sa.Float(), nullable=False, server_default="5000"),
            sa.Column("avg_purchase_value_jpy", sa.Float(), nullable=False, server_default="3000"),
            sa.Column("base_ctr", sa.Float(), nullable=False, server_default="0.01"),
            sa.Column("target_cvr", sa.Float(), nullable=False, server_default="0.02"),
            sa.Column("creative_title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("creative_body", sa.Text(), nullable=True),
            sa.Column("creative_image_url", sa.String(length=500), nullable=True),
            sa.Column("creative_click_url", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("creative_width", sa.Integer(), nullable=True),
            sa.Column("creative_height", sa.Integer(), nullable=True),
            sa.Column("start_date", sa.Date(), nullable=True),
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("login_id", sa.String(length=64), nullable=True),
            sa.Column("hashed_password", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(insp, "ix_dsp_campaigns_login_id", "dsp_campaigns", ["login_id"], unique=True)

    # ── dsp_spend_logs ─────────────────────────────────────────
    if not _has_table(insp, "dsp_spend_logs"):
        op.create_table(
            "dsp_spend_logs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("campaign_id", sa.String(length=36), nullable=False),
            sa.Column("impression_id", sa.String(length=36), nullable=True),
            sa.Column("click_token", sa.String(length=64), nullable=False),
            sa.Column("platform", sa.String(length=10), nullable=False, server_default="unknown"),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="ssp-node"),
            sa.Column("bid_price_jpy", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cleared_price_jpy", sa.Float(), nullable=False, server_default="0"),
            sa.Column("spend_jpy", sa.Float(), nullable=False, server_default="0"),
            sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(insp, "ix_dsp_spend_logs_campaign_id", "dsp_spend_logs", ["campaign_id"])
    _create_index_if_missing(insp, "ix_dsp_spend_logs_impression_id", "dsp_spend_logs", ["impression_id"])
    _create_index_if_missing(insp, "ix_dsp_spend_logs_click_token", "dsp_spend_logs", ["click_token"], unique=True)
    _create_index_if_missing(insp, "ix_dsp_spend_logs_logged_at", "dsp_spend_logs", ["logged_at"])

    # ── dsp_conversion_events ──────────────────────────────────
    if not _has_table(insp, "dsp_conversion_events"):
        op.create_table(
            "dsp_conversion_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("campaign_id", sa.String(length=36), nullable=False),
            sa.Column("impression_id", sa.String(length=36), nullable=True),
            sa.Column("click_token", sa.String(length=64), nullable=True),
            sa.Column("platform", sa.String(length=10), nullable=False, server_default="unknown"),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="direct"),
            sa.Column("event_type", sa.String(length=50), nullable=False, server_default="purchase"),
            sa.Column("revenue_jpy", sa.Float(), nullable=False, server_default="0"),
            sa.Column("dedup_key", sa.String(length=128), nullable=True),
            sa.Column("raw_payload", sa.Text(), nullable=True),
            sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(insp, "ix_dsp_conversion_events_campaign_id", "dsp_conversion_events", ["campaign_id"])
    _create_index_if_missing(insp, "ix_dsp_conversion_events_impression_id", "dsp_conversion_events", ["impression_id"])
    _create_index_if_missing(insp, "ix_dsp_conversion_events_click_token", "dsp_conversion_events", ["click_token"])
    _create_index_if_missing(insp, "ix_dsp_conversion_events_dedup_key", "dsp_conversion_events", ["dedup_key"], unique=True)
    _create_index_if_missing(insp, "ix_dsp_conversion_events_received_at", "dsp_conversion_events", ["received_at"])

    # ── dsp_configs 拡張（SSP連携画面用） ───────────────────────
    if _has_table(insp, "dsp_configs"):
        if not _has_column(insp, "dsp_configs", "platform_mapping"):
            op.add_column("dsp_configs", sa.Column("platform_mapping", sa.Text(), nullable=True))
        if not _has_column(insp, "dsp_configs", "app_mapping"):
            op.add_column("dsp_configs", sa.Column("app_mapping", sa.Text(), nullable=True))
        if not _has_column(insp, "dsp_configs", "qps_limit"):
            op.add_column("dsp_configs", sa.Column("qps_limit", sa.Integer(), nullable=False, server_default="0"))
        if not _has_column(insp, "dsp_configs", "last_win_rate"):
            op.add_column("dsp_configs", sa.Column("last_win_rate", sa.Float(), nullable=True))
        if not _has_column(insp, "dsp_configs", "last_latency_ms"):
            op.add_column("dsp_configs", sa.Column("last_latency_ms", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_table("dsp_conversion_events")
    op.drop_table("dsp_spend_logs")
    op.drop_table("dsp_campaigns")
    for col in ("last_latency_ms", "last_win_rate", "qps_limit", "app_mapping", "platform_mapping"):
        op.drop_column("dsp_configs", col)
