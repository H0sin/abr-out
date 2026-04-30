"""Per-cycle traffic polling and billing.

Skeleton — full implementation in next phase. The plan:
  1. for each active inbound, fetch client traffics from 3x-ui
  2. for each known config, compute delta_bytes vs last_traffic_bytes
  3. insert one usage_event + 3 wallet_transactions (buyer/seller/admin)
     all in a single DB transaction with idempotency keys.
"""
from __future__ import annotations

from app.common.logging import logger


async def poll_traffic_once() -> None:
    logger.debug("poll_traffic: noop (skeleton)")
