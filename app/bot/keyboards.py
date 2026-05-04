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
BTN_WALLET_HUB = "💼 واریز / برداشت / کیف پول"
BTN_HUB_DEPOSIT = "💳 واریز"
BTN_HUB_WITHDRAW = "🏧 برداشت"
BTN_HUB_HISTORY = "📜 تراکنش‌ها"

# Callback identifiers.
CB_WALLET = "menu:wallet"
CB_TOPUP = "menu:topup"
CB_SUPPORT = "menu:support"
CB_WALLET_HUB = "menu:wallet_hub"
CB_MSHIP_CHECK = "mship:check"


def join_channel_kb(channel_url: str) -> InlineKeyboardMarkup:
    """Gate keyboard: a Join URL button + a re-check callback button."""
    rows: list[list[InlineKeyboardButton]] = []
    if channel_url:
        rows.append(
            [InlineKeyboardButton(text="🔔 عضویت در کانال", url=channel_url)]
        )
    rows.append(
        [InlineKeyboardButton(text="✅ بررسی عضویت", callback_data=CB_MSHIP_CHECK)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _miniapp_url(route: str = "") -> str | None:
    """Return the public Mini App URL with a cache-busting `?v=` suffix and an
    optional hash route (e.g. ``"/withdraw"``). Returns None when the public
    URL is not configured."""
    base = get_settings().public_base_url
    if not base:
        return None
    suffix = f"#{route}" if route else ""
    return f"{base}/app/?v={int(time.time())}{suffix}"


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
            InlineKeyboardButton(text=BTN_WALLET_HUB, callback_data=CB_WALLET_HUB),
        ]
    )
    rows.append([InlineKeyboardButton(text=BTN_SUPPORT, callback_data=CB_SUPPORT)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wallet_hub_inline() -> InlineKeyboardMarkup:
    """Sub-menu shown after the user taps the unified Wallet hub button.

    «واریز» reuses the existing top-up FSM via callback. «برداشت» and
    «تراکنش‌ها» are WebApp buttons that deep-link directly into the Mini
    App's hash routes so the user lands on the right page in one tap.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=BTN_HUB_DEPOSIT, callback_data=CB_TOPUP)]
    ]
    withdraw_url = _miniapp_url("/withdraw")
    history_url = _miniapp_url("/wallet")
    second_row: list[InlineKeyboardButton] = []
    if withdraw_url:
        second_row.append(
            InlineKeyboardButton(
                text=BTN_HUB_WITHDRAW, web_app=WebAppInfo(url=withdraw_url)
            )
        )
    if history_url:
        second_row.append(
            InlineKeyboardButton(
                text=BTN_HUB_HISTORY, web_app=WebAppInfo(url=history_url)
            )
        )
    if second_row:
        rows.append(second_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def listing_buy_inline(listing_id: int) -> InlineKeyboardMarkup:
    """WebApp button that opens Browse and preselects one listing."""
    url = _miniapp_url(f"/browse?listing={listing_id}")
    root_url = _miniapp_url()
    rows: list[list[InlineKeyboardButton]] = []
    if url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🛒 خرید اوت‌باند #{listing_id}",
                    web_app=WebAppInfo(url=url),
                )
            ]
        )
    if root_url:
        rows.append(
            [InlineKeyboardButton(text=BTN_OPEN_APP, web_app=WebAppInfo(url=root_url))]
        )
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
