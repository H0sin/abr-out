from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from .models import WalletTransaction


async def get_balance(
    session: AsyncSession, user_id: int, currency: str = "USD"
) -> Decimal:
    """Compute current wallet balance from the transactions ledger."""
    result = await session.execute(
        select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(
            WalletTransaction.user_id == user_id,
            WalletTransaction.currency == currency,
        )
    )
    return Decimal(result.scalar_one())
