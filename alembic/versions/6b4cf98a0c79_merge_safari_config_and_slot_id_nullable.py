"""merge safari_config and slot_id_nullable

Revision ID: 6b4cf98a0c79
Revises: 8e0421d8e908, f2a3b4c5d6e7
Create Date: 2026-03-18 06:10:32.484928

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6b4cf98a0c79'
down_revision: Union[str, Sequence[str], None] = ('8e0421d8e908', 'f2a3b4c5d6e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
