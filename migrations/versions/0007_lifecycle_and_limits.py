"""Listing/Config lifecycle: add 'deleted' enum value, disabled_at/deleted_at,
and the per-config auto_disable_on_price_increase flag.

Revision ID: 0007_lifecycle_and_limits
Revises: 0006_simplify_billing
Create Date: 2026-05-01 21:00:00.000000

Backs the seller-/buyer-side disable/delete/edit flows and the buyer-opt-in
auto-disable-on-price-increase notification path. Soft-delete is preferred
over row removal so that historical ``outbound_usage``/``config_usage``
rows (which CASCADE on their parents) keep referential integrity.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_lifecycle_and_limits"
down_revision = "0006_simplify_billing"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, name: str) -> bool:
    return name in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Postgres ALTER TYPE ADD VALUE must run outside a transaction block
    # for some older PG versions; alembic's autocommit_block helps when
    # supported. Use a try/except to stay compatible with non-PG dialects
    # used in tests (e.g. SQLite).
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE listing_status ADD VALUE IF NOT EXISTS 'deleted'")
            op.execute("ALTER TYPE config_status ADD VALUE IF NOT EXISTS 'deleted'")

    if not _has_column(inspector, "listings", "disabled_at"):
        op.add_column(
            "listings",
            sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "listings", "deleted_at"):
        op.add_column(
            "listings",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "configs", "deleted_at"):
        op.add_column(
            "configs",
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "configs", "auto_disable_on_price_increase"):
        op.add_column(
            "configs",
            sa.Column(
                "auto_disable_on_price_increase",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "configs", "auto_disable_on_price_increase"):
        op.drop_column("configs", "auto_disable_on_price_increase")
    if _has_column(inspector, "configs", "deleted_at"):
        op.drop_column("configs", "deleted_at")
    if _has_column(inspector, "listings", "deleted_at"):
        op.drop_column("listings", "deleted_at")
    if _has_column(inspector, "listings", "disabled_at"):
        op.drop_column("listings", "disabled_at")
    # Note: ALTER TYPE ... DROP VALUE is not supported by Postgres; the
    # 'deleted' enum value remains. This is harmless because the schema
    # below this migration never inserts it.
