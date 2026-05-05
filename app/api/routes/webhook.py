from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

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
from app.common.payment.plisio import verify_plisio_signature
from app.common.settings import get_settings
from app.common.telegram_bot import send_message

router = APIRouter(prefix="/webhook", tags=["webhook"])

# Minimum USD amount that an underpayment must reach to be auto-credited.
# Anything below this is treated as dust / failed conversion and the intent is
# marked failed instead. Customers paying the exact invoice are unaffected;
# this only gates the partial/underpaid recovery path.
_PARTIAL_MIN_CREDIT_USD = Decimal("0.5")


def _quantize_usd(value: Decimal) -> Decimal:
    """Round a USD amount to 2 decimals (banker-safe HALF_UP)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _clamp_credit(usd: Decimal, invoice_amount: Decimal) -> Decimal:
    """Never credit more than the invoice (guards against rate drift /
    over-payment) and never less than zero."""
    if usd < 0:
        return Decimal("0")
    if usd > invoice_amount:
        return invoice_amount
    return usd


# NowPayments payment_status values:
#   waiting, confirming, confirmed, sending  -> in-flight, no DB change
#   finished                                 -> credit wallet
#   partially_paid                           -> auto-credit the actually-paid
#                                               USD amount (computed from
#                                               actually_paid / pay_amount)
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
        select(PaymentIntent)
        .where(
            PaymentIntent.external_ref == order_id,
            PaymentIntent.gateway == PaymentGateway.nowpayments,
        )
        .with_for_update()
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
        # Compute USD-equivalent of what actually arrived. NowPayments doesn't
        # echo a USD figure for partial payments, only crypto amounts, so we
        # back-calculate via the invoice rate that was locked at create-time:
        #     credited_usd = actually_paid / pay_amount * price_amount
        actually_paid = payload.get("actually_paid")
        pay_amount = payload.get("pay_amount")
        invoice_usd = (
            Decimal(str(price_amount)) if price_amount is not None else intent.amount
        )
        credited: Decimal | None = None
        try:
            if actually_paid is not None and pay_amount is not None:
                ap = Decimal(str(actually_paid))
                pm = Decimal(str(pay_amount))
                if pm > 0 and ap > 0:
                    credited = _clamp_credit(
                        _quantize_usd(ap / pm * invoice_usd), intent.amount
                    )
        except Exception as exc:
            logger.warning(
                "NowPayments IPN: failed to compute partial credit for order={}: {}",
                order_id, exc,
            )

        if credited is None or credited < _PARTIAL_MIN_CREDIT_USD:
            # Dust / no usable amount: treat as failure.
            logger.warning(
                "NowPayments IPN: partially_paid order={} dust (actually_paid={} "
                "pay_amount={} computed_usd={}), marking failed",
                order_id, actually_paid, pay_amount, credited,
            )
            intent.status = PaymentStatus.failed
            await session.commit()
            await send_message(
                intent.user_id,
                f"❌ پرداخت شما به‌صورت ناقص و کمتر از حداقل قابل تأیید دریافت شد.\n"
                f"لطفاً برای پیگیری با پشتیبانی تماس بگیرید.\n"
                f"🔢 کد پیگیری: <code>{order_id}</code>",
            )
        else:
            logger.warning(
                "NowPayments IPN: partially_paid order={} crediting {} USD of "
                "invoiced {} USD (actually_paid={} pay_amount={})",
                order_id, credited, intent.amount, actually_paid, pay_amount,
            )
            wallet_tx = WalletTransaction(
                user_id=intent.user_id,
                amount=credited,
                currency=intent.currency or "USD",
                type=TxnType.topup,
                ref=order_id,
                idempotency_key=f"nowpayments-{order_id}",
            )
            session.add(wallet_tx)
            # Reflect the actually-received amount on the intent itself, so
            # the payment record matches what the customer paid (e.g. 9.5
            # instead of the originally-invoiced 10).
            intent.amount = credited
            intent.status = PaymentStatus.confirmed
            await session.commit()
            await send_message(
                intent.user_id,
                f"✅ موجودی شما <b>{credited}$</b> شارژ شد.\n"
                f"⚠️ مبلغ فاکتور به مبلغ واریزی شما اصلاح شد.\n"
                f"🔢 کد پیگیری: <code>{order_id}</code>",
            )

    # else: in-flight states (waiting/confirming/confirmed/sending) → no-op.

    return Response(status_code=200)


# Plisio invoice callback statuses (per documentation):
#   new, pending, pending internal       -> in-flight, no DB change
#   completed                            -> credit wallet
#   expired                              -> docs: "look for the 'amount' field
#                                          to verify payment. The full amount
#                                          may not have been paid." Plisio has
#                                          no dedicated underpaid status — it
#                                          surfaces here as expired+amount>0.
#   error, cancelled                     -> mark failed, notify
#   cancelled duplicate                  -> no-op (a sibling invoice succeeded)
_PLISIO_SUCCESS = {"completed"}
_PLISIO_FAILURE = {"error", "cancelled"}
_PLISIO_EXPIRED = {"expired"}
_PLISIO_DUPLICATE = {"cancelled duplicate", "cancelled_duplicate"}


@router.post("/plisio")
async def plisio_ipn(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """
    Plisio IPN callback (with ``?json=true``). Verifies the HMAC-SHA1
    ``verify_hash`` over the JSON body, then updates the corresponding
    ``PaymentIntent`` and credits the wallet on success. Idempotent.
    """
    raw = await request.body()
    settings = get_settings()

    if not verify_plisio_signature(raw, settings.plisio_secret_key):
        logger.warning("Plisio IPN: signature verification failed")
        return Response(status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    order_id: str = str(payload.get("order_number") or "")
    invoice_status: str = str(payload.get("status") or "").lower()
    source_amount = payload.get("source_amount")

    if not order_id:
        logger.warning("Plisio IPN: missing order_number")
        return Response(status_code=200)

    result = await session.execute(
        select(PaymentIntent)
        .where(
            PaymentIntent.external_ref == order_id,
            PaymentIntent.gateway == PaymentGateway.plisio,
        )
        .with_for_update()
    )
    intent = result.scalar_one_or_none()
    if intent is None:
        logger.warning("Plisio IPN: unknown order_number={}", order_id)
        return Response(status_code=200)

    if intent.status != PaymentStatus.pending:
        logger.info(
            "Plisio IPN: order={} already in status {}, skipping",
            order_id, intent.status,
        )
        return Response(status_code=200)

    if invoice_status in _PLISIO_SUCCESS:
        credit_amount: Decimal = intent.amount
        if source_amount is not None:
            try:
                echoed = Decimal(str(source_amount))
                if abs(echoed - intent.amount) > Decimal("0.01"):
                    logger.warning(
                        "Plisio IPN: source_amount={} differs from intent={} for order={}",
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
            idempotency_key=f"plisio-{order_id}",
        )
        session.add(wallet_tx)
        intent.status = PaymentStatus.confirmed
        await session.commit()
        logger.info(
            "Plisio: topped up {} USD for user {}", credit_amount, intent.user_id
        )
        await send_message(
            intent.user_id,
            f"✅ موجودی شما <b>{credit_amount}$</b> شارژ شد.\n"
            f"🔢 کد پیگیری: <code>{order_id}</code>",
        )

    elif invoice_status in _PLISIO_FAILURE:
        intent.status = PaymentStatus.failed
        await session.commit()
        await send_message(
            intent.user_id,
            f"❌ پرداخت شما انجام نشد یا منقضی شد.\n"
            f"🔢 کد پیگیری: <code>{order_id}</code>",
        )

    elif invoice_status in _PLISIO_EXPIRED:
        # Underpaid invoices arrive here with a non-zero ``amount`` field
        # (received crypto). True expiries have amount==0 / missing.
        amount_crypto_raw = payload.get("amount")
        source_rate_raw = payload.get("source_rate")
        invoice_total_raw = payload.get("invoice_total_sum")

        credited: Decimal | None = None
        try:
            amount_crypto = (
                Decimal(str(amount_crypto_raw)) if amount_crypto_raw is not None else Decimal("0")
            )
            if amount_crypto > 0:
                if source_rate_raw is not None:
                    rate = Decimal(str(source_rate_raw))
                    if rate > 0:
                        credited = _clamp_credit(
                            _quantize_usd(amount_crypto * rate), intent.amount
                        )
                # Fallback when source_rate is absent: derive ratio from the
                # original invoice (source_amount corresponds to invoice_total_sum).
                if credited is None and invoice_total_raw is not None and source_amount is not None:
                    inv_total = Decimal(str(invoice_total_raw))
                    src_total = Decimal(str(source_amount))
                    if inv_total > 0 and src_total > 0:
                        credited = _clamp_credit(
                            _quantize_usd(src_total * amount_crypto / inv_total),
                            intent.amount,
                        )
        except Exception as exc:
            logger.warning(
                "Plisio IPN: failed to compute partial credit for order={}: {}",
                order_id, exc,
            )

        if credited is None or credited < _PARTIAL_MIN_CREDIT_USD:
            # True expiry (no payment) or dust: mark failed.
            logger.info(
                "Plisio IPN: expired order={} (amount={} source_rate={} "
                "invoice_total_sum={} computed_usd={}), marking failed",
                order_id, amount_crypto_raw, source_rate_raw,
                invoice_total_raw, credited,
            )
            intent.status = PaymentStatus.failed
            await session.commit()
            await send_message(
                intent.user_id,
                f"❌ پرداخت شما انجام نشد یا منقضی شد.\n"
                f"🔢 کد پیگیری: <code>{order_id}</code>",
            )
        else:
            logger.warning(
                "Plisio IPN: underpaid expired order={} crediting {} USD of "
                "invoiced {} USD (amount={} {} source_rate={})",
                order_id, credited, intent.amount, amount_crypto_raw,
                payload.get("currency"), source_rate_raw,
            )
            wallet_tx = WalletTransaction(
                user_id=intent.user_id,
                amount=credited,
                currency=intent.currency or "USD",
                type=TxnType.topup,
                ref=order_id,
                idempotency_key=f"plisio-{order_id}",
            )
            session.add(wallet_tx)
            # Reflect the actually-received amount on the intent itself, so
            # the payment record matches what the customer paid (e.g. 9.5
            # instead of the originally-invoiced 10).
            intent.amount = credited
            intent.status = PaymentStatus.confirmed
            await session.commit()
            await send_message(
                intent.user_id,
                f"✅ موجودی شما <b>{credited}$</b> شارژ شد.\n"
                f"⚠️ مبلغ فاکتور به مبلغ واریزی شما اصلاح شد.\n"
                f"🔢 کد پیگیری: <code>{order_id}</code>",
            )

    elif invoice_status in _PLISIO_DUPLICATE:
        # The user switched cryptocurrencies; a sibling invoice will (or did)
        # carry the actual payment. Leave this intent pending — the success
        # callback for the sibling order_number handles credit.
        logger.info("Plisio IPN: order={} cancelled-duplicate, leaving pending", order_id)

    # else: in-flight states (new/pending/pending internal) → no-op.

    return Response(status_code=200)
