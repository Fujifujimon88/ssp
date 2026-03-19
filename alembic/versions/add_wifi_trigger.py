"""add wifi_trigger_rules and wifi_checkin_logs

Revision ID: c9d0e1f2a3b4
Revises: b1c2d3e4f5a6
Branch labels: None
Depends on: None
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9d0e1f2a3b4'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'wifi_trigger_rules',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('ssid', sa.String(64), nullable=False),
        sa.Column('dealer_id', sa.String(36), sa.ForeignKey('dealers.id'), nullable=True),
        sa.Column('action_type', sa.String(32), nullable=False),   # push | line | point
        sa.Column('action_config', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('cooldown_minutes', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_wifi_trigger_rules_ssid', 'wifi_trigger_rules', ['ssid'])
    op.create_index('ix_wifi_trigger_rules_dealer_id', 'wifi_trigger_rules', ['dealer_id'])

    op.create_table(
        'wifi_checkin_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('device_id', sa.String(64), nullable=False),
        sa.Column('ssid', sa.String(64), nullable=False),
        sa.Column('dealer_id', sa.String(36), nullable=True),
        sa.Column('actions_fired', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('triggered_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_wifi_checkin_logs_device_id', 'wifi_checkin_logs', ['device_id'])
    op.create_index('ix_wifi_checkin_logs_triggered_at', 'wifi_checkin_logs', ['triggered_at'])


def downgrade() -> None:
    op.drop_index('ix_wifi_checkin_logs_triggered_at', table_name='wifi_checkin_logs')
    op.drop_index('ix_wifi_checkin_logs_device_id', table_name='wifi_checkin_logs')
    op.drop_table('wifi_checkin_logs')

    op.drop_index('ix_wifi_trigger_rules_dealer_id', table_name='wifi_trigger_rules')
    op.drop_index('ix_wifi_trigger_rules_ssid', table_name='wifi_trigger_rules')
    op.drop_table('wifi_trigger_rules')
