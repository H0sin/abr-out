"""Worker job: process pending USDT-BSC withdrawal requests.

Three stages, all run inside the same APScheduler tick:

1. **Recovery** — any row stuck in ``submitting`` longer than the configured
   recovery window (worker crashed between status update and broadcast) is
   reverted to ``pending``.
2. **Submit** — for each ``pending`` row we *first sign* the tx (yielding a
   deterministic hash), persist that hash + ``submitted`` status, **then**
   broadcast. If the broadcast call itself raises (RPC timeout, ``already
   known`` …) we still leave the row in ``submitted`` because the tx may have
   landed on chain; the next tick's confirmation polling decides the outcome.
3. **Confirm** — for each ``submitted`` row, poll the receipt:
     * ``status==1`` → ``confirmed``.
     * ``status==0`` → ``failed`` + refund (on-chain revert is final).
     * no receipt and no in-mempool tx after the alert window → freeze the
       row at ``submitted`` and log loudly so an admin can investigate.
       We do **not** auto-refund a missing tx because the same nonce may
       still confirm later, which would create a double-spend.

Notifies the user via Telegram on every terminal transition.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.common.db.models import WithdrawalRequest, WithdrawalStatus
from app.common.db.session import SessionLocal
from app.common.db.wallet import refund_failed_withdrawal
from app.common.logging import logger
from app.common.payout.bsc import PayoutConfigError, get_payout_client
from app.common.settings import get_settings
from app.common.telegram_bot import send_message


async def process_withdrawals_once() -> None:
    settings = get_settings()
    try:
        client = get_payout_client()
    except PayoutConfigError as e:
        logger.warning("[withdrawals] hot wallet not configured: {} — skipping", e)
        return

    now = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        # --- (1) recovery: unstick rows abandoned in `submitting` ---
        recovery_cutoff = now - timedelta(
            minutes=settings.withdrawal_submitting_recovery_min
        )
        stuck = (
            await session.execute(
                select(WithdrawalRequest)
                .where(
                    WithdrawalRequest.status == WithdrawalStatus.submitting,
                    WithdrawalRequest.updated_at < recovery_cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        for w in stuck:
            logger.warning(
                "[withdrawals] #{} stuck in 'submitting' since {} — reverting to 'pending'",
                w.id, w.updated_at,
            )
            w.status = WithdrawalStatus.pending
            w.error_msg = "recovered from stuck submitting state"
        if stuck:
            await session.commit()

        # --- (2) submit pending rows ---
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

        # --- (3) confirmation polling ---
        submitted = (
            await session.execute(
                select(WithdrawalRequest)
                .where(WithdrawalRequest.status == WithdrawalStatus.submitted)
                .order_by(WithdrawalRequest.id.asc())
                .limit(50)
            )
        ).scalars().all()
        for w in submitted:
            await _check_receipt(session, w, client, now, settings)


async def _submit(session, w: WithdrawalRequest, client) -> None:
    """Sign first, persist the deterministic hash + ``submitted`` status,
    *then* broadcast. This eliminates the double-spend window that exists
    when broadcast and persistence are not separated: even if the RPC call
    raises after the tx has landed in the mempool, our DB already records
    that the user is on the hook for it.
    """
    # Step A: sign. A failure here is purely local — nothing is on chain,
    # so refunding the user is safe.
    try:
        gas_price = int(w.gas_price_wei) if w.gas_price_wei is not None else None
        signed = await client.sign_transfer(w.to_address, w.net_usdt, gas_price)
    except Exception as exc:
        logger.exception("[withdrawals] #{} sign failed", w.id)
        w.status = WithdrawalStatus.failed
        w.error_msg = f"sign failed: {str(exc)[:400]}"
        await refund_failed_withdrawal(session, w, reason="sign failed")
        await session.commit()
        try:
            await send_message(
                w.user_id,
                "❌ برداشت شما انجام نشد و موجودی به کیف پول بازگشت داده شد.\n"
                f"دلیل: <code>{(str(exc) or 'unknown')[:200]}</code>",
            )
        except Exception:
            logger.exception("[withdrawals] notify-fail user {}", w.user_id)
        return

    # Step B: persist the hash + status BEFORE we hit the network. From this
    # point on we treat the withdrawal as "in flight" regardless of what
    # send_raw_transaction does — _check_receipt is the source of truth.
    w.tx_hash = signed.tx_hash
    w.status = WithdrawalStatus.submitted
    if w.gas_price_wei is None or int(w.gas_price_wei) == 0:
        w.gas_price_wei = Decimal(signed.gas_price_wei)
    await session.commit()

    # Step C: broadcast. ``already known`` / ``nonce too low`` and transient
    # network errors must NOT change state — receipt polling handles them.
    try:
        await client.broadcast_raw(signed.raw_tx)
        logger.info(
            "[withdrawals] #{} broadcast: tx={} amount={} USDT nonce={}",
            w.id, signed.tx_hash, w.net_usdt, signed.nonce,
        )
    except Exception as exc:
        logger.warning(
            "[withdrawals] #{} broadcast raised (tx may still be mined): {}",
            w.id, exc,
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


async def _check_receipt(
    session, w: WithdrawalRequest, client, now: datetime, settings
) -> None:
    if not w.tx_hash:
        return
    receipt = await client.get_receipt(w.tx_hash)
    if receipt is not None:
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
            return
        # Reverted on-chain → refund (this is final; nonce was consumed).
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
        return

    # No receipt yet. If the tx is well past the alert window AND the node
    # no longer knows about it, the broadcast was likely lost. We do NOT
    # auto-refund: the same nonce may still mine later (especially if a
    # second worker rebroadcasts). Freeze the row and alert.
    age = now - (w.updated_at or w.created_at)
    alert_after = timedelta(minutes=settings.withdrawal_submitted_alert_min)
    if age >= alert_after:
        tx = await client.get_transaction(w.tx_hash)
        if tx is None:
            logger.error(
                "[withdrawals] #{} tx={} dropped from mempool after {} — "
                "manual investigation required (no auto-refund to avoid double-spend)",
                w.id, w.tx_hash, age,
            )
            stamp = f"dropped-mempool@{now.isoformat(timespec='minutes')}"
            if w.error_msg != stamp:
                w.error_msg = stamp
                await session.commit()
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
