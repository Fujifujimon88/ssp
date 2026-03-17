"""add store_ad_assignments + dealer agency_id/store_number

Revision ID: e1f2a3b4c5d6
Revises: d5e6f7a8b9c0
Create Date: 2026-03-18 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str]] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # agencies テーブルが存在しない場合は作成
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS agencies (
            id SERIAL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            api_key VARCHAR(64) NOT NULL UNIQUE,
            contact_email VARCHAR(256),
            take_rate FLOAT NOT NULL DEFAULT 0.175,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """))

    # dealers に agency_id カラムを追加（存在しない場合のみ）
    conn.execute(sa.text("""
        ALTER TABLE dealers
        ADD COLUMN IF NOT EXISTS agency_id INTEGER REFERENCES agencies(id)
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_dealers_agency_id ON dealers (agency_id)
    """))

    # dealers に store_number カラムを追加（存在しない場合のみ）
    conn.execute(sa.text("""
        ALTER TABLE dealers
        ADD COLUMN IF NOT EXISTS store_number INTEGER
    """))

    # store_ad_assignments テーブルを新規作成
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS store_ad_assignments (
            id VARCHAR(36) PRIMARY KEY,
            dealer_id VARCHAR(36) NOT NULL REFERENCES dealers(id),
            campaign_id VARCHAR(36) NOT NULL REFERENCES affiliate_campaigns(id),
            priority INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_store_ad_assignments_dealer_id
        ON store_ad_assignments (dealer_id)
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_store_ad_assignments_campaign_id
        ON store_ad_assignments (campaign_id)
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP TABLE IF EXISTS store_ad_assignments"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_dealers_agency_id"))
    conn.execute(sa.text("ALTER TABLE dealers DROP COLUMN IF EXISTS agency_id"))
    conn.execute(sa.text("ALTER TABLE dealers DROP COLUMN IF EXISTS store_number"))
