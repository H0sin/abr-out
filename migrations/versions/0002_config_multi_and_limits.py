"""configs: allow multiple per (listing, buyer) and add name/expiry/limit

Revision ID: 0002_config_multi_and_limits
Revises: 0001_admin_features
Create Date: 2026-05-01 00:00:00.000000

Changes to ``configs`` table:
  - drop unique constraint (listing_id, buyer_user_id) — buyers may now
    create multiple configs against the same listing
  - add column ``name`` (varchar 64, NOT NULL) — buyer-chosen label
  - add column ``expiry_at`` (timestamptz, nullable) — NULL = unlimited
  - add column ``total_gb_limit`` (numeric, nullable) — NULL = unlimited
  - add helper index on (listing_id, buyer_user_id) (no longer unique)

Idempotent so that a partially applied previous attempt can be retried.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_config_multi_and_limits"
down_revision = "0001_admin_features"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_index(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def _has_unique(insp, table: str, name: str) -> bool:
    return any(uc["name"] == name for uc in insp.get_unique_constraints(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    # 1. Drop unique constraint if present.
    if _has_unique(insp, "configs", "uq_configs_listing_buyer"):
        op.drop_constraint(
            "uq_configs_listing_buyer", "configs", type_="unique"
        )

    # 2. Add columns (nullable first so backfill works).
    if not _has_column(insp, "configs", "name"):
        op.add_column(
            "configs",
            sa.Column("name", sa.String(length=64), nullable=True),
        )
        # Backfill existing rows: copy panel_client_email.
        op.execute(
            "UPDATE configs SET name = panel_client_email WHERE name IS NULL"
        )
        op.alter_column("configs", "name", nullable=False)

    if not _has_column(insp, "configs", "expiry_at"):
        op.add_column(
            "configs",
            sa.Column("expiry_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _has_column(insp, "configs", "total_gb_limit"):
        op.add_column(
            "configs",
            sa.Column("total_gb_limit", sa.Numeric(12, 4), nullable=True),
        )

    # 3. Helper non-unique index.
    insp = inspect(bind)
    if not _has_index(insp, "configs", "ix_configs_listing_buyer"):
        op.create_index(
            "ix_configs_listing_buyer",
            "configs",
            ["listing_id", "buyer_user_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if _has_index(insp, "configs", "ix_configs_listing_buyer"):
        op.drop_index("ix_configs_listing_buyer", table_name="configs")

    if _has_column(insp, "configs", "total_gb_limit"):
        op.drop_column("configs", "total_gb_limit")
    if _has_column(insp, "configs", "expiry_at"):
        op.drop_column("configs", "expiry_at")
    if _has_column(insp, "configs", "name"):
        op.drop_column("configs", "name")

    insp = inspect(bind)
    if not _has_unique(insp, "configs", "uq_configs_listing_buyer"):
        op.create_unique_constraint(
            "uq_configs_listing_buyer",
            "configs",
            ["listing_id", "buyer_user_id"],
        )
