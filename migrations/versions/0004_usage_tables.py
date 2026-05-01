"""outbound_usage + config_usage tables, plus poll-cycle anchor columns

Revision ID: 0004_usage_tables
Revises: 0003_drop_swapwallet
Create Date: 2026-05-01 18:00:00.000000

Introduces the read→reset traffic-poll model:

  * ``outbound_usage`` — per-cycle traffic at the inbound (seller) level.
  * ``config_usage``   — per-cycle traffic at the client (buyer) level.

Adds anchor columns for the poller:

  * ``listings.last_outbound_up_bytes`` / ``last_outbound_down_bytes``
    track diff state for the inbound's panel-level totals (3x-ui has no
    per-inbound total reset endpoint).
  * ``configs.last_snapshot_bytes`` is a fallback diff anchor used only
    when a panel-side client reset fails; in the normal flow it stays 0
    because the poller resets client counters every cycle.

Idempotent so a partially-applied previous attempt can be retried.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision = "0004_usage_tables"
down_revision = "0003_drop_swapwallet"
branch_labels = None
depends_on = None


def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def _has_column(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    # 1. Anchor columns on listings/configs.
    if not _has_column(insp, "listings", "last_outbound_up_bytes"):
        op.add_column(
            "listings",
            sa.Column(
                "last_outbound_up_bytes",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        )
    if not _has_column(insp, "listings", "last_outbound_down_bytes"):
        op.add_column(
            "listings",
            sa.Column(
                "last_outbound_down_bytes",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        )
    if not _has_column(insp, "configs", "last_snapshot_bytes"):
        op.add_column(
            "configs",
            sa.Column(
                "last_snapshot_bytes",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        )

    # 2. outbound_usage.
    if not _has_table(insp, "outbound_usage"):
        op.create_table(
            "outbound_usage",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column(
                "listing_id",
                sa.Integer(),
                sa.ForeignKey("listings.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("seller_user_id", sa.BigInteger(), nullable=False),
            sa.Column("panel_inbound_id", sa.Integer(), nullable=False),
            sa.Column("cycle_id", PG_UUID(as_uuid=True), nullable=False),
            sa.Column("delta_up_bytes", sa.BigInteger(), nullable=False),
            sa.Column("delta_down_bytes", sa.BigInteger(), nullable=False),
            sa.Column("delta_total_bytes", sa.BigInteger(), nullable=False),
            sa.Column("gb", sa.Numeric(20, 10), nullable=False),
            sa.Column("seller_credit_usd", sa.Numeric(20, 8), nullable=False),
            sa.Column(
                "panel_total_up_bytes", sa.BigInteger(), nullable=False
            ),
            sa.Column(
                "panel_total_down_bytes", sa.BigInteger(), nullable=False
            ),
            sa.Column(
                "reset_attempted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "reset_succeeded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "sampled_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "listing_id",
                "cycle_id",
                name="uq_outbound_usage_listing_cycle",
            ),
        )
        op.create_index(
            "ix_outbound_usage_sampled_at",
            "outbound_usage",
            ["sampled_at"],
        )
        op.create_index(
            "ix_outbound_usage_listing_time",
            "outbound_usage",
            ["listing_id", "sampled_at"],
        )

    # 3. config_usage.
    insp = inspect(bind)
    if not _has_table(insp, "config_usage"):
        op.create_table(
            "config_usage",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column(
                "config_id",
                sa.Integer(),
                sa.ForeignKey("configs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("listing_id", sa.Integer(), nullable=False),
            sa.Column("buyer_user_id", sa.BigInteger(), nullable=False),
            sa.Column("seller_user_id", sa.BigInteger(), nullable=False),
            sa.Column("cycle_id", PG_UUID(as_uuid=True), nullable=False),
            sa.Column("delta_up_bytes", sa.BigInteger(), nullable=False),
            sa.Column("delta_down_bytes", sa.BigInteger(), nullable=False),
            sa.Column("delta_total_bytes", sa.BigInteger(), nullable=False),
            sa.Column("gb", sa.Numeric(20, 10), nullable=False),
            sa.Column("buyer_debit_usd", sa.Numeric(20, 8), nullable=False),
            sa.Column("panel_email", sa.String(length=128), nullable=False),
            sa.Column(
                "reset_attempted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "reset_succeeded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "sampled_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "config_id",
                "cycle_id",
                name="uq_config_usage_config_cycle",
            ),
        )
        op.create_index(
            "ix_config_usage_sampled_at",
            "config_usage",
            ["sampled_at"],
        )
        op.create_index(
            "ix_config_usage_config_time",
            "config_usage",
            ["config_id", "sampled_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if _has_table(insp, "config_usage"):
        op.drop_index("ix_config_usage_config_time", table_name="config_usage")
        op.drop_index("ix_config_usage_sampled_at", table_name="config_usage")
        op.drop_table("config_usage")

    insp = inspect(bind)
    if _has_table(insp, "outbound_usage"):
        op.drop_index(
            "ix_outbound_usage_listing_time", table_name="outbound_usage"
        )
        op.drop_index(
            "ix_outbound_usage_sampled_at", table_name="outbound_usage"
        )
        op.drop_table("outbound_usage")

    insp = inspect(bind)
    if _has_column(insp, "configs", "last_snapshot_bytes"):
        op.drop_column("configs", "last_snapshot_bytes")
    if _has_column(insp, "listings", "last_outbound_down_bytes"):
        op.drop_column("listings", "last_outbound_down_bytes")
    if _has_column(insp, "listings", "last_outbound_up_bytes"):
        op.drop_column("listings", "last_outbound_up_bytes")
