"""admin features: block, transaction note, support, broadcast

Revision ID: 0001_admin_features
Revises: 0000_baseline
Create Date: 2026-05-01 00:00:00.000000

Adds:
  - users.is_blocked, users.started_at
  - wallet_transactions.note, wallet_transactions.created_by_admin_id
  - composite index on wallet_transactions(user_id, type, created_at)
  - support_messages table (+ enum support_direction)
  - broadcasts table (+ enum broadcast_status)

This migration is intentionally idempotent: a previous attempt could have
partially applied (creating an enum or adding a column) before failing, so
each step checks the current DB state before running.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0001_admin_features"
down_revision = "0000_baseline"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return any(c["name"] == column for c in insp.get_columns(table))


def _has_index(insp, table: str, name: str) -> bool:
    return any(i["name"] == name for i in insp.get_indexes(table))


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)

    # users
    if not _has_column(insp, "users", "is_blocked"):
        op.add_column(
            "users",
            sa.Column(
                "is_blocked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    if not _has_column(insp, "users", "started_at"):
        op.add_column(
            "users",
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        )

    # wallet_transactions
    if not _has_column(insp, "wallet_transactions", "note"):
        op.add_column(
            "wallet_transactions",
            sa.Column("note", sa.Text(), nullable=True),
        )
    if not _has_column(insp, "wallet_transactions", "created_by_admin_id"):
        op.add_column(
            "wallet_transactions",
            sa.Column("created_by_admin_id", sa.BigInteger(), nullable=True),
        )
    if not _has_index(
        insp, "wallet_transactions", "ix_wallet_transactions_user_type_created"
    ):
        op.create_index(
            "ix_wallet_transactions_user_type_created",
            "wallet_transactions",
            ["user_id", "type", "created_at"],
        )

    # support_messages (+ enum)
    sa.Enum("in", "out", name="support_direction").create(bind, checkfirst=True)
    support_direction = postgresql.ENUM(
        "in", "out", name="support_direction", create_type=False
    )
    insp = inspect(bind)  # refresh after enum create
    if not _has_table(insp, "support_messages"):
        op.create_table(
            "support_messages",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.BigInteger(),
                sa.ForeignKey("users.telegram_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("direction", support_direction, nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("user_message_id", sa.BigInteger(), nullable=True),
            sa.Column("replied_by_admin_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "replied_to_id",
                sa.BigInteger(),
                sa.ForeignKey("support_messages.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    insp = inspect(bind)
    if _has_table(insp, "support_messages"):
        if not _has_index(insp, "support_messages", "ix_support_messages_user_created"):
            op.create_index(
                "ix_support_messages_user_created",
                "support_messages",
                ["user_id", "created_at"],
            )
        if not _has_index(
            insp, "support_messages", "ix_support_messages_direction_created"
        ):
            op.create_index(
                "ix_support_messages_direction_created",
                "support_messages",
                ["direction", "created_at"],
            )

    # broadcasts (+ enum)
    sa.Enum(
        "queued", "running", "done", "failed", name="broadcast_status"
    ).create(bind, checkfirst=True)
    broadcast_status = postgresql.ENUM(
        "queued",
        "running",
        "done",
        "failed",
        name="broadcast_status",
        create_type=False,
    )
    insp = inspect(bind)
    if not _has_table(insp, "broadcasts"):
        op.create_table(
            "broadcasts",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("admin_id", sa.BigInteger(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("audience", sa.Text(), nullable=False),
            sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sent", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "status", broadcast_status, nullable=False, server_default="queued"
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("broadcasts")
    sa.Enum(name="broadcast_status").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_support_messages_direction_created", table_name="support_messages")
    op.drop_index("ix_support_messages_user_created", table_name="support_messages")
    op.drop_table("support_messages")
    sa.Enum(name="support_direction").drop(op.get_bind(), checkfirst=True)

    op.drop_index(
        "ix_wallet_transactions_user_type_created",
        table_name="wallet_transactions",
    )
    op.drop_column("wallet_transactions", "created_by_admin_id")
    op.drop_column("wallet_transactions", "note")

    op.drop_column("users", "started_at")
    op.drop_column("users", "is_blocked")
