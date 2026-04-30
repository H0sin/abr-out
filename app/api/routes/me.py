from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import current_user
from app.common.db.models import TxnType, User, WalletTransaction
from app.common.db.session import SessionLocal
from app.common.db.wallet import get_balance
from app.common.settings import get_settings

router = APIRouter(prefix="/api/me", tags=["me"])


class MeOut(BaseModel):
    telegram_id: int
    username: str | None
    role: str
    balance_usd: Decimal
    is_admin: bool
    is_blocked: bool


class TransactionOut(BaseModel):
    id: int
    type: str
    amount: Decimal
    currency: str
    ref: str | None
    note: str | None
    created_at: datetime


class TransactionsPage(BaseModel):
    items: list[TransactionOut]
    total: int
    page: int
    size: int


_VALID_TYPES = {t.value for t in TxnType}


def _parse_types(raw: str | None) -> list[TxnType] | None:
    if not raw:
        return None
    out: list[TxnType] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if piece and piece in _VALID_TYPES:
            out.append(TxnType(piece))
    return out or None


@router.get("", response_model=MeOut)
async def me(user: User = Depends(current_user)) -> MeOut:
    async with SessionLocal() as session:
        balance = await get_balance(session, user.telegram_id)
    return MeOut(
        telegram_id=user.telegram_id,
        username=user.username,
        role=user.role.value,
        balance_usd=balance,
        is_admin=user.telegram_id in get_settings().admin_ids,
        is_blocked=user.is_blocked,
    )


@router.get("/transactions", response_model=TransactionsPage)
async def list_my_transactions(
    user: User = Depends(current_user),
    type: str | None = Query(default=None, description="CSV of TxnType"),
    direction: Literal["all", "credit", "debit"] = "all",
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> TransactionsPage:
    types = _parse_types(type)

    filters = [WalletTransaction.user_id == user.telegram_id]
    if types:
        filters.append(WalletTransaction.type.in_(types))
    if direction == "credit":
        filters.append(WalletTransaction.amount > 0)
    elif direction == "debit":
        filters.append(WalletTransaction.amount < 0)
    if date_from is not None:
        filters.append(WalletTransaction.created_at >= date_from)
    if date_to is not None:
        filters.append(WalletTransaction.created_at <= date_to)

    async with SessionLocal() as session:
        total = (
            await session.execute(
                select(func.count())
                .select_from(WalletTransaction)
                .where(*filters)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(WalletTransaction)
                .where(*filters)
                .order_by(
                    WalletTransaction.created_at.desc(),
                    WalletTransaction.id.desc(),
                )
                .limit(size)
                .offset((page - 1) * size)
            )
        ).scalars().all()

    return TransactionsPage(
        items=[
            TransactionOut(
                id=r.id,
                type=r.type.value,
                amount=r.amount,
                currency=r.currency,
                ref=r.ref,
                note=r.note,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=int(total),
        page=page,
        size=size,
    )
