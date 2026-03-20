"""affiliate_campaigns に click_url_template を追加

smaad / A8.net 等のクリックURLテンプレート。
JANet は janet_media_id + janet_original_id で固定フォーマット生成するが、
smaad / A8.net はURLテンプレートで device_id を埋め込む。

  例: https://tr.smaad.net/redirect?zo=745468462&ad=198337123&uid={device_id}
  例: https://px.a8.net/a8fly/earnings?a8mat=XXX&uid={device_id}

Revision ID: e3f4a5b6c7d8
Revises: c7d8e9f0a1b2
Create Date: 2026-03-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("affiliate_campaigns", sa.Column("click_url_template", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("affiliate_campaigns", "click_url_template")
