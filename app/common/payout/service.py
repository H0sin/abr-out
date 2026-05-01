"""Shared business logic for creating a withdrawal request.

Used by both the manual API endpoint and the auto-withdraw worker job. The
function performs all validation, balance re-check, fee quoting and ledger
writes inside a single async session — caller is responsible for committing.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.models import (
    User,
    WithdrawalRequest,
    WithdrawalSource,
    WithdrawalStatus,
)
from app.common.db.wallet import debit_for_withdrawal, get_balance
from app.common.payout.bsc import (
    BscPayoutClient,
    PayoutAddressError,
    PayoutConfigError,
    get_payout_client,
)
from app.common.settings import get_settings

_NON_TERMINAL = (
    WithdrawalStatus.pending,
    WithdrawalStatus.submitting,
    WithdrawalStatus.submitted,
)


class WithdrawalError(ValueError):
    """User-facing validation error for withdrawal creation."""


@dataclass(frozen=True)
class WithdrawalQuote:
    amount_usd: Decimal
    fee_usd: Decimal
    net_usdt: Decimal
    gas_price_wei: int


async def quote_withdrawal(
    amount_usd: Decimal, *, client: BscPayoutClient | None = None
) -> WithdrawalQuote:
    """Return a (live) fee quote for a hypothetical withdrawal of
    ``amount_usd``. Does not touch the DB. ``net_usdt`` may be negative if
    the fee exceeds the amount — caller decides how to surface that."""
    c = client or get_payout_client()
    fee_usd, gas_price_wei = await c.estimate_fee_usd()
    net = (amount_usd - fee_usd).quantize(Decimal("0.00000001"))
    return WithdrawalQuote(
        amount_usd=amount_usd,
        fee_usd=fee_usd,
        net_usdt=net,
        gas_price_wei=gas_price_wei,
    )


async def create_withdrawal(
    session: AsyncSession,
    *,
    user_id: int,
    amount_usd: Decimal,
    to_address: str,
    source: WithdrawalSource = WithdrawalSource.manual,
    client: BscPayoutClient | None = None,
) -> WithdrawalRequest:
    """Create a ``WithdrawalRequest`` + matching payout debit row.

    Validates: address checksum, min amount, no other non-terminal
    withdrawal in flight, balance sufficiency. Raises :class:`WithdrawalError`
    on any precondition failure. Caller commits.
    """
    settings = get_settings()
    c = client or get_payout_client()

    try:
        checksum_addr = c.is_valid_address(to_address)
    except PayoutAddressError as e:
        raise WithdrawalError(f"آدرس مقصد نامعتبر است: {e}") from e
    except PayoutConfigError as e:
        raise WithdrawalError(f"سرویس برداشت در دسترس نیست: {e}") from e

    if amount_usd <= 0:
        raise WithdrawalError("مبلغ باید بزرگ‌تر از صفر باشد.")
    if amount_usd < settings.withdrawal_min_usd:
        raise WithdrawalError(
            f"حداقل مبلغ برداشت {settings.withdrawal_min_usd}$ است."
        )

    # User must exist + not be blocked.
    user = await session.get(User, user_id)
    if user is None:
        raise WithdrawalError("کاربر یافت نشد.")
    if user.is_blocked:
        raise WithdrawalError("حساب شما مسدود است.")

    # Prevent stacking concurrent withdrawals.
    existing = await session.execute(
        select(WithdrawalRequest.id)
        .where(
            WithdrawalRequest.user_id == user_id,
            WithdrawalRequest.status.in_(_NON_TERMINAL),
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise WithdrawalError(
            "یک درخواست برداشت در حال پردازش دارید؛ تا اتمام آن منتظر بمانید."
        )

    balance = await get_balance(session, user_id)
    if balance < amount_usd:
        raise WithdrawalError(
            f"موجودی ناکافی است (موجودی فعلی: {balance:.4f}$)."
        )

    quote = await quote_withdrawal(amount_usd, client=c)
    if quote.net_usdt <= 0:
        raise WithdrawalError(
            f"کارمزد شبکه ({quote.fee_usd}$) از مبلغ برداشت بیشتر است."
        )

    idem = f"withdraw-debit-{secrets.token_hex(12)}"
    wr = WithdrawalRequest(
        user_id=user_id,
        amount_usd=amount_usd,
        fee_usd=quote.fee_usd,
        net_usdt=quote.net_usdt,
        to_address=checksum_addr,
        chain="BSC",
        asset="USDT",
        status=WithdrawalStatus.pending,
        source=source,
        gas_price_wei=Decimal(quote.gas_price_wei),
        idempotency_key=idem,
    )
    session.add(wr)
    # Flush so we have an id for the ledger ref.
    await session.flush()
    debit_for_withdrawal(session, wr)
    return wr
