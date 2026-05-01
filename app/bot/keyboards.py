from __future__ import annotations

import time

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from app.common.settings import get_settings

# Visible labels (also used as inline button text).
BTN_OPEN_APP = "🛒 خرید و فروش اوت‌باند"
BTN_WALLET = "👛 کیف پول"
BTN_TOPUP = "💳 افزایش موجودی"
BTN_SUPPORT = "📨 پشتیبانی"

# Callback identifiers.
CB_WALLET = "menu:wallet"
CB_TOPUP = "menu:topup"
CB_SUPPORT = "menu:support"


def _miniapp_url() -> str | None:
    """Return the public Mini App URL with a cache-busting `?v=` suffix.
    Returns None when the public URL is not configured."""
    base = get_settings().public_base_url
    if not base:
        return None
    return f"{base}/app/?v={int(time.time())}"


def main_menu_inline() -> InlineKeyboardMarkup:
    """Glassy inline menu sent under chat messages. Replaces the legacy reply
    keyboard so initData reaches the Mini App reliably on every platform.

    First row: WebApp button (sends initData). Remaining rows: callback
    actions handled by the bot router."""
    rows: list[list[InlineKeyboardButton]] = []

    url = _miniapp_url()
    if url:
        rows.append(
            [InlineKeyboardButton(text=BTN_OPEN_APP, web_app=WebAppInfo(url=url))]
        )

    rows.append(
        [
            InlineKeyboardButton(text=BTN_WALLET, callback_data=CB_WALLET),
            InlineKeyboardButton(text=BTN_TOPUP, callback_data=CB_TOPUP),
        ]
    )
    rows.append([InlineKeyboardButton(text=BTN_SUPPORT, callback_data=CB_SUPPORT)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hide_reply_keyboard() -> ReplyKeyboardRemove:
    """Send once on /start to clear any legacy persistent reply keyboard."""
    return ReplyKeyboardRemove()


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
