"""add composite indexes to dsp event and bid log tables (#10 Phase 1)

Revision ID: dspengine0012
Revises: dspengine0011
Create Date: 2026-05-28 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "dspengine0012"
down_revision: Union[str, Sequence[str], None] = "dspengine0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(insp, table: str, name: str) -> bool:
    return name in {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    if insp.has_table("dsp_spend_logs"):
        if not _has_index(insp, "dsp_spend_logs", "ix_dsp_spend_logs_campaign_logged"):
            op.create_index("ix_dsp_spend_logs_campaign_logged", "dsp_spend_logs", ["campaign_id", "logged_at"])

    if insp.has_table("dsp_click_events"):
        if not _has_index(insp, "dsp_click_events", "ix_dsp_click_events_campaign_clicked"):
            op.create_index("ix_dsp_click_events_campaign_clicked", "dsp_click_events", ["campaign_id", "clicked_at"])

    if insp.has_table("dsp_conversion_events"):
        if not _has_index(insp, "dsp_conversion_events", "ix_dsp_conv_events_campaign_attributed_received"):
            op.create_index("ix_dsp_conv_events_campaign_attributed_received", "dsp_conversion_events", ["campaign_id", "attributed", "received_at"])

    if insp.has_table("dsp_bid_logs"):
        if not _has_index(insp, "dsp_bid_logs", "ix_dsp_bid_logs_outcome_campaign"):
            op.create_index("ix_dsp_bid_logs_outcome_campaign", "dsp_bid_logs", ["outcome", "campaign_id"])
        if not _has_index(insp, "dsp_bid_logs", "ix_dsp_bid_logs_campaign_nbr_logged"):
            op.create_index("ix_dsp_bid_logs_campaign_nbr_logged", "dsp_bid_logs", ["campaign_id", "nbr", "logged_at"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = inspect(conn)

    if insp.has_table("dsp_bid_logs"):
        if _has_index(insp, "dsp_bid_logs", "ix_dsp_bid_logs_campaign_nbr_logged"):
            op.drop_index("ix_dsp_bid_logs_campaign_nbr_logged", table_name="dsp_bid_logs")
        if _has_index(insp, "dsp_bid_logs", "ix_dsp_bid_logs_outcome_campaign"):
            op.drop_index("ix_dsp_bid_logs_outcome_campaign", table_name="dsp_bid_logs")
    if insp.has_table("dsp_conversion_events"):
        if _has_index(insp, "dsp_conversion_events", "ix_dsp_conv_events_campaign_attributed_received"):
            op.drop_index("ix_dsp_conv_events_campaign_attributed_received", table_name="dsp_conversion_events")
    if insp.has_table("dsp_click_events"):
        if _has_index(insp, "dsp_click_events", "ix_dsp_click_events_campaign_clicked"):
            op.drop_index("ix_dsp_click_events_campaign_clicked", table_name="dsp_click_events")
    if insp.has_table("dsp_spend_logs"):
        if _has_index(insp, "dsp_spend_logs", "ix_dsp_spend_logs_campaign_logged"):
            op.drop_index("ix_dsp_spend_logs_campaign_logged", table_name="dsp_spend_logs")
