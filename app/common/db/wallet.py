from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from .models import TxnType, WalletTransaction, WithdrawalRequest, WithdrawalStatus


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


def debit_for_withdrawal(
    session: AsyncSession, withdrawal: WithdrawalRequest
) -> WalletTransaction:
    """Stage a ``payout`` ledger row that debits the user for the gross
    withdrawal amount. Caller commits."""
    tx = WalletTransaction(
        user_id=withdrawal.user_id,
        amount=-withdrawal.amount_usd,
        currency="USD",
        type=TxnType.payout,
        ref=f"withdraw:{withdrawal.id}" if withdrawal.id else None,
        idempotency_key=withdrawal.idempotency_key,
    )
    session.add(tx)
    return tx


async def refund_failed_withdrawal(
    session: AsyncSession, withdrawal: WithdrawalRequest, *, reason: str | None = None
) -> WalletTransaction:
    """Insert a ``refund`` ledger row that credits the user back the gross
    amount, and mark the withdrawal as ``refunded``. Idempotent via the
    ``withdraw-refund-{id}`` idempotency key. Caller commits."""
    tx = WalletTransaction(
        user_id=withdrawal.user_id,
        amount=withdrawal.amount_usd,
        currency="USD",
        type=TxnType.refund,
        ref=f"withdraw:{withdrawal.id}",
        note=f"refund withdrawal #{withdrawal.id}"
        + (f": {reason}" if reason else ""),
        idempotency_key=f"withdraw-refund-{withdrawal.id}",
    )
    session.add(tx)
    withdrawal.status = WithdrawalStatus.refunded
    return tx
