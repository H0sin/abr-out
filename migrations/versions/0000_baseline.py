"""baseline schema from models

Revision ID: 0000_baseline
Revises:
Create Date: 2026-04-30 00:00:00.000000

Creates the entire initial schema (users, wallet_transactions, listings,
configs, usage_events, ratings, ping_samples) from SQLAlchemy models via
``Base.metadata.create_all``. This is the first migration in the chain.
"""
from __future__ import annotations

from alembic import op

from app.common.db.models import Base

revision = "0000_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
