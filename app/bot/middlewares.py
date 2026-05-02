"""Bot middleware: short-circuit any update from a blocked user."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from app.bot.keyboards import CB_MSHIP_CHECK, join_channel_kb
from app.common.db.models import User
from app.common.db.session import SessionLocal
from app.common.logging import logger
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


_MEMBER_OK = {"member", "administrator", "creator"}


_MEMBER_OK = {"member", "administrator", "creator"}


async def is_channel_member(bot: Bot | None, channel: str, user_id: int) -> bool:
    """True if the user is a member of ``channel``. False on any error."""
    if bot is None or not channel:
        return False
    try:
        cm = await bot.get_chat_member(channel, user_id)
        return getattr(cm, "status", None) in _MEMBER_OK
    except Exception as exc:
        logger.warning(
            "get_chat_member failed for channel={} user={}: {}",
            channel,
            user_id,
            exc,
        )
        return False


class MembershipMiddleware(BaseMiddleware):
    """Force users to join the configured Telegram channel before interacting.

    Skips: admins, bots, updates without ``from_user``, and the re-check
    callback itself. When ``REQUIRED_CHANNEL`` is unset the middleware is a
    no-op. WebApp/URL buttons are not routed through aiogram middleware, so
    link-behind buttons remain freely accessible by design.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        settings = get_settings()
        channel = settings.required_channel.strip()
        if not channel:
            return await handler(event, data)

        from_user = getattr(event, "from_user", None)
        if from_user is None or getattr(from_user, "is_bot", False):
            return await handler(event, data)
        if from_user.id in settings.admin_ids:
            return await handler(event, data)

        # Always allow the re-check callback through so the handler can run.
        if isinstance(event, CallbackQuery) and (event.data or "") == CB_MSHIP_CHECK:
            return await handler(event, data)

        # Always allow /start through: cmd_start handles the upsert + admin
        # notification + channel-gate UI itself, so users who never joined
        # the channel still get a User row + their first-start notification.
        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text.startswith("/start"):
                return await handler(event, data)

        bot: Bot | None = data.get("bot")
        is_member = False
        if bot is not None:
            try:
                cm = await bot.get_chat_member(channel, from_user.id)
                is_member = getattr(cm, "status", None) in _MEMBER_OK
            except Exception as exc:
                logger.warning(
                    "get_chat_member failed for channel={} user={}: {}",
                    channel,
                    from_user.id,
                    exc,
                )
                is_member = False

        if is_member:
            return await handler(event, data)

        url = settings.effective_required_channel_url
        kb = join_channel_kb(url).model_dump(exclude_none=True)
        text = "🔒 برای استفاده از ربات ابتدا در کانال ما عضو شوید."
        if isinstance(event, Message):
            try:
                await event.answer(text, reply_markup=kb)
            except Exception:
                pass
        elif isinstance(event, CallbackQuery):
            try:
                await event.answer("ابتدا عضو کانال شوید.", show_alert=True)
            except Exception:
                pass
            if event.message is not None:
                try:
                    await event.message.answer(text, reply_markup=kb)
                except Exception:
                    pass
        return None
