"""add install_events and postback_logs tables

Revision ID: a1b2c3d4e5f6
Revises: 5b1cd629f6ec
Create Date: 2026-03-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '5b1cd629f6ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create install_events and postback_logs tables; add new columns to
    affiliate_campaigns and android_devices."""

    # ── install_events ─────────────────────────────────────────
    op.create_table(
        'install_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('device_id', sa.String(length=255), nullable=False),
        sa.Column('package_name', sa.String(length=255), nullable=False),
        sa.Column('campaign_id', sa.String(length=36), nullable=False),
        sa.Column('install_ts', sa.BigInteger(), nullable=False),
        sa.Column('apk_sha256', sa.String(length=64), nullable=True),
        sa.Column('billing_status', sa.String(length=20), nullable=False),
        sa.Column('postback_status', sa.String(length=20), nullable=False),
        sa.Column('postback_attempts', sa.Integer(), nullable=False),
        sa.Column('cpi_amount', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['campaign_id'], ['affiliate_campaigns.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_install_events_device_id'), 'install_events', ['device_id'], unique=False
    )
    op.create_index(
        op.f('ix_install_events_campaign_id'), 'install_events', ['campaign_id'], unique=False
    )

    # ── postback_logs ──────────────────────────────────────────
    op.create_table(
        'postback_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('install_event_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('request_url', sa.Text(), nullable=False),
        sa.Column('response_status', sa.Integer(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['install_event_id'], ['install_events.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_postback_logs_install_event_id'),
        'postback_logs',
        ['install_event_id'],
        unique=False,
    )

    # ── affiliate_campaigns — new columns ──────────────────────
    op.add_column(
        'affiliate_campaigns',
        sa.Column('adjust_event_token', sa.String(length=200), nullable=True),
    )
    op.add_column(
        'affiliate_campaigns',
        sa.Column('advertising_id_field', sa.String(length=100), nullable=True),
    )

    # ── android_devices — gaid column ─────────────────────────
    op.add_column(
        'android_devices',
        sa.Column('gaid', sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    """Reverse: drop new tables and columns."""
    op.drop_column('android_devices', 'gaid')
    op.drop_column('affiliate_campaigns', 'advertising_id_field')
    op.drop_column('affiliate_campaigns', 'adjust_event_token')
    op.drop_index(op.f('ix_postback_logs_install_event_id'), table_name='postback_logs')
    op.drop_table('postback_logs')
    op.drop_index(op.f('ix_install_events_campaign_id'), table_name='install_events')
    op.drop_index(op.f('ix_install_events_device_id'), table_name='install_events')
    op.drop_table('install_events')
