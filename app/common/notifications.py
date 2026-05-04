"""Notification helpers for listing lifecycle events.

These helpers fan out a single Telegram message to every distinct buyer
that owns at least one non-deleted ``Config`` under a given listing.
Failures are logged and swallowed: notifications are best-effort and must
never roll back the calling DB transaction.
"""
from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.models import Config, ConfigStatus
from app.common.logging import logger
from app.common.settings import get_settings
from app.common.telegram_bot import send_message


async def _distinct_buyer_ids(
    session: AsyncSession,
    listing_id: int,
    *,
    only_with_price_flag: bool = False,
    only_active: bool = False,
) -> list[int]:
    """Return distinct ``buyer_user_id`` for non-deleted configs.

    ``only_with_price_flag`` is used by the price-increase path so we only
    notify buyers who opted in to the auto-disable behaviour.
    ``only_active`` restricts to currently-active configs (used when the
    notification message only makes sense for active subscribers, e.g. a
    listing being disabled while the buyer's config has already been
    self-disabled).
    """
    stmt = select(Config.buyer_user_id).where(
        Config.listing_id == listing_id,
        Config.status != ConfigStatus.deleted,
    )
    if only_with_price_flag:
        stmt = stmt.where(Config.auto_disable_on_price_increase.is_(True))
    if only_active:
        stmt = stmt.where(Config.status == ConfigStatus.active)
    stmt = stmt.distinct()
    rows = (await session.execute(stmt)).all()
    return [int(r[0]) for r in rows]


async def notify_listing_buyers(
    session: AsyncSession,
    listing_id: int,
    text: str,
    *,
    only_with_price_flag: bool = False,
    only_active: bool = False,
) -> int:
    """Send ``text`` once to every distinct affected buyer. Returns count sent."""
    buyer_ids = await _distinct_buyer_ids(
        session,
        listing_id,
        only_with_price_flag=only_with_price_flag,
        only_active=only_active,
    )
    return await _send_to_users(buyer_ids, text)


async def notify_users(user_ids: Iterable[int], text: str) -> int:
    """Send ``text`` to a pre-computed set of telegram user ids."""
    return await _send_to_users(list(user_ids), text)


async def notify_channel_new_listing(
    listing_id: int,
    price_per_gb_usd: Decimal,
) -> bool:
    """Announce a newly-activated listing in the required-join channel."""
    settings = get_settings()
    chat_id = settings.required_channel_post_chat
    bot_username = settings.bot_username.strip().lstrip("@")
    if chat_id is None:
        logger.info(
            "[notifications] skip channel listing announcement: channel chat is not configured"
        )
        return False
    if not bot_username:
        logger.info(
            "[notifications] skip channel listing announcement: bot_username is empty"
        )
        return False

    start_url = f"https://t.me/{bot_username}?start=buy_{listing_id}"
    text = (
        "🆕 <b>اوت‌باند جدید اضافه شد</b>\n\n"
        f"<b>آیدی:</b> <code>#{listing_id}</code>\n"
        f"<b>قیمت:</b> <b>{escape(str(price_per_gb_usd))}</b> دلار / GB"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "🛒 خرید",
                    "url": start_url,
                }
            ]
        ]
    }
    try:
        resp = await send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception(
            "[notifications] channel listing announcement failed listing_id={}",
            listing_id,
        )
        return False
    ok = bool(resp and resp.get("ok"))
    if not ok:
        logger.warning(
            "[notifications] channel listing announcement rejected listing_id={} resp={}",
            listing_id,
            resp,
        )
    return ok


async def _send_to_users(user_ids: list[int], text: str) -> int:
    sent = 0
    for uid in user_ids:
        try:
            resp = await send_message(uid, text)
            if resp and resp.get("ok"):
                sent += 1
        except Exception:
            logger.exception(
                "[notifications] failed to send to user_id={}", uid
            )
    return sent
