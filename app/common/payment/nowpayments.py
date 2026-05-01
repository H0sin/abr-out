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

NOWPAYMENTS_BASE = "https://api.nowpayments.io/v1"

# Reference cheapest/common coin used to estimate the global USD floor for
# top-ups. NowPayments returns a per-coin minimum, so we pick a widely
# supported low-fee coin to compute a permissive lower bound.
_MIN_AMOUNT_REF_COIN = "usdttrc20"
_MIN_AMOUNT_CACHE_TTL_SEC = 600  # 10 minutes

_min_cache: dict[str, float | Decimal | None] = {"value": None, "ts": 0.0}
_min_lock = asyncio.Lock()


def gen_order_id() -> str:
    return f"NP-{uuid.uuid4().hex[:12]}"


async def get_min_amount_usd() -> Decimal | None:
    """
    Fetch the minimum top-up amount in USD from NowPayments for the reference
    coin (USDT-TRC20). Cached for 10 minutes. Returns ``None`` on failure
    (caller should fall back to ``settings.min_topup_usd``).
    """
    now = time.time()
    cached = _min_cache["value"]
    if cached is not None and now - float(_min_cache["ts"]) < _MIN_AMOUNT_CACHE_TTL_SEC:
        return Decimal(str(cached))

    settings = get_settings()
    if not settings.nowpayments_api_key:
        return None

    async with _min_lock:
        cached = _min_cache["value"]
        if cached is not None and time.time() - float(_min_cache["ts"]) < _MIN_AMOUNT_CACHE_TTL_SEC:
            return Decimal(str(cached))

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{NOWPAYMENTS_BASE}/min-amount",
                    params={
                        "currency_from": _MIN_AMOUNT_REF_COIN,
                        "currency_to": "usd",
                        "fiat_equivalent": "usd",
                    },
                    headers={"x-api-key": settings.nowpayments_api_key},
                )
                r.raise_for_status()
                data = r.json()
            # The API can return either {min_amount: <num>} (already in
            # currency_to=usd) or a fiat_equivalent field. Prefer
            # fiat_equivalent if present.
            raw = data.get("fiat_equivalent") or data.get("min_amount")
            if raw is None:
                return None
            value = Decimal(str(raw))
            _min_cache["value"] = float(value)
            _min_cache["ts"] = time.time()
            logger.info("[NowPayments] min-amount refreshed: {} USD", value)
            return value
        except Exception as exc:
            logger.warning("[NowPayments] failed to fetch min-amount: {}", exc)
            return None


async def create_invoice(amount_usd: Decimal, order_id: str, user_id: int) -> dict:
    """
    Create a NowPayments hosted invoice and return order info + payment URL.
    """
    settings = get_settings()

    if not settings.nowpayments_api_key:
        raise RuntimeError("NOWPAYMENTS_API_KEY is not configured")

    base = settings.public_base_url
    if not base:
        raise RuntimeError("DOMAIN (or WEBHOOK_BASE_URL) is not configured")
    ipn_url = f"{base}/webhook/nowpayments"

    return_url = (
        f"https://t.me/{settings.bot_username}?start=topup_done"
        if settings.bot_username
        else None
    )

    body: dict = {
        "price_amount": float(amount_usd),
        "price_currency": "usd",
        "order_id": order_id,
        "order_description": f"Top-up {amount_usd}$ for user {user_id}",
        "ipn_callback_url": ipn_url,
        "is_fee_paid_by_user": True,
    }
    if return_url:
        body["success_url"] = return_url
        body["cancel_url"] = return_url

    logger.info(
        "[NowPayments] creating invoice order={} amount_usd={} user={}",
        order_id, amount_usd, user_id,
    )

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{NOWPAYMENTS_BASE}/invoice",
            json=body,
            headers={
                "x-api-key": settings.nowpayments_api_key,
                "Content-Type": "application/json",
            },
        )
        r.raise_for_status()
        result = r.json()

    pay_url = result.get("invoice_url")
    invoice_id = str(result.get("id") or "")
    if not pay_url:
        raise RuntimeError(f"NowPayments did not return invoice_url: {result}")

    return {
        "order_id": order_id,
        "invoice_id": invoice_id,
        "pay_url": pay_url,
        "amount_usd": amount_usd,
    }


def verify_nowpayments_signature(
    raw_body: bytes, received_sig: str, ipn_secret: str
) -> bool:
    """
    Verify the ``x-nowpayments-sig`` header on an IPN callback.

    NowPayments' scheme: HMAC-SHA512 over ``JSON.stringify(body)`` with the
    keys sorted alphabetically (compact separators, no whitespace), using the
    IPN secret as the key. Compared in constant time, case-insensitive.
    """
    if not received_sig or not ipn_secret:
        return False
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return False
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    computed = hmac_mod.new(
        ipn_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()
    return hmac_mod.compare_digest(computed.lower(), received_sig.lower())
