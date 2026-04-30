from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from app.common.settings import get_settings

BTN_OPEN_APP = "🚀 باز کردن مینی‌اپ"
BTN_WALLET = "👛 کیف پول"
BTN_TOPUP = "💳 افزایش موجودی"
BTN_SUPPORT = "📨 پشتیبانی"


def main_menu() -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=BTN_WALLET), KeyboardButton(text=BTN_TOPUP)],
        [KeyboardButton(text=BTN_SUPPORT)],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def open_app_inline() -> InlineKeyboardMarkup | None:
    """Inline button to open the Mini App. Inline WebApp buttons send initData
    on all platforms (including Telegram Desktop), unlike reply-keyboard ones
    which can sometimes be flaky on desktop clients."""
    base = get_settings().public_base_url
    if not base:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BTN_OPEN_APP,
                    web_app=WebAppInfo(url=f"{base}/app/"),
                )
            ]
        ]
    )


def admin_user_panel(user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    """Inline panel sent to admins for managing a single user."""
    block_label = "🔓 آنبلاک" if is_blocked else "🚫 بلاک"
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="➕ افزایش موجودی", callback_data=f"adm:bal:add:{user_id}"
            ),
            InlineKeyboardButton(
                text="➖ کاهش موجودی", callback_data=f"adm:bal:sub:{user_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                text="✉️ پیام به کاربر", callback_data=f"adm:msg:{user_id}"
            ),
            InlineKeyboardButton(
                text=block_label, callback_data=f"adm:block:{user_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                text="📜 تراکنش‌های کاربر", callback_data=f"adm:txs:{user_id}"
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_reply_kb(user_id: int, support_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ پاسخ", callback_data=f"sup:reply:{user_id}:{support_id}"
                )
            ]
        ]
    )
