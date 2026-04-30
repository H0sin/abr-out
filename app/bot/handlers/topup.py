from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import BTN_TOPUP, main_menu
from app.common.db.models import SwapWalletTx, SwapWalletTxStatus
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.payment.swapwallet import create_swapwallet_payment
from app.common.settings import get_settings

router = Router(name="topup")


class TopUpStates(StatesGroup):
    waiting_amount = State()


@router.message(F.text == BTN_TOPUP)
async def on_topup_start(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    settings = get_settings()
    await state.set_state(TopUpStates.waiting_amount)
    await message.answer(
        f"💵 مبلغ دلاری که می‌خواهید به کیف پول اضافه شود را وارد کنید:\n"
        f"(حداقل: <b>{settings.min_topup_usd}$</b>)\n\n"
        f"مثال: <code>5</code> یا <code>10.50</code>",
    )


@router.message(TopUpStates.waiting_amount)
async def on_topup_amount(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None or message.text is None:
        return

    settings = get_settings()

    try:
        amount_usd = Decimal(message.text.strip().replace(",", "."))
    except InvalidOperation:
        await message.answer(
            "❌ مبلغ نامعتبر است. لطفاً یک عدد وارد کنید.",
            reply_markup=main_menu(),
        )
        return

    if amount_usd < settings.min_topup_usd:
        await message.answer(
            f"❌ حداقل مبلغ {settings.min_topup_usd}$ است.",
            reply_markup=main_menu(),
        )
        return

    processing_msg = await message.answer("⏳ در حال ساخت لینک پرداخت...")

    try:
        payment = await create_swapwallet_payment(amount_usd, message.from_user.id)
    except Exception:
        logger.exception("SwapWallet payment creation failed for user {}", message.from_user.id)
        await processing_msg.edit_text(
            "❌ خطا در ساخت لینک پرداخت. لطفاً دوباره امتحان کنید.",
        )
        await message.answer("بازگشت به منو:", reply_markup=main_menu())
        return

    async with SessionLocal() as session:
        tx = SwapWalletTx(
            order_id=payment["order_id"],
            user_id=message.from_user.id,
            amount_usd=payment["amount_usd"],
            amount_irt=payment["amount_irt"],
            invoice_id=payment["invoice_id"],
            status=SwapWalletTxStatus.pending,
        )
        session.add(tx)
        await session.commit()

    kbd = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 پرداخت {amount_usd}$", url=payment["pay_url"])]
        ]
    )
    amount_irt_fmt = f"{payment['amount_irt']:,}"
    await processing_msg.edit_text(
        f"✅ لینک پرداخت آماده شد.\n\n"
        f"💵 مبلغ: <b>{amount_usd}$</b>\n"
        f"🪙 معادل تومانی: <b>{amount_irt_fmt} تومان</b>\n"
        f"🔢 کد پیگیری: <code>{payment['order_id']}</code>\n\n"
        f"برای پرداخت روی دکمه زیر کلیک کنید:",
        reply_markup=kbd,
    )
