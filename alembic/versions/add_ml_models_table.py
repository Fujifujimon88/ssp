"""Add ml_model_versions table

Revision ID: d1e2f3a4b5c6
Revises: c1d2e3f4a5b6
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = "c1d2e3f4a5b6"


def upgrade():
    op.create_table(
        "ml_model_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("version", sa.String(32), nullable=False, unique=True),
        sa.Column("model_type", sa.String(32), nullable=False, default="two_tower"),
        sa.Column("train_auc", sa.Float),
        sa.Column("val_auc", sa.Float),
        sa.Column("offline_ctr_lift", sa.Float),
        sa.Column("tflite_size_mb", sa.Float),
        sa.Column("tflite_path", sa.Text),
        sa.Column("is_active", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )
    # Add cohort_id to device_profiles (ML-03)
    op.add_column("device_profiles",
        sa.Column("cohort_id", sa.Integer, nullable=True))
    op.add_column("device_profiles",
        sa.Column("cohort_label", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("device_profiles", "cohort_label")
    op.drop_column("device_profiles", "cohort_id")
    op.drop_table("ml_model_versions")
