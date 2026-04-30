"""Bot middleware: short-circuit any update from a blocked user."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from app.common.db.models import User
from app.common.db.session import SessionLocal
from app.common.settings import get_settings


async def _user_block_status(user_id: int) -> bool:
    async with SessionLocal() as session:
        row = await session.execute(
            select(User.is_blocked).where(User.telegram_id == user_id)
        )
        v = row.scalar_one_or_none()
    return bool(v)


class BlockMiddleware(BaseMiddleware):
    """Drop messages from blocked users, with one short notice."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        # Admins are never blocked.
        if from_user.id in get_settings().admin_ids:
            return await handler(event, data)

        if not await _user_block_status(from_user.id):
            return await handler(event, data)

        # Blocked: respond and stop propagation.
        if isinstance(event, Message):
            try:
                await event.answer("🚫 حساب شما مسدود است.")
            except Exception:
                pass
        elif isinstance(event, CallbackQuery):
            try:
                await event.answer("🚫 حساب شما مسدود است.", show_alert=True)
            except Exception:
                pass
        return None
