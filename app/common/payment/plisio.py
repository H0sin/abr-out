from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import uuid
from decimal import Decimal

import httpx

from app.common.logging import logger
from app.common.settings import get_settings

PLISIO_BASE = "https://api.plisio.net/api/v1"


def gen_order_id() -> str:
    return f"PL-{uuid.uuid4().hex[:12]}"


async def create_invoice(amount_usd: Decimal, order_id: str, user_id: int) -> dict:
    """
    Create a Plisio hosted invoice and return order info + payment URL.
    """
    settings = get_settings()

    if not settings.plisio_secret_key:
        raise RuntimeError("PLISIO_SECRET_KEY is not configured")

    base = settings.public_base_url
    if not base:
        raise RuntimeError("DOMAIN (or WEBHOOK_BASE_URL) is not configured")
    # ``json=true`` is required by Plisio so callbacks come as JSON (the
    # default is form-encoded and uses PHP's serialize() for the signed
    # payload, which is awkward to verify outside PHP).
    callback_url = f"{base}/webhook/plisio?json=true"

    return_url = (
        f"https://t.me/{settings.bot_username}?start=topup_done"
        if settings.bot_username
        else None
    )

    params: dict[str, str] = {
        "source_currency": "USD",
        "source_amount": str(amount_usd),
        "order_number": order_id,
        "order_name": f"Top-up {amount_usd}$ for user {user_id}",
        "callback_url": callback_url,
        "api_key": settings.plisio_secret_key,
        # Pre-filling ``email`` skips the "Enter your e-mail" step on the
        # hosted invoice page so the buyer goes straight to the payment
        # screen. We don't have a real email for Telegram users, so synth
        # a stable per-user one.
        "email": f"user{user_id}@telegram.local",
    }
    # Extend the hosted-invoice payment window beyond Plisio's 60-minute
    # default. Many users top up from an exchange withdrawal, which can stall
    # in KYC/manual review for hours; without this the invoice expires before
    # funds arrive and we'd have to refund manually.
    expire_min = max(1, min(2880, int(settings.invoice_expire_min)))
    params["expire_min"] = str(expire_min)
    if return_url:
        params["success_invoice_url"] = return_url
        params["fail_invoice_url"] = return_url

    logger.info(
        "[Plisio] creating invoice order={} amount_usd={} user={}",
        order_id, amount_usd, user_id,
    )

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{PLISIO_BASE}/invoices/new", params=params)
        r.raise_for_status()
        result = r.json()

    if result.get("status") != "success":
        raise RuntimeError(f"Plisio create-invoice failed: {result}")

    data = result.get("data") or {}
    pay_url = data.get("invoice_url")
    invoice_id = str(data.get("txn_id") or "")
    if not pay_url:
        raise RuntimeError(f"Plisio did not return invoice_url: {result}")

    return {
        "order_id": order_id,
        "invoice_id": invoice_id,
        "pay_url": pay_url,
        "amount_usd": amount_usd,
    }


def verify_plisio_signature(raw_body: bytes, secret: str) -> bool:
    """
    Verify a Plisio JSON callback (sent when ``callback_url`` includes
    ``?json=true``). The scheme — per Plisio's Node.js example — is:

      hmac_sha1(secret, JSON.stringify({...payload, verify_hash removed}))

    where the JSON serialization preserves the key order from the original
    payload. Python ``dict`` preserves insertion order (3.7+), so a normal
    ``json.loads`` is sufficient and also handles nested objects/arrays
    correctly (an earlier version used ``object_pairs_hook=list`` which only
    preserved order at the top level and silently corrupted nested objects).
    """
    if not secret:
        return False
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False

    received_sig = str(data.pop("verify_hash", "") or "")
    if not received_sig:
        return False

    canonical = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    computed = hmac_mod.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return hmac_mod.compare_digest(computed.lower(), received_sig.lower())
