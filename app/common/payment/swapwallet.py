from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import json
import time
import uuid
from decimal import Decimal

import httpx

from app.common.logging import logger
from app.common.settings import get_settings

_AGORA_PRICES_URL = "https://swapwallet.app/api/v1/market/prices"
_RATE_CACHE_TTL_SEC = 300  # 5 minutes

# In-process cache: {"rate": int | None, "ts": float}
_rate_cache: dict[str, float | int | None] = {"rate": None, "ts": 0.0}
_rate_lock = asyncio.Lock()


async def get_usd_to_irt_rate() -> int:
    """
    Fetch live USDT→IRT rate from SwapWallet (Agora) v1/market/prices.
    Cached for 5 minutes. Falls back to last successful rate, then to
    settings.swapwallet_fallback_rate, otherwise raises.
    """
    now = time.time()
    cached = _rate_cache["rate"]
    if cached and now - float(_rate_cache["ts"]) < _RATE_CACHE_TTL_SEC:
        return int(cached)

    async with _rate_lock:
        cached = _rate_cache["rate"]
        if cached and time.time() - float(_rate_cache["ts"]) < _RATE_CACHE_TTL_SEC:
            return int(cached)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(_AGORA_PRICES_URL)
                r.raise_for_status()
                rate = int(float(r.json()["result"]["USDT/IRT"]))
            _rate_cache["rate"] = rate
            _rate_cache["ts"] = time.time()
            logger.info("[SwapWallet] USDT/IRT rate refreshed: {}", rate)
            return rate
        except Exception as exc:
            logger.warning("[SwapWallet] failed to fetch live rate: {}", exc)
            if cached:
                logger.info("[SwapWallet] using stale cached rate: {}", cached)
                return int(cached)
            fallback = get_settings().swapwallet_fallback_rate
            if fallback > 0:
                logger.warning("[SwapWallet] using settings fallback rate: {}", fallback)
                return fallback
            raise RuntimeError("نرخ دلار در دسترس نیست") from exc


async def usd_to_irt(amount_usd: Decimal) -> int:
    """Convert USD amount to IRT (Toman) using the live rate."""
    rate = await get_usd_to_irt_rate()
    return int(round(float(amount_usd) * rate))


async def create_swapwallet_payment(amount_usd: Decimal, chat_id: int) -> dict:
    """
    Create a SwapWallet payment request and return order info + payment URL.
    """
    settings = get_settings()
    order_id = f"TX-{uuid.uuid4().hex[:12]}"
    amount_irt = await usd_to_irt(amount_usd)

    base = settings.public_base_url
    if not base:
        raise RuntimeError("DOMAIN (or WEBHOOK_BASE_URL) is not configured")
    webhook_url = f"{base}/webhook/swapwallet"

    body = {
        "amount": {"number": str(amount_irt), "unit": "IRT"},
        "ttl": 3600,
        "userLanguage": "FA",
        "orderId": order_id,
        "webhookUrl": webhook_url,
        "description": f"افزایش موجودی {amount_usd}$",
        "userTelegramId": chat_id,
    }

    if not settings.swapwallet_api_key:
        raise RuntimeError("SWAPWALLET_API_KEY is not configured")

    logger.info(
        "[SwapWallet] creating payment order={} amount_usd={} amount_irt={}",
        order_id, amount_usd, amount_irt,
    )

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://swapwallet.app/api/v1/merchants/resid",
            json=body,
            headers={"Authorization": f"Bearer {settings.swapwallet_api_key}"},
        )
        r.raise_for_status()
        result = r.json()["result"]

    links = result["paymentLinks"]
    pay_url = next(
        (l["url"] for l in links if l["type"] == "TELEGRAM_WEBAPP"),
        links[0]["url"],
    )

    return {
        "order_id": order_id,
        "invoice_id": result["id"],
        "pay_url": pay_url,
        "amount_usd": amount_usd,
        "amount_irt": amount_irt,
    }


def verify_swapwallet_hmac(raw_body: bytes, received_hmac: str, api_key: str) -> bool:
    """Verify the HMAC-SHA256 signature on a SwapWallet webhook callback."""
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return False
    data.pop("hmac", None)
    body_no_hmac = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    computed = hmac_mod.new(
        api_key.encode("utf-8"),
        body_no_hmac.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac_mod.compare_digest(computed.lower(), received_hmac.lower())
