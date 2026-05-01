"""Listing quality gate + stability metric.

Adds four columns to ``listings``:

- ``stability_pct`` – percentage of successful pings over the last 24h,
  computed by the ``aggregate_pings_once`` worker job.
- ``probe_client_uuid`` / ``probe_client_email`` – identify a dedicated
  3x-ui client added at listing-creation time. The Iran-side prober uses
  this UUID to build a real VLESS-TCP tunnel through the seller's panel
  and measures end-to-end L7 latency through it (mirrors 3x-ui's own
  Outbound-test feature).
- ``pending_until_at`` – quality-gate deadline. New listings start in
  ``pending`` status; the ``listing_quality_gate`` worker promotes to
  ``active`` on the first ``PingSample.ok=true`` and hard-deletes the
  listing (panel inbound + DB row) once this timestamp passes without a
  single successful ping.

Revision ID: 0008_listing_quality_gate
Revises: 0007_lifecycle_and_limits
Create Date: 2026-05-01 22:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0008_listing_quality_gate"
down_revision = "0007_lifecycle_and_limits"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, name: str) -> bool:
    return name in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "listings", "stability_pct"):
        op.add_column(
            "listings",
            sa.Column("stability_pct", sa.Integer(), nullable=True),
        )
    if not _has_column(inspector, "listings", "probe_client_uuid"):
        op.add_column(
            "listings",
            sa.Column("probe_client_uuid", sa.String(length=64), nullable=True),
        )
    if not _has_column(inspector, "listings", "probe_client_email"):
        op.add_column(
            "listings",
            sa.Column("probe_client_email", sa.String(length=128), nullable=True),
        )
    if not _has_column(inspector, "listings", "pending_until_at"):
        op.add_column(
            "listings",
            sa.Column("pending_until_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "listings", "pending_until_at"):
        op.drop_column("listings", "pending_until_at")
    if _has_column(inspector, "listings", "probe_client_email"):
        op.drop_column("listings", "probe_client_email")
    if _has_column(inspector, "listings", "probe_client_uuid"):
        op.drop_column("listings", "probe_client_uuid")
    if _has_column(inspector, "listings", "stability_pct"):
        op.drop_column("listings", "stability_pct")
