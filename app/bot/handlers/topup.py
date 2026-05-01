from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import CB_TOPUP, main_menu_inline
from app.common.db.models import PaymentGateway, PaymentIntent, PaymentStatus
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.payment.nowpayments import (
    create_invoice,
    gen_order_id,
    get_min_amount_usd,
)
from app.common.settings import get_settings

router = Router(name="topup")


class TopUpStates(StatesGroup):
    waiting_amount = State()


async def _effective_min_usd() -> Decimal:
    settings = get_settings()
    np_min = await get_min_amount_usd()
    if np_min is None:
        return settings.min_topup_usd
    return max(settings.min_topup_usd, np_min)


@router.callback_query(F.data == CB_TOPUP)
async def on_topup_start(cb: CallbackQuery, state: FSMContext) -> None:
    if cb.from_user is None or cb.message is None:
        return
    min_usd = await _effective_min_usd()
    await state.set_state(TopUpStates.waiting_amount)
    await cb.message.answer(
        f"💵 مبلغ دلاری که می‌خواهید به کیف پول اضافه شود را وارد کنید:\n"
        f"(حداقل: <b>{min_usd}$</b>)\n\n"
        f"💎 پرداخت با ارز دیجیتال از طریق NowPayments انجام می‌شود.\n\n"
        f"مثال: <code>5</code> یا <code>10.50</code>",
    )
    await cb.answer()


@router.message(TopUpStates.waiting_amount)
async def on_topup_amount(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None or message.text is None:
        return

    try:
        amount_usd = Decimal(message.text.strip().replace(",", "."))
    except InvalidOperation:
        await message.answer(
            "❌ مبلغ نامعتبر است. لطفاً یک عدد وارد کنید.",
            reply_markup=main_menu_inline(),
        )
        return

    min_usd = await _effective_min_usd()
    if amount_usd < min_usd:
        await message.answer(
            f"❌ حداقل مبلغ {min_usd}$ است.",
            reply_markup=main_menu_inline(),
        )
        return

    processing_msg = await message.answer("⏳ در حال ساخت لینک پرداخت...")

    order_id = gen_order_id()
    try:
        payment = await create_invoice(amount_usd, order_id, message.from_user.id)
    except Exception:
        logger.exception(
            "NowPayments invoice creation failed for user {}", message.from_user.id
        )
        await processing_msg.edit_text(
            "❌ خطا در ساخت لینک پرداخت. لطفاً دوباره امتحان کنید.",
        )
        await message.answer("بازگشت به منو:", reply_markup=main_menu_inline())
        return

    async with SessionLocal() as session:
        intent = PaymentIntent(
            user_id=message.from_user.id,
            gateway=PaymentGateway.nowpayments,
            amount=payment["amount_usd"],
            currency="USD",
            status=PaymentStatus.pending,
            external_ref=payment["order_id"],
        )
        session.add(intent)
        await session.commit()

    kbd = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 پرداخت {amount_usd}$", url=payment["pay_url"])]
        ]
    )
    await processing_msg.edit_text(
        f"✅ لینک پرداخت آماده شد.\n\n"
        f"💵 مبلغ: <b>{amount_usd}$</b>\n"
        f"🔢 کد پیگیری: <code>{payment['order_id']}</code>\n\n"
        f"💎 روی دکمه زیر کلیک کنید و در صفحه‌ی NowPayments ارز دیجیتال موردنظر "
        f"خود (USDT, BTC, …) را انتخاب کنید:",
        reply_markup=kbd,
    )
