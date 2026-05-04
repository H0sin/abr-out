"""Add 'plisio' value to payment_gateway enum.

Revision ID: 0013_add_plisio_gateway
Revises: 0012_config_auto_disabled_at
Create Date: 2026-05-04 12:00:00.000000

Adds the ``plisio`` payment gateway, used as a low-amount alternative to
NowPayments (which enforces a relatively high per-coin minimum).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0013_add_plisio_gateway"
down_revision = "0012_config_auto_disabled_at"
branch_labels = None
depends_on = None


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
    if not _enum_exists(bind, "payment_gateway"):
        return
    values = _enum_values(bind, "payment_gateway")
    if "plisio" in values:
        return
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
    # older Postgres; alembic uses autocommit=False by default. Use the
    # connection's COMMIT to flush, then add. Modern Postgres (>=12)
    # supports it transactionally, which covers our deployment.
    op.execute("ALTER TYPE payment_gateway ADD VALUE 'plisio'")


def downgrade() -> None:
    bind = op.get_bind()
    if not _enum_exists(bind, "payment_gateway"):
        return
    values = _enum_values(bind, "payment_gateway")
    if "plisio" not in values:
        return
    new_values = [v for v in values if v != "plisio"]
    new_values_sql = ", ".join(f"'{v}'" for v in new_values)
    op.execute("ALTER TYPE payment_gateway RENAME TO payment_gateway_old")
    op.execute(f"CREATE TYPE payment_gateway AS ENUM ({new_values_sql})")
    # Defensive: drop any rows that still reference the value being removed.
    op.execute("DELETE FROM payment_intents WHERE gateway::text = 'plisio'")
    op.execute(
        "ALTER TABLE payment_intents "
        "ALTER COLUMN gateway TYPE payment_gateway "
        "USING gateway::text::payment_gateway"
    )
    op.execute("DROP TYPE payment_gateway_old")
