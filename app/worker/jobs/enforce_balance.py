"""Disable configs for buyers whose balance reached zero, re-enable on top-up."""
from __future__ import annotations

from app.common.logging import logger


async def enforce_balances_once() -> None:
    logger.debug("enforce_buyer_balance: noop (skeleton)")
