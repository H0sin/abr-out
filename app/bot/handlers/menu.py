from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bot.keyboards import (
    CB_SUPPORT,
    CB_WALLET,
    admin_user_panel,
    hide_reply_keyboard,
    main_menu_inline,
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


async def _block_guard_cb(cb: CallbackQuery) -> bool:
    if cb.from_user is None:
        return False
    if await _is_blocked(cb.from_user.id):
        await cb.answer("🚫 حساب شما مسدود است.", show_alert=True)
        return True
    return False


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
) -> None:
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

    # Clear any legacy persistent reply keyboard from older bot versions.
    await message.answer(
        "سلام! به مارکت‌پلیس اوت\u200cباند خوش اومدی.",
        reply_markup=hide_reply_keyboard(),
    )

    payload = (command.args or "").strip().lower()
    if payload == "topup":
        # Deep-link from miniapp: jump straight into the top-up FSM.
        from app.bot.handlers.topup import TopUpStates

        settings = get_settings()
        await state.set_state(TopUpStates.waiting_amount)
        await message.answer(
            f"💵 مبلغ دلاری که می‌خواهید به کیف پول اضافه شود را وارد کنید:\n"
            f"(حداقل: <b>{settings.min_topup_usd}$</b>)\n\n"
            f"مثال: <code>5</code> یا <code>10.50</code>",
        )
    else:
        await message.answer(
            "از منوی زیر یکی را انتخاب کن:",
            reply_markup=main_menu_inline(),
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


@router.callback_query(F.data == CB_WALLET)
async def on_wallet(cb: CallbackQuery) -> None:
    if cb.from_user is None or cb.message is None:
        return
    if await _block_guard_cb(cb):
        return
    from app.common.db.wallet import get_balance

    async with SessionLocal() as session:
        balance = await get_balance(session, cb.from_user.id)

    await cb.message.answer(
        f"💵 موجودی کیف پول: <b>{balance:.4f} USD</b>",
        reply_markup=main_menu_inline(),
    )
    await cb.answer()


@router.callback_query(F.data == CB_SUPPORT)
async def on_support_start(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.message is None:
        return
    if await _block_guard_cb(cb):
        return
    await state.set_state(SupportStates.waiting_message)
    await cb.message.answer(
        "📨 پیام خود را برای پشتیبانی ارسال کنید (متن).\n"
        "برای انصراف /cancel را بزنید.",
    )
    await cb.answer()


@router.message(SupportStates.waiting_message, F.text == "/cancel")
async def on_support_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❎ ارسال پیام پشتیبانی لغو شد.", reply_markup=main_menu_inline())


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
        await message.answer("متن پیام خالی بود.", reply_markup=main_menu_inline())
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
        reply_markup=main_menu_inline(),
    )
