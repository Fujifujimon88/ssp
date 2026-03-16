"""Add take_rate to agencies and agency_id to affiliate_campaigns

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"


def upgrade():
    # agencies に take_rate を追加（既存行は 17.5% にセット）
    op.add_column(
        "agencies",
        sa.Column("take_rate", sa.Float, nullable=False, server_default="0.175"),
    )

    # affiliate_campaigns に agency_id を追加（NULL 許容）
    op.add_column(
        "affiliate_campaigns",
        sa.Column(
            "agency_id",
            sa.Integer,
            sa.ForeignKey("agencies.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_affiliate_campaigns_agency", "affiliate_campaigns", ["agency_id"])


def downgrade():
    op.drop_index("ix_affiliate_campaigns_agency", table_name="affiliate_campaigns")
    op.drop_column("affiliate_campaigns", "agency_id")
    op.drop_column("agencies", "take_rate")
