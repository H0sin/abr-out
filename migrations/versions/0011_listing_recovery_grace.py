"""Add recovered_at timestamp for post-recovery marketplace grace.

Revision ID: 0011_listing_recovery_grace
Revises: 0010_backfill_listing_health
Create Date: 2026-05-04 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_listing_recovery_grace"
down_revision = "0010_backfill_listing_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("listings", "recovered_at")
