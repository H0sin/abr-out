from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from app.common.settings import get_settings

BTN_OPEN_APP = "🚀 باز کردن مینی‌اپ"
BTN_WALLET = "👛 کیف پول"
BTN_TOPUP = "💳 افزایش موجودی"


def main_menu() -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    base = get_settings().public_base_url
    if base:
        rows.append(
            [KeyboardButton(text=BTN_OPEN_APP, web_app=WebAppInfo(url=f"{base}/app/"))]
        )
    rows.append([KeyboardButton(text=BTN_WALLET), KeyboardButton(text=BTN_TOPUP)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
