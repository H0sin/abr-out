"""Add listings.broken_notify_count.

Revision ID: 0014_listing_broken_notify_count
Revises: 0013_add_plisio_gateway
Create Date: 2026-05-04 13:00:00.000000

Tracks how many buyer-facing "outbound is down" notifications we've
already sent for the current outage of each listing. ``listing_quality_gate``
caps the count so a seller repeatedly hitting "retry test" doesn't spam
buyer chats with the same warning. Reset to 0 on recovery.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014_listing_broken_notify_count"
down_revision = "0013_add_plisio_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column(
            "broken_notify_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("listings", "broken_notify_count")
