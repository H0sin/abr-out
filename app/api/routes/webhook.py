from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.session import get_session
from app.common.db.models import SwapWalletTx, SwapWalletTxStatus, TxnType, WalletTransaction
from app.common.logging import logger
from app.common.payment.swapwallet import verify_swapwallet_hmac
from app.common.settings import get_settings
from app.common.telegram_bot import send_message

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/nowpayments")
async def nowpayments_ipn() -> dict[str, str]:
    # TODO: HMAC verify, upsert payment_intent, insert wallet_transaction.
    return {"status": "todo"}


@router.post("/swapwallet")
async def swapwallet_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """
    Receive payment state updates from SwapWallet.
    Always returns HTTP 200 (except invalid HMAC → 401) to prevent retries.
    """
    raw = await request.body()

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    settings = get_settings()
    received_hmac = payload.get("hmac", "")

    if not verify_swapwallet_hmac(raw, received_hmac, settings.swapwallet_api_key):
        logger.warning("SwapWallet callback: HMAC verification failed")
        return Response(status_code=401)

    invoice = payload.get("event", {}).get("invoice", {})
    order_id: str = invoice.get("orderId", "")
    status: str = invoice.get("status", "").upper()

    if not order_id:
        return Response(status_code=200)

    tx = await session.get(SwapWalletTx, order_id)
    if tx is None:
        logger.warning("SwapWallet callback: unknown order_id={}", order_id)
        return Response(status_code=200)

    # Idempotency: skip already-processed transactions
    if tx.status != SwapWalletTxStatus.pending:
        return Response(status_code=200)

    if status in ("PAID", "COMPLETED"):
        wallet_tx = WalletTransaction(
            user_id=tx.user_id,
            amount=tx.amount_usd,
            currency="USD",
            type=TxnType.topup,
            ref=tx.order_id,
            idempotency_key=f"swapwallet-{tx.order_id}",
        )
        session.add(wallet_tx)
        tx.status = SwapWalletTxStatus.paid
        await session.commit()
        logger.info(
            "SwapWallet: topped up {} USD for user {}", tx.amount_usd, tx.user_id
        )
        await send_message(
            tx.user_id,
            f"✅ موجودی شما <b>{tx.amount_usd}$</b> شارژ شد.\n"
            f"🔢 کد پیگیری: <code>{tx.order_id}</code>",
        )

    elif status in ("CANCELLED", "EXPIRED"):
        tx.status = SwapWalletTxStatus.cancelled
        await session.commit()
        await send_message(
            tx.user_id,
            f"❌ پرداخت شما لغو یا منقضی شد.\n"
            f"🔢 کد پیگیری: <code>{tx.order_id}</code>",
        )

    elif status == "ERROR":
        tx.status = SwapWalletTxStatus.failed
        await session.commit()
        await send_message(
            tx.user_id,
            f"⚠️ پرداخت شما با خطا مواجه شد.\n"
            f"🔢 کد پیگیری: <code>{tx.order_id}</code>",
        )

    return Response(status_code=200)

