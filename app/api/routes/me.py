from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import current_user
from app.common.db.models import User
from app.common.db.session import SessionLocal
from app.common.db.wallet import get_balance

router = APIRouter(prefix="/api/me", tags=["me"])


class MeOut(BaseModel):
    telegram_id: int
    username: str | None
    role: str
    balance_usd: Decimal


@router.get("", response_model=MeOut)
async def me(user: User = Depends(current_user)) -> MeOut:
    async with SessionLocal() as session:
        balance = await get_balance(session, user.telegram_id)
    return MeOut(
        telegram_id=user.telegram_id,
        username=user.username,
        role=user.role.value,
        balance_usd=balance,
    )
