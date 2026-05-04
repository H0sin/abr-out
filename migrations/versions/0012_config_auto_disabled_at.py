"""Add configs.auto_disabled_at to mark balance-driven auto disables.

Revision ID: 0012_config_auto_disabled_at
Revises: 0011_listing_recovery_grace
Create Date: 2026-05-04 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0012_config_auto_disabled_at"
down_revision = "0011_listing_recovery_grace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "configs",
        sa.Column("auto_disabled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("configs", "auto_disabled_at")
