"""Add agencies and invoices tables

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"


def upgrade():
    op.create_table(
        "agencies",
        sa.Column("id",            sa.Integer, primary_key=True),
        sa.Column("name",          sa.String(128), nullable=False),
        sa.Column("api_key",       sa.String(64),  nullable=False, unique=True),
        sa.Column("contact_email", sa.String(256)),
        sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "invoices",
        sa.Column("id",                   sa.Integer, primary_key=True),
        sa.Column("period_month",         sa.String(7), nullable=False),
        sa.Column("campaign_id",          sa.Integer, sa.ForeignKey("affiliate_campaigns.id")),
        sa.Column("agency_id",            sa.Integer, sa.ForeignKey("agencies.id"), nullable=True),
        sa.Column("gross_revenue_jpy",    sa.Integer, nullable=False, default=0),
        sa.Column("take_rate",            sa.Float,   nullable=False, default=0.175),
        sa.Column("platform_fee_jpy",     sa.Integer, nullable=False, default=0),
        sa.Column("net_payable_jpy",      sa.Integer, nullable=False, default=0),
        sa.Column("cpi_count",            sa.Integer, nullable=False, default=0),
        sa.Column("impression_count",     sa.Integer, nullable=False, default=0),
        sa.Column("video_complete_count", sa.Integer, nullable=False, default=0),
        sa.Column("status",               sa.String(16), nullable=False, default="draft"),
        sa.Column("created_at",           sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at",              sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_invoices_period", "invoices", ["period_month"])
    op.create_index("ix_invoices_campaign", "invoices", ["campaign_id"])


def downgrade():
    op.drop_table("invoices")
    op.drop_table("agencies")
