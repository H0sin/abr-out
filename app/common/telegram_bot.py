"""Shared Telegram Bot API helper used by API and worker processes.

The aiogram `Bot` instance lives only inside the polling process. For other
processes (FastAPI, worker jobs) we make plain HTTP calls to the Bot API.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.common.logging import logger
from app.common.settings import get_settings

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _api_url(method: str, token: str | None = None) -> str:
    tok = token or get_settings().bot_token
    return f"https://api.telegram.org/bot{tok}/{method}"


async def _post(method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST to Bot API. Returns parsed JSON or None on transport error."""
    url = _api_url(method)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json=payload)
        return r.json() if r.content else None
    except Exception:
        logger.exception("Telegram API call failed: {}", method)
        return None


async def send_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool = True,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await _post("sendMessage", payload)


async def copy_message(
    chat_id: int,
    from_chat_id: int,
    message_id: int,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "from_chat_id": from_chat_id,
        "message_id": message_id,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await _post("copyMessage", payload)


async def edit_message_reply_markup(
    chat_id: int,
    message_id: int,
    reply_markup: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return await _post(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup or {"inline_keyboard": []},
        },
    )


async def answer_callback_query(
    callback_query_id: str,
    text: str | None = None,
    show_alert: bool = False,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text is not None:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    return await _post("answerCallbackQuery", payload)


def is_user_blocked_response(resp: dict[str, Any] | None) -> bool:
    """True if Telegram says the user blocked the bot or chat not found."""
    if not resp or resp.get("ok"):
        return False
    desc = (resp.get("description") or "").lower()
    return (
        "bot was blocked" in desc
        or "chat not found" in desc
        or "user is deactivated" in desc
    )
