from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bot.keyboards import (
    BTN_SUPPORT,
    BTN_WALLET,
    admin_user_panel,
    main_menu,
    support_reply_kb,
)
from app.common.db.models import SupportDirection, SupportMessage, User
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.settings import get_settings
from app.common.telegram_bot import copy_message, send_message

router = Router(name="menu")


class SupportStates(StatesGroup):
    waiting_message = State()


async def _is_blocked(user_id: int) -> bool:
    async with SessionLocal() as session:
        u = await session.get(User, user_id)
    return bool(u and u.is_blocked)


async def _block_guard(message: Message) -> bool:
    """Return True if user is blocked (and notify them); caller should stop."""
    if message.from_user is None:
        return False
    if await _is_blocked(message.from_user.id):
        await message.answer("🚫 حساب شما مسدود است.")
        return True
    return False


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user is None:
        return

    tg = message.from_user
    is_first_start = False

    async with SessionLocal() as session:
        # Upsert (insert if missing).
        await session.execute(
            pg_insert(User)
            .values(telegram_id=tg.id, username=tg.username)
            .on_conflict_do_update(
                index_elements=["telegram_id"],
                set_={"username": tg.username},
            )
        )
        await session.commit()

        user = await session.get(User, tg.id)
        if user is not None and user.started_at is None:
            user.started_at = datetime.now(timezone.utc)
            await session.commit()
            is_first_start = True

    if user is not None and user.is_blocked:
        await message.answer("🚫 حساب شما مسدود است.")
        return

    await message.answer(
        "سلام! به مارکت‌پلیس اوتباند خوش اومدی.\n\n"
        "از منوی پایین یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu(),
    )

    if is_first_start:
        await _notify_admins_new_user(user)


async def _notify_admins_new_user(user: User | None) -> None:
    if user is None:
        return
    settings = get_settings()
    if not settings.admin_ids:
        return

    text = (
        "👤 <b>کاربر جدید</b>\n"
        f"نام کاربری: {('@' + escape(user.username)) if user.username else '—'}\n"
        f"آی‌دی: <code>{user.telegram_id}</code>\n"
        f"تاریخ: {user.started_at.strftime('%Y-%m-%d %H:%M') if user.started_at else '—'}"
    )
    kb = admin_user_panel(user.telegram_id, is_blocked=user.is_blocked).model_dump(
        exclude_none=True
    )
    for admin_id in settings.admin_ids:
        try:
            await send_message(admin_id, text, reply_markup=kb)
        except Exception:
            logger.exception(
                "Failed to notify admin {} about new user {}", admin_id, user.telegram_id
            )


@router.message(F.text == BTN_WALLET)
async def on_wallet(message: Message) -> None:
    if message.from_user is None:
        return
    if await _block_guard(message):
        return
    from app.common.db.wallet import get_balance

    async with SessionLocal() as session:
        balance = await get_balance(session, message.from_user.id)

    await message.answer(
        f"💵 موجودی کیف پول: <b>{balance:.4f} USD</b>",
        reply_markup=main_menu(),
    )


@router.message(F.text == BTN_SUPPORT)
async def on_support_start(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    if await _block_guard(message):
        return
    await state.set_state(SupportStates.waiting_message)
    await message.answer(
        "📨 پیام خود را برای پشتیبانی ارسال کنید (متن).\n"
        "برای انصراف /cancel را بزنید.",
    )


@router.message(SupportStates.waiting_message, F.text == "/cancel")
async def on_support_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❎ ارسال پیام پشتیبانی لغو شد.", reply_markup=main_menu())


@router.message(SupportStates.waiting_message)
async def on_support_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None or not message.text:
        return
    if await _block_guard(message):
        return

    settings = get_settings()
    text = message.text.strip()
    if not text:
        await message.answer("متن پیام خالی بود.", reply_markup=main_menu())
        return

    async with SessionLocal() as session:
        sm = SupportMessage(
            user_id=message.from_user.id,
            direction=SupportDirection.in_,
            text=text,
            user_message_id=message.message_id,
        )
        session.add(sm)
        await session.commit()
        support_id = sm.id

    user_label = (
        f"@{escape(message.from_user.username)}"
        if message.from_user.username
        else f"<code>{message.from_user.id}</code>"
    )
    header = (
        f"📨 <b>پیام پشتیبانی</b>\n"
        f"از: {user_label} (<code>{message.from_user.id}</code>)"
    )

    kb = support_reply_kb(message.from_user.id, support_id).model_dump(
        exclude_none=True
    )
    for admin_id in settings.admin_ids:
        try:
            await send_message(admin_id, header)
            # Use copyMessage so the original (text/photo/etc.) is preserved.
            await copy_message(
                chat_id=admin_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=kb,
            )
        except Exception:
            logger.exception("Failed to forward support message to admin {}", admin_id)

    await message.answer(
        "✅ پیام شما برای پشتیبانی ارسال شد. به‌زودی پاسخ می‌گیرید.",
        reply_markup=main_menu(),
    )
