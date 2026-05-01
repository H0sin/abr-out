from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from html import escape
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select

from app.api.deps import current_admin
from app.api.routes.me import (
    TransactionOut,
    TransactionsPage,
    _parse_types,
)
from app.common.db.models import (
    Broadcast,
    BroadcastStatus,
    Config,
    Listing,
    SupportDirection,
    SupportMessage,
    TxnType,
    User,
    WalletTransaction,
)
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.telegram_bot import send_message

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- Schemas ----------


class AdminUserOut(BaseModel):
    telegram_id: int
    username: str | None
    role: str
    is_blocked: bool
    balance_usd: Decimal
    configs_count: int
    listings_count: int
    created_at: datetime
    started_at: datetime | None


class AdminUsersPage(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    size: int


class BlockBody(BaseModel):
    blocked: bool


class TxBody(BaseModel):
    amount: Decimal
    type: Literal["adjustment", "topup", "refund", "payout"] = "adjustment"
    note: str = Field(min_length=2, max_length=500)


class DMBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Audience(BaseModel):
    kind: Literal["all", "buyers", "sellers", "date_range"] = "all"
    date_from: datetime | None = Field(default=None, alias="from")
    date_to: datetime | None = Field(default=None, alias="to")

    model_config = {"populate_by_name": True}


class BroadcastBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    audience: Audience = Audience()


class BroadcastPreviewBody(BaseModel):
    audience: Audience = Audience()


class BroadcastJobOut(BaseModel):
    id: int
    text: str
    status: str
    total: int
    sent: int
    failed: int
    created_at: datetime
    finished_at: datetime | None


class SupportEntry(BaseModel):
    id: int
    user_id: int
    username: str | None
    direction: str
    text: str
    replied_by_admin_id: int | None
    created_at: datetime


class SupportPage(BaseModel):
    items: list[SupportEntry]
    total: int
    page: int
    size: int


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# ---------- Helpers ----------


_balance_subq = (
    select(
        WalletTransaction.user_id.label("uid"),
        func.coalesce(func.sum(WalletTransaction.amount), 0).label("balance"),
    )
    .group_by(WalletTransaction.user_id)
    .subquery()
)


def _audience_filters(audience: Audience):
    """Return list of SQL filters on User for the given audience."""
    filters = [User.is_blocked.is_(False)]
    if audience.kind == "buyers":
        filters.append(
            User.telegram_id.in_(select(Config.buyer_user_id).distinct())
        )
    elif audience.kind == "sellers":
        filters.append(
            User.telegram_id.in_(select(Listing.seller_user_id).distinct())
        )
    elif audience.kind == "date_range":
        if audience.date_from is not None:
            filters.append(User.created_at >= audience.date_from)
        if audience.date_to is not None:
            filters.append(User.created_at <= audience.date_to)
    return filters


# ---------- Users ----------


@router.get("/users", response_model=AdminUsersPage)
async def list_users(
    _: User = Depends(current_admin),
    q: str | None = None,
    blocked: Literal["all", "yes", "no"] = "all",
    sort: Literal["created_at", "balance", "username", "telegram_id"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> AdminUsersPage:
    bal_subq = _balance_subq

    configs_subq = (
        select(Config.buyer_user_id.label("uid"), func.count().label("c"))
        .group_by(Config.buyer_user_id)
        .subquery()
    )
    listings_subq = (
        select(Listing.seller_user_id.label("uid"), func.count().label("c"))
        .group_by(Listing.seller_user_id)
        .subquery()
    )

    base = (
        select(
            User,
            func.coalesce(bal_subq.c.balance, 0).label("balance"),
            func.coalesce(configs_subq.c.c, 0).label("configs_count"),
            func.coalesce(listings_subq.c.c, 0).label("listings_count"),
        )
        .select_from(User)
        .join(bal_subq, bal_subq.c.uid == User.telegram_id, isouter=True)
        .join(configs_subq, configs_subq.c.uid == User.telegram_id, isouter=True)
        .join(listings_subq, listings_subq.c.uid == User.telegram_id, isouter=True)
    )

    where = []
    if q:
        like = f"%{q.strip()}%"
        # Try numeric match for telegram_id too
        ors = [User.username.ilike(like)]
        if q.strip().lstrip("-").isdigit():
            ors.append(User.telegram_id == int(q.strip()))
        where.append(or_(*ors))
    if blocked == "yes":
        where.append(User.is_blocked.is_(True))
    elif blocked == "no":
        where.append(User.is_blocked.is_(False))

    if where:
        base = base.where(*where)

    sort_col_map = {
        "created_at": User.created_at,
        "balance": func.coalesce(bal_subq.c.balance, 0),
        "username": User.username,
        "telegram_id": User.telegram_id,
    }
    sort_col = sort_col_map[sort]
    base = base.order_by(sort_col.asc() if order == "asc" else desc(sort_col))

    async with SessionLocal() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(User).where(*where)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                base.limit(size).offset((page - 1) * size)
            )
        ).all()

    items: list[AdminUserOut] = []
    for u, balance, configs_count, listings_count in rows:
        items.append(
            AdminUserOut(
                telegram_id=u.telegram_id,
                username=u.username,
                role=u.role.value,
                is_blocked=u.is_blocked,
                balance_usd=Decimal(balance),
                configs_count=int(configs_count),
                listings_count=int(listings_count),
                created_at=u.created_at,
                started_at=u.started_at,
            )
        )
    return AdminUsersPage(items=items, total=int(total), page=page, size=size)


async def _get_user_or_404(user_id: int) -> AdminUserOut:
    async with SessionLocal() as session:
        u = await session.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="user not found")
        balance = (
            await session.execute(
                select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(
                    WalletTransaction.user_id == user_id
                )
            )
        ).scalar_one()
        configs_count = (
            await session.execute(
                select(func.count()).select_from(Config).where(
                    Config.buyer_user_id == user_id
                )
            )
        ).scalar_one()
        listings_count = (
            await session.execute(
                select(func.count()).select_from(Listing).where(
                    Listing.seller_user_id == user_id
                )
            )
        ).scalar_one()
    return AdminUserOut(
        telegram_id=u.telegram_id,
        username=u.username,
        role=u.role.value,
        is_blocked=u.is_blocked,
        balance_usd=Decimal(balance),
        configs_count=int(configs_count),
        listings_count=int(listings_count),
        created_at=u.created_at,
        started_at=u.started_at,
    )


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_user(user_id: int, _: User = Depends(current_admin)) -> AdminUserOut:
    return await _get_user_or_404(user_id)


@router.post("/users/{user_id}/block", response_model=AdminUserOut)
async def set_blocked(
    user_id: int,
    body: BlockBody,
    admin: User = Depends(current_admin),
) -> AdminUserOut:
    async with SessionLocal() as session:
        u = await session.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="user not found")
        u.is_blocked = body.blocked
        await session.commit()

    try:
        await send_message(
            user_id,
            "🚫 حساب شما توسط مدیر مسدود شد."
            if body.blocked
            else "✅ حساب شما توسط مدیر فعال شد.",
        )
    except Exception:
        logger.exception("Block notify failed for {}", user_id)
    logger.info(
        "Admin {} {} user {}", admin.telegram_id, "blocked" if body.blocked else "unblocked", user_id
    )
    return await _get_user_or_404(user_id)


@router.post("/users/{user_id}/transactions", response_model=TransactionOut)
async def add_user_transaction(
    user_id: int,
    body: TxBody,
    admin: User = Depends(current_admin),
) -> TransactionOut:
    if body.amount == 0:
        raise HTTPException(status_code=400, detail="amount must be non-zero")

    async with SessionLocal() as session:
        target = await session.get(User, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        wt = WalletTransaction(
            user_id=user_id,
            amount=body.amount,
            currency="USD",
            type=TxnType(body.type),
            ref=f"admin:{admin.telegram_id}",
            note=body.note,
            created_by_admin_id=admin.telegram_id,
            idempotency_key=f"admin-{admin.telegram_id}-{uuid.uuid4()}",
        )
        session.add(wt)
        await session.commit()
        await session.refresh(wt)

    try:
        verb = "افزایش" if body.amount > 0 else "کاهش"
        await send_message(
            user_id,
            f"💼 موجودی کیف پول شما توسط مدیر {verb} یافت.\n"
            f"مبلغ: <b>{abs(body.amount)}$</b>\n"
            f"علت: {escape(body.note)}",
        )
    except Exception:
        logger.exception("Failed to notify user {} about admin tx", user_id)

    return TransactionOut(
        id=wt.id,
        type=wt.type.value,
        amount=wt.amount,
        currency=wt.currency,
        ref=wt.ref,
        note=wt.note,
        created_at=wt.created_at,
    )


@router.get("/users/{user_id}/transactions", response_model=TransactionsPage)
async def list_user_transactions(
    user_id: int,
    _: User = Depends(current_admin),
    type: str | None = None,
    direction: Literal["all", "credit", "debit"] = "all",
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> TransactionsPage:
    types = _parse_types(type)
    filters = [WalletTransaction.user_id == user_id]
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
                select(func.count()).select_from(WalletTransaction).where(*filters)
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


@router.post("/users/{user_id}/message")
async def send_user_dm(
    user_id: int,
    body: DMBody,
    admin: User = Depends(current_admin),
) -> dict[str, bool]:
    async with SessionLocal() as session:
        u = await session.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="user not found")
        sm = SupportMessage(
            user_id=user_id,
            direction=SupportDirection.out,
            text=body.text,
            replied_by_admin_id=admin.telegram_id,
        )
        session.add(sm)
        await session.commit()

    resp = await send_message(user_id, f"📩 <b>پیام مدیر:</b>\n{escape(body.text)}")
    ok = bool(resp and resp.get("ok"))
    return {"ok": ok}


# ---------- Broadcast ----------


@router.post("/broadcast/preview")
async def broadcast_preview(
    body: BroadcastPreviewBody,
    _: User = Depends(current_admin),
) -> dict[str, int]:
    filters = _audience_filters(body.audience)
    async with SessionLocal() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(User).where(*filters)
            )
        ).scalar_one()
    return {"count": int(total)}


@router.post("/broadcast", response_model=BroadcastJobOut)
async def create_broadcast(
    body: BroadcastBody,
    admin: User = Depends(current_admin),
) -> BroadcastJobOut:
    filters = _audience_filters(body.audience)
    async with SessionLocal() as session:
        total = (
            await session.execute(
                select(func.count()).select_from(User).where(*filters)
            )
        ).scalar_one()
        bc = Broadcast(
            admin_id=admin.telegram_id,
            text=body.text,
            audience=json.dumps(body.audience.model_dump(mode="json", by_alias=False)),
            total=int(total),
            sent=0,
            failed=0,
            status=BroadcastStatus.queued,
        )
        session.add(bc)
        await session.commit()
        await session.refresh(bc)
    return _broadcast_to_out(bc)


@router.get("/broadcast/{bid}", response_model=BroadcastJobOut)
async def get_broadcast(
    bid: int,
    _: User = Depends(current_admin),
) -> BroadcastJobOut:
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, bid)
    if bc is None:
        raise HTTPException(status_code=404, detail="not found")
    return _broadcast_to_out(bc)


def _broadcast_to_out(bc: Broadcast) -> BroadcastJobOut:
    return BroadcastJobOut(
        id=bc.id,
        text=bc.text,
        status=bc.status.value,
        total=bc.total,
        sent=bc.sent,
        failed=bc.failed,
        created_at=bc.created_at,
        finished_at=bc.finished_at,
    )


# ---------- Support ----------


@router.get("/support", response_model=SupportPage)
async def list_support(
    _: User = Depends(current_admin),
    only_unanswered: bool = False,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> SupportPage:
    where = [SupportMessage.direction == SupportDirection.in_]

    async with SessionLocal() as session:
        if only_unanswered:
            answered_subq = (
                select(SupportMessage.replied_to_id)
                .where(
                    SupportMessage.direction == SupportDirection.out,
                    SupportMessage.replied_to_id.is_not(None),
                )
                .subquery()
            )
            where.append(SupportMessage.id.notin_(select(answered_subq.c.replied_to_id)))

        total = (
            await session.execute(
                select(func.count()).select_from(SupportMessage).where(*where)
            )
        ).scalar_one()

        rows = (
            await session.execute(
                select(SupportMessage, User.username)
                .join(User, User.telegram_id == SupportMessage.user_id, isouter=True)
                .where(*where)
                .order_by(SupportMessage.created_at.desc())
                .limit(size)
                .offset((page - 1) * size)
            )
        ).all()

    items = [
        SupportEntry(
            id=sm.id,
            user_id=sm.user_id,
            username=username,
            direction=sm.direction.value,
            text=sm.text,
            replied_by_admin_id=sm.replied_by_admin_id,
            created_at=sm.created_at,
        )
        for (sm, username) in rows
    ]
    return SupportPage(items=items, total=int(total), page=page, size=size)


@router.post("/support/{sid}/reply")
async def reply_support(
    sid: int,
    body: ReplyBody,
    admin: User = Depends(current_admin),
) -> dict[str, bool]:
    async with SessionLocal() as session:
        original = await session.get(SupportMessage, sid)
        if original is None or original.direction != SupportDirection.in_:
            raise HTTPException(status_code=404, detail="support message not found")
        sm = SupportMessage(
            user_id=original.user_id,
            direction=SupportDirection.out,
            text=body.text,
            replied_by_admin_id=admin.telegram_id,
            replied_to_id=sid,
        )
        session.add(sm)
        await session.commit()

    resp = await send_message(
        original.user_id,
        f"📩 <b>پاسخ پشتیبانی:</b>\n{escape(body.text)}",
    )
    return {"ok": bool(resp and resp.get("ok"))}
