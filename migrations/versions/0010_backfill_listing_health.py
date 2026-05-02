"""Backfill listing health timestamps from PingSample.

The 0009 migration added ``last_ok_ping_at`` / ``last_probed_at`` /
``broken_since`` as NULL columns. Combined with the new demote pass in
``listing_quality_gate``, that left every pre-existing ``active`` row
with ``last_ok_ping_at IS NULL`` and at least one historical PingSample
since ``created_at`` — which the demote rule treats as "stopped
responding" and flips to ``broken``. Two healthy listings disappeared
from Browse the first time the worker ran after deploy.

This migration is purely curative:

1. ``UPDATE listings SET last_probed_at = MAX(sampled_at)``
2. ``UPDATE listings SET last_ok_ping_at = MAX(sampled_at WHERE ok)``
3. Any ``broken`` listing whose backfilled ``last_ok_ping_at`` is recent
   enough (within ``listing_broken_after_minutes`` of NOW) is flipped
   back to ``active`` and ``broken_since`` cleared. We use the
   conservative default of 10 minutes here; environments that override
   ``LISTING_BROKEN_AFTER_MINUTES`` will reconverge on the next
   quality-gate tick anyway.

Revision ID: 0010_backfill_listing_health
Revises: 0009_listing_broken_state
Create Date: 2026-05-02 00:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = "0010_backfill_listing_health"
down_revision = "0009_listing_broken_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill last_probed_at from any PingSample row.
    op.execute(
        """
        UPDATE listings AS l
           SET last_probed_at = sub.max_ts
          FROM (
            SELECT listing_id, MAX(sampled_at) AS max_ts
              FROM ping_samples
             GROUP BY listing_id
          ) AS sub
         WHERE sub.listing_id = l.id
           AND l.last_probed_at IS NULL
        """
    )
    # Backfill last_ok_ping_at from ok=true PingSamples only.
    op.execute(
        """
        UPDATE listings AS l
           SET last_ok_ping_at = sub.max_ts
          FROM (
            SELECT listing_id, MAX(sampled_at) AS max_ts
              FROM ping_samples
             WHERE ok = true
             GROUP BY listing_id
          ) AS sub
         WHERE sub.listing_id = l.id
           AND l.last_ok_ping_at IS NULL
        """
    )
    # Reverse any broken→active demotion that was caused by the missing
    # backfill. A row is considered healthy if it has an ok sample within
    # the last 10 minutes after the backfill above.
    op.execute(
        """
        UPDATE listings
           SET status = 'active',
               broken_since = NULL
         WHERE status = 'broken'
           AND last_ok_ping_at IS NOT NULL
           AND last_ok_ping_at >= NOW() - INTERVAL '10 minutes'
        """
    )


def downgrade() -> None:
    # The backfill is idempotent and harmless to keep on a downgrade.
    pass
