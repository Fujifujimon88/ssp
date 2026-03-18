"""add dealer_push_logs table

Revision ID: f495c04833bf
Revises: 6b4cf98a0c79
Create Date: 2026-03-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f495c04833bf'
down_revision = '6b4cf98a0c79'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'dealer_push_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('dealer_id', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('android_sent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('ios_sent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_devices', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['dealer_id'], ['dealers.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_dealer_push_logs_dealer_id', 'dealer_push_logs', ['dealer_id'])
    op.create_index('ix_dealer_push_logs_sent_at', 'dealer_push_logs', ['sent_at'])


def downgrade() -> None:
    op.drop_index('ix_dealer_push_logs_sent_at', table_name='dealer_push_logs')
    op.drop_index('ix_dealer_push_logs_dealer_id', table_name='dealer_push_logs')
    op.drop_table('dealer_push_logs')
