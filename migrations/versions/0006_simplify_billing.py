"""Add config_usage.seller_credit_usd

Revision ID: 0006_simplify_billing
Revises: 0005_withdrawals
Create Date: 2026-05-01 20:00:00.000000

Part of the billing simplification: the poller now records the seller's
credit per buyer-config (instead of a separate per-inbound aggregate row)
and no longer emits ``commission`` wallet rows. This migration just adds
the new audit column; the legacy ``OutboundUsage`` and ``UsageEvent``
tables are kept for historical data.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_simplify_billing"
down_revision = "0005_withdrawals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "config_usage",
        sa.Column(
            "seller_credit_usd",
            sa.Numeric(20, 8),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("config_usage", "seller_credit_usd")
