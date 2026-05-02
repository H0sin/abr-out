"""Listing dynamic health: ``broken`` status + sample timestamps.

Extends the ``listing_status`` enum with a new ``broken`` value used by
the worker to demote unresponsive ``active`` listings (and recover them
once they pass two consecutive ok-pings). Also adds three timestamp
columns to ``listings``:

- ``last_probed_at``  – last sample arrival, used to throttle re-probes
  of broken listings to ~10 minutes.
- ``last_ok_ping_at`` – last ``ok=true`` sample, drives the
  active->broken demotion threshold.
- ``broken_since``    – when the row was demoted; lower bound for the
  recovery consecutive-ok check.

Revision ID: 0009_listing_broken_state
Revises: 0008_listing_quality_gate
Create Date: 2026-05-02 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_listing_broken_state"
down_revision = "0008_listing_quality_gate"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, name: str) -> bool:
    return name in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Postgres requires ``ALTER TYPE ... ADD VALUE`` to run outside a
    # transaction block. Use the autocommit context so the migration is
    # safe under both online and offline runs.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE listing_status ADD VALUE IF NOT EXISTS 'broken'")

    if not _has_column(inspector, "listings", "last_probed_at"):
        op.add_column(
            "listings",
            sa.Column("last_probed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "listings", "last_ok_ping_at"):
        op.add_column(
            "listings",
            sa.Column("last_ok_ping_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "listings", "broken_since"):
        op.add_column(
            "listings",
            sa.Column("broken_since", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "listings", "broken_since"):
        op.drop_column("listings", "broken_since")
    if _has_column(inspector, "listings", "last_ok_ping_at"):
        op.drop_column("listings", "last_ok_ping_at")
    if _has_column(inspector, "listings", "last_probed_at"):
        op.drop_column("listings", "last_probed_at")
    # Note: Postgres has no clean ``DROP VALUE`` for an enum. Leaving the
    # ``broken`` value in place is harmless once the application code stops
    # using it; rolling back the schema therefore does not remove it.
