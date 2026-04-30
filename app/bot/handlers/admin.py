"""Admin-only inline panel and FSMs (adjust balance, DM user, block, support reply)."""
from __future__ import annotations

import uuid
from datetime import timezone
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.keyboards import admin_user_panel
from app.common.db.models import (
    SupportDirection,
    SupportMessage,
    TxnType,
    User,
    WalletTransaction,
)
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.settings import get_settings
from app.common.telegram_bot import send_message

router = Router(name="admin")


class AdjustStates(StatesGroup):
    waiting_amount = State()
    waiting_note = State()


class DMStates(StatesGroup):
    waiting_text = State()


class SupportReplyStates(StatesGroup):
    waiting_text = State()


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in get_settings().admin_ids


# ---------- Entry: /admin <user_id> ----------


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer(
            "استفاده: <code>/admin &lt;telegram_id&gt;</code>\n"
            "از پنل وب برای جستجوی کاربر استفاده کنید."
        )
        return

    target_id = int(parts[1])
    await _send_user_panel(message.chat.id, target_id)


async def _send_user_panel(chat_id: int, user_id: int) -> None:
    async with SessionLocal() as session:
        u = await session.get(User, user_id)
        if u is None:
            await send_message(chat_id, "❌ کاربر یافت نشد.")
            return
        balance = (
            await session.execute(
                select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(
                    WalletTransaction.user_id == user_id
                )
            )
        ).scalar_one()

    text = (
        "👤 <b>پنل کاربر</b>\n"
        f"نام کاربری: {('@' + escape(u.username)) if u.username else '—'}\n"
        f"آی‌دی: <code>{u.telegram_id}</code>\n"
        f"موجودی: <b>{balance}$</b>\n"
        f"وضعیت: {'🚫 مسدود' if u.is_blocked else '✅ فعال'}"
    )
    kb = admin_user_panel(u.telegram_id, is_blocked=u.is_blocked).model_dump(
        exclude_none=True
    )
    await send_message(chat_id, text, reply_markup=kb)


# ---------- Callbacks: adm:bal / adm:msg / adm:block / adm:txs ----------


@router.callback_query(F.data.startswith("adm:bal:"))
async def cb_adjust_balance(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("⛔️ فقط ادمین.", show_alert=True)
        return
    # adm:bal:add:<id>  or  adm:bal:sub:<id>
    parts = (cq.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"add", "sub"} or not parts[3].lstrip("-").isdigit():
        await cq.answer()
        return
    direction = parts[2]
    user_id = int(parts[3])
    await state.set_state(AdjustStates.waiting_amount)
    await state.update_data(target_user_id=user_id, direction=direction)
    label = "افزایش" if direction == "add" else "کاهش"
    await cq.message.answer(
        f"💵 مقدار <b>{label}</b> موجودی برای کاربر <code>{user_id}</code> را وارد کنید (به دلار).\n"
        "/cancel برای انصراف."
    )
    await cq.answer()


@router.message(AdjustStates.waiting_amount, F.text == "/cancel")
@router.message(DMStates.waiting_text, F.text == "/cancel")
@router.message(AdjustStates.waiting_note, F.text == "/cancel")
@router.message(SupportReplyStates.waiting_text, F.text == "/cancel")
async def cancel_admin_fsm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❎ لغو شد.")


@router.message(AdjustStates.waiting_amount)
async def on_adjust_amount(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    if not message.text:
        return
    try:
        amount = Decimal(message.text.strip().replace(",", "."))
    except InvalidOperation:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    if amount <= 0:
        await message.answer("❌ مبلغ باید بزرگتر از صفر باشد.")
        return
    await state.update_data(amount=str(amount))
    await state.set_state(AdjustStates.waiting_note)
    await message.answer("📝 علت این تنظیم را وارد کنید (اجباری):")


@router.message(AdjustStates.waiting_note)
async def on_adjust_note(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    note = (message.text or "").strip()
    if len(note) < 2:
        await message.answer("❌ علت بسیار کوتاه است.")
        return

    data = await state.get_data()
    await state.clear()
    user_id = int(data["target_user_id"])
    direction = data["direction"]
    amount = Decimal(data["amount"])
    signed = amount if direction == "add" else -amount
    admin_id = message.from_user.id

    async with SessionLocal() as session:
        target = await session.get(User, user_id)
        if target is None:
            await message.answer("❌ کاربر یافت نشد.")
            return
        wt = WalletTransaction(
            user_id=user_id,
            amount=signed,
            currency="USD",
            type=TxnType.adjustment,
            ref=f"admin:{admin_id}",
            note=note,
            created_by_admin_id=admin_id,
            idempotency_key=f"admin-adjust-{uuid.uuid4()}",
        )
        session.add(wt)
        await session.commit()

    sign = "➕" if signed > 0 else "➖"
    await message.answer(
        f"✅ ثبت شد. {sign} <b>{abs(signed)}$</b> برای <code>{user_id}</code>\n"
        f"علت: {escape(note)}"
    )
    try:
        verb = "افزایش" if signed > 0 else "کاهش"
        await send_message(
            user_id,
            f"💼 موجودی کیف پول شما توسط مدیر {verb} یافت.\n"
            f"مبلغ: <b>{abs(signed)}$</b>\n"
            f"علت: {escape(note)}",
        )
    except Exception:
        logger.exception("Failed to notify user {} about adjustment", user_id)


@router.callback_query(F.data.startswith("adm:msg:"))
async def cb_dm(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("⛔️ فقط ادمین.", show_alert=True)
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        await cq.answer()
        return
    user_id = int(parts[2])
    await state.set_state(DMStates.waiting_text)
    await state.update_data(target_user_id=user_id)
    await cq.message.answer(
        f"✉️ متن پیام برای کاربر <code>{user_id}</code> را وارد کنید.\n/cancel برای انصراف."
    )
    await cq.answer()


@router.message(DMStates.waiting_text)
async def on_dm_text(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return
    data = await state.get_data()
    await state.clear()
    user_id = int(data["target_user_id"])
    admin_id = message.from_user.id

    async with SessionLocal() as session:
        sm = SupportMessage(
            user_id=user_id,
            direction=SupportDirection.out,
            text=text,
            replied_by_admin_id=admin_id,
        )
        session.add(sm)
        await session.commit()

    try:
        await send_message(user_id, f"📩 <b>پیام مدیر:</b>\n{escape(text)}")
        await message.answer("✅ ارسال شد.")
    except Exception:
        logger.exception("Failed to DM user {}", user_id)
        await message.answer("❌ ارسال نشد.")


@router.callback_query(F.data.startswith("adm:block:"))
async def cb_block(cq: CallbackQuery) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("⛔️ فقط ادمین.", show_alert=True)
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        await cq.answer()
        return
    user_id = int(parts[2])

    async with SessionLocal() as session:
        u = await session.get(User, user_id)
        if u is None:
            await cq.answer("کاربر یافت نشد", show_alert=True)
            return
        u.is_blocked = not u.is_blocked
        new_state = u.is_blocked
        await session.commit()

    label = "🚫 مسدود شد" if new_state else "🔓 آنبلاک شد"
    await cq.answer(label, show_alert=True)
    try:
        await send_message(
            user_id,
            "🚫 حساب شما توسط مدیر مسدود شد."
            if new_state
            else "✅ حساب شما توسط مدیر فعال شد.",
        )
    except Exception:
        logger.exception("Failed to notify user {} about block toggle", user_id)
    # Refresh the panel keyboard.
    try:
        kb = admin_user_panel(user_id, is_blocked=new_state).model_dump(
            exclude_none=True
        )
        if cq.message is not None:
            await cq.message.edit_reply_markup(reply_markup=admin_user_panel(user_id, new_state))
        # silence unused
        _ = kb
    except Exception:
        pass


@router.callback_query(F.data.startswith("adm:txs:"))
async def cb_txs(cq: CallbackQuery) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("⛔️ فقط ادمین.", show_alert=True)
        return
    parts = (cq.data or "").split(":")
    if len(parts) != 3 or not parts[2].lstrip("-").isdigit():
        await cq.answer()
        return
    user_id = int(parts[2])

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(WalletTransaction)
                .where(WalletTransaction.user_id == user_id)
                .order_by(WalletTransaction.created_at.desc())
                .limit(10)
            )
        ).scalars().all()

    if not rows:
        await cq.answer("بدون تراکنش", show_alert=True)
        return

    lines = ["📜 <b>۱۰ تراکنش اخیر</b>"]
    for r in rows:
        sign = "+" if r.amount > 0 else ""
        ts = r.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        line = f"<code>{ts}</code> · {r.type.value} · <b>{sign}{r.amount}$</b>"
        if r.note:
            line += f" — {escape(r.note)}"
        lines.append(line)
    if cq.message is not None:
        await cq.message.answer("\n".join(lines))
    await cq.answer()


# ---------- Support reply ----------


@router.callback_query(F.data.startswith("sup:reply:"))
async def cb_support_reply(cq: CallbackQuery, state: FSMContext) -> None:
    if cq.from_user is None or not _is_admin(cq.from_user.id):
        await cq.answer("⛔️ فقط ادمین.", show_alert=True)
        return
    parts = (cq.data or "").split(":")
    # sup:reply:<user_id>:<support_id>
    if (
        len(parts) != 4
        or not parts[2].lstrip("-").isdigit()
        or not parts[3].isdigit()
    ):
        await cq.answer()
        return
    user_id = int(parts[2])
    support_id = int(parts[3])
    await state.set_state(SupportReplyStates.waiting_text)
    await state.update_data(target_user_id=user_id, support_id=support_id)
    await cq.message.answer(
        f"↩️ متن پاسخ برای کاربر <code>{user_id}</code> را ارسال کنید.\n/cancel برای انصراف."
    )
    await cq.answer()


@router.message(SupportReplyStates.waiting_text)
async def on_support_reply_text(message: Message, state: FSMContext) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return
    data = await state.get_data()
    await state.clear()
    user_id = int(data["target_user_id"])
    support_id = int(data["support_id"])
    admin_id = message.from_user.id

    async with SessionLocal() as session:
        sm = SupportMessage(
            user_id=user_id,
            direction=SupportDirection.out,
            text=text,
            replied_by_admin_id=admin_id,
            replied_to_id=support_id,
        )
        session.add(sm)
        await session.commit()

    try:
        await send_message(
            user_id,
            f"📩 <b>پاسخ پشتیبانی:</b>\n{escape(text)}",
        )
        await message.answer("✅ ارسال شد.")
    except Exception:
        logger.exception("Failed to send support reply to user {}", user_id)
        await message.answer("❌ ارسال نشد.")
