"""add video fields (BKD-05) and VTA fields (BKD-09)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-03-17 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add video columns to creatives, video_event to mdm_impressions,
    VTA columns to install_events and affiliate_campaigns."""

    # ── creatives: video fields ────────────────────────────────
    op.add_column(
        'creatives',
        sa.Column('video_url', sa.Text(), nullable=True),
    )
    op.add_column(
        'creatives',
        sa.Column('video_duration_sec', sa.Integer(), nullable=True),
    )
    op.add_column(
        'creatives',
        sa.Column('skip_after_sec', sa.Integer(), nullable=False, server_default='5'),
    )
    op.add_column(
        'creatives',
        sa.Column('creative_type', sa.String(length=20), nullable=False, server_default='banner'),
    )

    # ── mdm_impressions: video_event ───────────────────────────
    op.add_column(
        'mdm_impressions',
        sa.Column('video_event', sa.String(length=30), nullable=True),
    )

    # ── install_events: VTA fields ─────────────────────────────
    op.add_column(
        'install_events',
        sa.Column('attribution_type', sa.String(length=20), nullable=False, server_default='click'),
    )
    op.add_column(
        'install_events',
        sa.Column('vta_impression_id', sa.String(length=36), nullable=True),
    )

    # ── affiliate_campaigns: VTA config ────────────────────────
    op.add_column(
        'affiliate_campaigns',
        sa.Column('vta_window_hours', sa.Integer(), nullable=False, server_default='24'),
    )
    op.add_column(
        'affiliate_campaigns',
        sa.Column('vta_cpi_rate', sa.Float(), nullable=False, server_default='0.5'),
    )


def downgrade() -> None:
    """Reverse: drop added columns."""
    op.drop_column('affiliate_campaigns', 'vta_cpi_rate')
    op.drop_column('affiliate_campaigns', 'vta_window_hours')
    op.drop_column('install_events', 'vta_impression_id')
    op.drop_column('install_events', 'attribution_type')
    op.drop_column('mdm_impressions', 'video_event')
    op.drop_column('creatives', 'creative_type')
    op.drop_column('creatives', 'skip_after_sec')
    op.drop_column('creatives', 'video_duration_sec')
    op.drop_column('creatives', 'video_url')
