"""Worker job: process pending USDT-BSC withdrawal requests.

Two stages, both run inside the same APScheduler tick:

1. **Submit** — for each row in ``pending``, broadcast the BEP20 transfer.
   On success move to ``submitted`` and store the tx hash. On exception
   move to ``failed`` and refund the user (idempotent ledger write).
2. **Confirm** — for each row in ``submitted``, poll the receipt. ``status==1``
   → ``confirmed``; ``status==0`` → ``failed`` + refund; otherwise leave it
   for the next tick.

Notifies the user via Telegram on every terminal transition.
"""
from __future__ import annotations

from sqlalchemy import select

from app.common.db.models import WithdrawalRequest, WithdrawalStatus
from app.common.db.session import SessionLocal
from app.common.db.wallet import refund_failed_withdrawal
from app.common.logging import logger
from app.common.payout.bsc import PayoutConfigError, get_payout_client
from app.common.telegram_bot import send_message

# Don't keep retrying on the same row forever — if the on-chain tx is stuck
# pending past this many cycles we just keep polling, but log loudly.
_PENDING_WARN_AFTER_RECEIPT_POLLS = 40  # ~20 minutes at 30s tick


async def process_withdrawals_once() -> None:
    try:
        client = get_payout_client()
    except PayoutConfigError as e:
        logger.warning("[withdrawals] hot wallet not configured: {} — skipping", e)
        return

    async with SessionLocal() as session:
        pending = (
            await session.execute(
                select(WithdrawalRequest)
                .where(WithdrawalRequest.status == WithdrawalStatus.pending)
                .order_by(WithdrawalRequest.id.asc())
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        for w in pending:
            w.status = WithdrawalStatus.submitting
        if pending:
            await session.commit()

        for w in pending:
            await _submit(session, w, client)

        # --- confirmation polling ---
        submitted = (
            await session.execute(
                select(WithdrawalRequest)
                .where(WithdrawalRequest.status == WithdrawalStatus.submitted)
                .order_by(WithdrawalRequest.id.asc())
                .limit(50)
            )
        ).scalars().all()
        for w in submitted:
            await _check_receipt(session, w, client)


async def _submit(session, w: WithdrawalRequest, client) -> None:
    try:
        gas_price = int(w.gas_price_wei) if w.gas_price_wei is not None else None
        tx_hash = await client.send_usdt(w.to_address, w.net_usdt, gas_price)
        w.tx_hash = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
        w.status = WithdrawalStatus.submitted
        await session.commit()
        logger.info(
            "[withdrawals] #{} submitted on-chain: tx={} amount={} USDT",
            w.id, w.tx_hash, w.net_usdt,
        )
        try:
            await send_message(
                w.user_id,
                "🛫 درخواست برداشت شما به شبکه ارسال شد.\n"
                f"مبلغ: <b>{w.net_usdt} USDT</b>\n"
                f"کارمزد: <b>{w.fee_usd}$</b>\n"
                f"تراکنش: <code>{w.tx_hash}</code>",
            )
        except Exception:
            logger.exception("[withdrawals] failed to notify user {}", w.user_id)
    except Exception as exc:
        logger.exception("[withdrawals] #{} send failed", w.id)
        w.status = WithdrawalStatus.failed
        w.error_msg = str(exc)[:500]
        await refund_failed_withdrawal(session, w, reason="on-chain send failed")
        await session.commit()
        try:
            await send_message(
                w.user_id,
                f"❌ برداشت شما انجام نشد و موجودی به کیف پول بازگشت داده شد.\n"
                f"دلیل: <code>{(str(exc) or 'unknown')[:200]}</code>",
            )
        except Exception:
            logger.exception("[withdrawals] notify-fail user {}", w.user_id)


async def _check_receipt(session, w: WithdrawalRequest, client) -> None:
    if not w.tx_hash:
        return
    receipt = await client.get_receipt(w.tx_hash)
    if receipt is None:
        return  # still pending
    status = int(receipt.get("status", 0))
    gas_used = receipt.get("gasUsed")
    if status == 1:
        w.status = WithdrawalStatus.confirmed
        if gas_used is not None:
            w.gas_used = int(gas_used)
        await session.commit()
        logger.info("[withdrawals] #{} confirmed on-chain", w.id)
        try:
            await send_message(
                w.user_id,
                "✅ برداشت شما با موفقیت روی شبکه تأیید شد.\n"
                f"مبلغ ارسالی: <b>{w.net_usdt} USDT</b>\n"
                f"تراکنش: <code>{w.tx_hash}</code>",
            )
        except Exception:
            logger.exception("[withdrawals] notify-confirm user {}", w.user_id)
    else:
        # Reverted on-chain → refund.
        w.status = WithdrawalStatus.failed
        w.error_msg = "on-chain reverted"
        if gas_used is not None:
            w.gas_used = int(gas_used)
        await refund_failed_withdrawal(session, w, reason="on-chain reverted")
        await session.commit()
        try:
            await send_message(
                w.user_id,
                "❌ تراکنش برداشت روی شبکه ناموفق بود؛ موجودی شما بازگشت داده شد.",
            )
        except Exception:
            logger.exception("[withdrawals] notify-revert user {}", w.user_id)
