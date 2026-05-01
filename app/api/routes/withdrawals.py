"""Withdrawal API: live fee quote, request creation, list/get, and
configuration of the per-user auto-withdraw rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.common.db.models import (
    AutoWithdrawalConfig,
    AutoWithdrawAmountPolicy,
    AutoWithdrawMode,
    User,
    WithdrawalRequest,
    WithdrawalSource,
)
from app.common.db.session import get_session
from app.common.logging import logger
from app.common.payout.bsc import (
    PayoutAddressError,
    PayoutConfigError,
    get_payout_client,
)
from app.common.payout.service import (
    WithdrawalError,
    create_withdrawal,
    quote_withdrawal,
)
from app.common.settings import get_settings

router = APIRouter(prefix="/api/withdrawals", tags=["withdrawals"])


# ---------- schemas ----------


class QuoteOut(BaseModel):
    amount_usd: Decimal
    fee_usd: Decimal
    net_usdt: Decimal
    gas_price_wei: int


class WithdrawalIn(BaseModel):
    amount_usd: Decimal = Field(gt=0)
    to_address: str


class WithdrawalOut(BaseModel):
    id: int
    user_id: int
    amount_usd: Decimal
    fee_usd: Decimal
    net_usdt: Decimal
    to_address: str
    chain: str
    asset: str
    status: str
    source: str
    tx_hash: str | None
    error_msg: str | None
    created_at: datetime
    updated_at: datetime


class WithdrawalsPage(BaseModel):
    items: list[WithdrawalOut]
    total: int
    page: int
    size: int


class AutoWithdrawIn(BaseModel):
    enabled: bool
    mode: Literal["time", "threshold"]
    interval_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    threshold_usd: Decimal | None = None
    amount_policy: Literal["full", "fixed"]
    fixed_amount_usd: Decimal | None = None
    to_address: str


class AutoWithdrawOut(BaseModel):
    enabled: bool
    mode: str
    interval_hours: int | None
    threshold_usd: Decimal | None
    amount_policy: str
    fixed_amount_usd: Decimal | None
    to_address: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_withdrawal_id: int | None


def _serialise(w: WithdrawalRequest) -> WithdrawalOut:
    return WithdrawalOut(
        id=w.id,
        user_id=w.user_id,
        amount_usd=w.amount_usd,
        fee_usd=w.fee_usd,
        net_usdt=w.net_usdt,
        to_address=w.to_address,
        chain=w.chain,
        asset=w.asset,
        status=w.status.value,
        source=w.source.value,
        tx_hash=w.tx_hash,
        error_msg=w.error_msg,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


def _serialise_auto(c: AutoWithdrawalConfig) -> AutoWithdrawOut:
    return AutoWithdrawOut(
        enabled=c.enabled,
        mode=c.mode.value,
        interval_hours=c.interval_hours,
        threshold_usd=c.threshold_usd,
        amount_policy=c.amount_policy.value,
        fixed_amount_usd=c.fixed_amount_usd,
        to_address=c.to_address,
        next_run_at=c.next_run_at,
        last_run_at=c.last_run_at,
        last_withdrawal_id=c.last_withdrawal_id,
    )


# ---------- endpoints: manual ----------


@router.get("/quote", response_model=QuoteOut)
async def get_quote(
    amount_usd: Decimal = Query(gt=0),
    user: User = Depends(current_user),
) -> QuoteOut:
    try:
        q = await quote_withdrawal(amount_usd)
    except PayoutConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return QuoteOut(
        amount_usd=q.amount_usd,
        fee_usd=q.fee_usd,
        net_usdt=q.net_usdt,
        gas_price_wei=q.gas_price_wei,
    )


@router.post("", response_model=WithdrawalOut)
async def post_withdrawal(
    body: WithdrawalIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WithdrawalOut:
    try:
        wr = await create_withdrawal(
            session,
            user_id=user.telegram_id,
            amount_usd=body.amount_usd,
            to_address=body.to_address,
            source=WithdrawalSource.manual,
        )
    except WithdrawalError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PayoutConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    await session.commit()
    await session.refresh(wr)
    logger.info(
        "withdrawal #{} created: user={} amount={} fee={} net={}",
        wr.id, wr.user_id, wr.amount_usd, wr.fee_usd, wr.net_usdt,
    )
    return _serialise(wr)


@router.get("", response_model=WithdrawalsPage)
async def list_withdrawals(
    user: User = Depends(current_user),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> WithdrawalsPage:
    base = select(WithdrawalRequest).where(WithdrawalRequest.user_id == user.telegram_id)
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery())
        )
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(
                WithdrawalRequest.created_at.desc(), WithdrawalRequest.id.desc()
            )
            .limit(size)
            .offset((page - 1) * size)
        )
    ).scalars().all()
    return WithdrawalsPage(
        items=[_serialise(r) for r in rows],
        total=int(total),
        page=page,
        size=size,
    )


@router.get("/{wid}", response_model=WithdrawalOut)
async def get_withdrawal(
    wid: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> WithdrawalOut:
    w = await session.get(WithdrawalRequest, wid)
    if w is None or w.user_id != user.telegram_id:
        raise HTTPException(status_code=404, detail="not found")
    return _serialise(w)


# ---------- endpoints: auto-withdraw ----------


@router.get("/auto/config", response_model=AutoWithdrawOut | None)
async def get_auto_config(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AutoWithdrawOut | None:
    c = await session.get(AutoWithdrawalConfig, user.telegram_id)
    if c is None:
        return None
    return _serialise_auto(c)


@router.put("/auto/config", response_model=AutoWithdrawOut)
async def put_auto_config(
    body: AutoWithdrawIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AutoWithdrawOut:
    settings = get_settings()
    # Validate per-mode requirements.
    mode = AutoWithdrawMode(body.mode)
    policy = AutoWithdrawAmountPolicy(body.amount_policy)
    if mode is AutoWithdrawMode.time:
        if not body.interval_hours or body.interval_hours < 1:
            raise HTTPException(400, "interval_hours لازم است (≥ 1).")
        threshold = None
        interval = int(body.interval_hours)
    else:
        if body.threshold_usd is None or body.threshold_usd < settings.withdrawal_min_usd:
            raise HTTPException(
                400,
                f"threshold_usd باید ≥ {settings.withdrawal_min_usd} باشد.",
            )
        threshold = body.threshold_usd
        interval = None

    if policy is AutoWithdrawAmountPolicy.fixed:
        if (
            body.fixed_amount_usd is None
            or body.fixed_amount_usd < settings.withdrawal_min_usd
        ):
            raise HTTPException(
                400,
                f"fixed_amount_usd باید ≥ {settings.withdrawal_min_usd} باشد.",
            )
        fixed_amount = body.fixed_amount_usd
    else:
        fixed_amount = None

    try:
        addr = get_payout_client().is_valid_address(body.to_address)
    except PayoutAddressError as e:
        raise HTTPException(400, f"آدرس مقصد نامعتبر است: {e}") from e
    except PayoutConfigError as e:
        raise HTTPException(503, str(e)) from e

    next_run: datetime | None = None
    if body.enabled and mode is AutoWithdrawMode.time:
        next_run = datetime.now(timezone.utc) + timedelta(hours=interval or 0)

    values = {
        "user_id": user.telegram_id,
        "enabled": body.enabled,
        "mode": mode,
        "interval_hours": interval,
        "threshold_usd": threshold,
        "amount_policy": policy,
        "fixed_amount_usd": fixed_amount,
        "to_address": addr,
        "next_run_at": next_run,
    }
    set_values = {k: v for k, v in values.items() if k != "user_id"}
    set_values["updated_at"] = func.now()
    stmt = (
        pg_insert(AutoWithdrawalConfig)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[AutoWithdrawalConfig.user_id], set_=set_values
        )
        .returning(AutoWithdrawalConfig)
    )
    result = await session.execute(stmt)
    row = result.scalar_one()
    await session.commit()
    return _serialise_auto(row)


@router.delete("/auto/config", response_model=AutoWithdrawOut | None)
async def disable_auto_config(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AutoWithdrawOut | None:
    c = await session.get(AutoWithdrawalConfig, user.telegram_id)
    if c is None:
        return None
    c.enabled = False
    c.next_run_at = None
    await session.commit()
    return _serialise_auto(c)
