"""add swapwallet_transactions table

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-04-30 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE swapwallet_tx_status AS ENUM "
        "('pending', 'paid', 'cancelled', 'failed')"
    )
    op.create_table(
        "swapwallet_transactions",
        sa.Column("order_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.telegram_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_usd", sa.Numeric(20, 8), nullable=False),
        sa.Column("amount_irt", sa.BigInteger, nullable=False),
        sa.Column("invoice_id", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "paid", "cancelled", "failed",
                name="swapwallet_tx_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_swapwallet_tx_user", "swapwallet_transactions", ["user_id"])
    op.create_index(
        "ix_swapwallet_tx_created_at", "swapwallet_transactions", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_swapwallet_tx_created_at", table_name="swapwallet_transactions")
    op.drop_index("ix_swapwallet_tx_user", table_name="swapwallet_transactions")
    op.drop_table("swapwallet_transactions")
    op.execute("DROP TYPE swapwallet_tx_status")
