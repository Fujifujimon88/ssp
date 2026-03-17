"""merge migration branches (agency chain + device_profiles chain)

Revision ID: d5e6f7a8b9c0
Revises: f3a4b5c6d7e8, c4d5e6f7a8b9
Create Date: 2026-03-17 14:00:00.000000

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str]] = ('f3a4b5c6d7e8', 'c4d5e6f7a8b9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
