from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.models import (
    PaymentGateway,
    PaymentIntent,
    PaymentStatus,
    TxnType,
    WalletTransaction,
)
from app.common.db.session import get_session
from app.common.logging import logger
from app.common.payment.nowpayments import verify_nowpayments_signature
from app.common.settings import get_settings
from app.common.telegram_bot import send_message

router = APIRouter(prefix="/webhook", tags=["webhook"])


# NowPayments payment_status values:
#   waiting, confirming, confirmed, sending  -> in-flight, no DB change
#   finished                                 -> credit wallet
#   partially_paid                           -> notify, do NOT auto-credit
#   failed, refunded, expired                -> mark failed, notify
_FINAL_SUCCESS = {"finished"}
_FINAL_FAILURE = {"failed", "refunded", "expired"}
_PARTIAL = {"partially_paid"}


@router.post("/nowpayments")
async def nowpayments_ipn(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """
    NowPayments IPN callback. Verifies the ``x-nowpayments-sig`` HMAC-SHA512
    signature, then updates the corresponding ``PaymentIntent`` and credits
    the user's wallet on success. Idempotent.
    """
    raw = await request.body()
    settings = get_settings()
    received_sig = request.headers.get("x-nowpayments-sig", "")

    if not verify_nowpayments_signature(raw, received_sig, settings.nowpayments_ipn_secret):
        logger.warning("NowPayments IPN: signature verification failed")
        return Response(status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    order_id: str = str(payload.get("order_id") or "")
    payment_status: str = str(payload.get("payment_status") or "").lower()
    price_amount = payload.get("price_amount")

    if not order_id:
        logger.warning("NowPayments IPN: missing order_id")
        return Response(status_code=200)

    result = await session.execute(
        select(PaymentIntent).where(
            PaymentIntent.external_ref == order_id,
            PaymentIntent.gateway == PaymentGateway.nowpayments,
        )
    )
    intent = result.scalar_one_or_none()
    if intent is None:
        logger.warning("NowPayments IPN: unknown order_id={}", order_id)
        return Response(status_code=200)

    # Idempotency: skip already-finalised intents.
    if intent.status != PaymentStatus.pending:
        logger.info(
            "NowPayments IPN: order={} already in status {}, skipping",
            order_id, intent.status,
        )
        return Response(status_code=200)

    if payment_status in _FINAL_SUCCESS:
        # Prefer the original price_amount on the intent (USD) over
        # whatever NowPayments echoes back, to avoid rate-conversion drift.
        credit_amount: Decimal = intent.amount
        if price_amount is not None:
            try:
                echoed = Decimal(str(price_amount))
                # If the echoed amount differs by more than a cent, log it.
                if abs(echoed - intent.amount) > Decimal("0.01"):
                    logger.warning(
                        "NowPayments IPN: price_amount={} differs from intent={} for order={}",
                        echoed, intent.amount, order_id,
                    )
            except Exception:
                pass

        wallet_tx = WalletTransaction(
            user_id=intent.user_id,
            amount=credit_amount,
            currency=intent.currency or "USD",
            type=TxnType.topup,
            ref=order_id,
            idempotency_key=f"nowpayments-{order_id}",
        )
        session.add(wallet_tx)
        intent.status = PaymentStatus.confirmed
        await session.commit()
        logger.info(
            "NowPayments: topped up {} USD for user {}", credit_amount, intent.user_id
        )
        await send_message(
            intent.user_id,
            f"✅ موجودی شما <b>{credit_amount}$</b> شارژ شد.\n"
            f"🔢 کد پیگیری: <code>{order_id}</code>",
        )

    elif payment_status in _FINAL_FAILURE:
        intent.status = PaymentStatus.failed
        await session.commit()
        await send_message(
            intent.user_id,
            f"❌ پرداخت شما انجام نشد یا منقضی شد.\n"
            f"🔢 کد پیگیری: <code>{order_id}</code>",
        )

    elif payment_status in _PARTIAL:
        # Keep intent pending; let admin resolve manually.
        await send_message(
            intent.user_id,
            f"⚠️ پرداخت شما به‌صورت ناقص دریافت شد.\n"
            f"لطفاً برای پیگیری با پشتیبانی تماس بگیرید.\n"
            f"🔢 کد پیگیری: <code>{order_id}</code>",
        )

    # else: in-flight states (waiting/confirming/confirmed/sending) → no-op.

    return Response(status_code=200)
