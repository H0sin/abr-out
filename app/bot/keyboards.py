from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_BUY = "🛒 خرید"
BTN_SELL = "💰 فروش"
BTN_WALLET = "👛 کیف پول"
BTN_TOPUP = "💳 افزایش موجودی"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BUY)],
            [KeyboardButton(text=BTN_SELL), KeyboardButton(text=BTN_WALLET)],
            [KeyboardButton(text=BTN_TOPUP)],
        ],
        resize_keyboard=True,
    )
