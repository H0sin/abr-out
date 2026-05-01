"""drop swapwallet integration

Revision ID: 0003_drop_swapwallet
Revises: 0002_config_multi_and_limits
Create Date: 2026-05-01 12:00:00.000000

Removes the SwapWallet payment gateway:
  - drop ``swapwallet_transactions`` table (and its indexes)
  - drop ``swapwallet_tx_status`` enum type
  - remove the ``swapwallet`` value from the ``payment_gateway`` enum

Idempotent so a partially-applied previous attempt can be retried.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003_drop_swapwallet"
down_revision = "0002_config_multi_and_limits"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def _enum_values(bind, name: str) -> list[str]:
    rows = bind.execute(
        sa.text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = :n "
            "ORDER BY e.enumsortorder"
        ),
        {"n": name},
    ).fetchall()
    return [r[0] for r in rows]


def _enum_exists(bind, name: str) -> bool:
    row = bind.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}
    ).fetchone()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    # 1. Drop swapwallet_transactions table.
    if _has_table(insp, "swapwallet_transactions"):
        # Drop indexes first (defensive; drop_table also drops them).
        for ix in ("ix_swapwallet_tx_user", "ix_swapwallet_tx_created_at"):
            try:
                op.drop_index(ix, table_name="swapwallet_transactions")
            except Exception:
                pass
        op.drop_table("swapwallet_transactions")

    # 2. Drop swapwallet_tx_status enum type if no longer referenced.
    if _enum_exists(bind, "swapwallet_tx_status"):
        op.execute("DROP TYPE swapwallet_tx_status")

    # 3. Remove 'swapwallet' from payment_gateway enum.
    #    Postgres can't drop an enum value directly: rename old, create new,
    #    cast columns, drop old.
    if _enum_exists(bind, "payment_gateway"):
        values = _enum_values(bind, "payment_gateway")
        if "swapwallet" in values:
            new_values = [v for v in values if v != "swapwallet"]
            new_values_sql = ", ".join(f"'{v}'" for v in new_values)
            op.execute("ALTER TYPE payment_gateway RENAME TO payment_gateway_old")
            op.execute(f"CREATE TYPE payment_gateway AS ENUM ({new_values_sql})")
            # Defensive: any rows still using 'swapwallet' (shouldn't exist
            # in normal flow, but be safe) are deleted before the cast.
            op.execute(
                "DELETE FROM payment_intents WHERE gateway::text = 'swapwallet'"
            )
            op.execute(
                "ALTER TABLE payment_intents "
                "ALTER COLUMN gateway TYPE payment_gateway "
                "USING gateway::text::payment_gateway"
            )
            op.execute("DROP TYPE payment_gateway_old")


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Restore 'swapwallet' value on payment_gateway enum.
    if _enum_exists(bind, "payment_gateway"):
        values = _enum_values(bind, "payment_gateway")
        if "swapwallet" not in values:
            op.execute("ALTER TYPE payment_gateway ADD VALUE 'swapwallet'")

    # 2. Recreate swapwallet_tx_status enum.
    if not _enum_exists(bind, "swapwallet_tx_status"):
        op.execute(
            "CREATE TYPE swapwallet_tx_status AS ENUM "
            "('pending', 'paid', 'cancelled', 'failed')"
        )

    # 3. Recreate swapwallet_transactions table.
    insp = inspect(bind)
    if not _has_table(insp, "swapwallet_transactions"):
        op.create_table(
            "swapwallet_transactions",
            sa.Column("order_id", sa.String(length=64), primary_key=True),
            sa.Column(
                "user_id",
                sa.BigInteger(),
                sa.ForeignKey("users.telegram_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("amount_usd", sa.Numeric(20, 8), nullable=False),
            sa.Column("amount_irt", sa.BigInteger(), nullable=False),
            sa.Column("invoice_id", sa.String(length=128), nullable=True),
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
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_swapwallet_tx_user", "swapwallet_transactions", ["user_id"]
        )
        op.create_index(
            "ix_swapwallet_tx_created_at",
            "swapwallet_transactions",
            ["created_at"],
        )
