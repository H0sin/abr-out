"""withdrawal_requests + auto_withdrawal_configs

Revision ID: 0005_withdrawals
Revises: 0004_usage_tables
Create Date: 2026-05-01 19:00:00.000000

Adds the user-facing USDT-BSC withdrawal pipeline:

  * ``withdrawal_requests`` — one row per manual or auto-triggered payout.
  * ``auto_withdrawal_configs`` — per-user auto-withdraw rule (one row per user).

Idempotent so a partially-applied previous attempt can be retried.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005_withdrawals"
down_revision = "0004_usage_tables"
branch_labels = None
depends_on = None


_WITHDRAWAL_STATUS = (
    "pending",
    "submitting",
    "submitted",
    "confirmed",
    "failed",
    "refunded",
)
_WITHDRAWAL_SOURCE = ("manual", "auto")
_AUTO_MODE = ("time", "threshold")
_AUTO_AMOUNT_POLICY = ("full", "fixed")


def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    withdrawal_status = sa.Enum(*_WITHDRAWAL_STATUS, name="withdrawal_status")
    withdrawal_source = sa.Enum(*_WITHDRAWAL_SOURCE, name="withdrawal_source")
    auto_mode = sa.Enum(*_AUTO_MODE, name="auto_withdraw_mode")
    auto_policy = sa.Enum(*_AUTO_AMOUNT_POLICY, name="auto_withdraw_amount_policy")

    withdrawal_status.create(bind, checkfirst=True)
    withdrawal_source.create(bind, checkfirst=True)
    auto_mode.create(bind, checkfirst=True)
    auto_policy.create(bind, checkfirst=True)

    if not _has_table(insp, "withdrawal_requests"):
        op.create_table(
            "withdrawal_requests",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.BigInteger(),
                sa.ForeignKey("users.telegram_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("amount_usd", sa.Numeric(20, 8), nullable=False),
            sa.Column("fee_usd", sa.Numeric(20, 8), nullable=False),
            sa.Column("net_usdt", sa.Numeric(20, 8), nullable=False),
            sa.Column("to_address", sa.String(64), nullable=False),
            sa.Column("chain", sa.String(16), nullable=False, server_default="BSC"),
            sa.Column("asset", sa.String(16), nullable=False, server_default="USDT"),
            sa.Column(
                "status",
                sa.Enum(*_WITHDRAWAL_STATUS, name="withdrawal_status", create_type=False),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "source",
                sa.Enum(*_WITHDRAWAL_SOURCE, name="withdrawal_source", create_type=False),
                nullable=False,
                server_default="manual",
            ),
            sa.Column("tx_hash", sa.String(80), nullable=True),
            sa.Column("error_msg", sa.Text(), nullable=True),
            sa.Column("gas_price_wei", sa.Numeric(40, 0), nullable=True),
            sa.Column("gas_used", sa.Integer(), nullable=True),
            sa.Column(
                "idempotency_key", sa.String(128), nullable=False, unique=True
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_withdrawal_requests_user_status",
            "withdrawal_requests",
            ["user_id", "status"],
        )
        op.create_index(
            "ix_withdrawal_requests_status_created",
            "withdrawal_requests",
            ["status", "created_at"],
        )

    if not _has_table(insp, "auto_withdrawal_configs"):
        op.create_table(
            "auto_withdrawal_configs",
            sa.Column(
                "user_id",
                sa.BigInteger(),
                sa.ForeignKey("users.telegram_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "mode",
                sa.Enum(*_AUTO_MODE, name="auto_withdraw_mode", create_type=False),
                nullable=False,
            ),
            sa.Column("interval_hours", sa.Integer(), nullable=True),
            sa.Column("threshold_usd", sa.Numeric(20, 8), nullable=True),
            sa.Column(
                "amount_policy",
                sa.Enum(
                    *_AUTO_AMOUNT_POLICY,
                    name="auto_withdraw_amount_policy",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("fixed_amount_usd", sa.Numeric(20, 8), nullable=True),
            sa.Column("to_address", sa.String(64), nullable=False),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "last_withdrawal_id",
                sa.BigInteger(),
                sa.ForeignKey("withdrawal_requests.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_auto_withdrawal_configs_enabled",
            "auto_withdrawal_configs",
            ["enabled"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    if _has_table(insp, "auto_withdrawal_configs"):
        op.drop_index(
            "ix_auto_withdrawal_configs_enabled",
            table_name="auto_withdrawal_configs",
        )
        op.drop_table("auto_withdrawal_configs")
    if _has_table(insp, "withdrawal_requests"):
        op.drop_index(
            "ix_withdrawal_requests_status_created",
            table_name="withdrawal_requests",
        )
        op.drop_index(
            "ix_withdrawal_requests_user_status",
            table_name="withdrawal_requests",
        )
        op.drop_table("withdrawal_requests")

    sa.Enum(name="auto_withdraw_amount_policy").drop(bind, checkfirst=True)
    sa.Enum(name="auto_withdraw_mode").drop(bind, checkfirst=True)
    sa.Enum(name="withdrawal_source").drop(bind, checkfirst=True)
    sa.Enum(name="withdrawal_status").drop(bind, checkfirst=True)
