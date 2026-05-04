"""Worker job: evaluate per-user auto-withdraw configs and create
withdrawal requests when their trigger fires.

Runs every 60s. For each enabled config:
  * skip if the user already has a non-terminal withdrawal in flight.
  * compute whether the trigger is due (time-based or threshold-based).
  * compute the gross amount per the policy (full balance vs fixed amount).
  * create a ``WithdrawalRequest(source=auto)`` via the shared service.
  * update ``last_run_at``/``next_run_at``/``last_withdrawal_id``.

Failures land in :mod:`process_withdrawals` which refunds + notifies. The
auto-config stays enabled so the next cycle retries on schedule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.common.db.models import (
    AutoWithdrawalConfig,
    AutoWithdrawAmountPolicy,
    AutoWithdrawMode,
    WithdrawalSource,
)
from app.common.db.session import SessionLocal
from app.common.db.wallet import get_balance
from app.common.logging import logger
from app.common.payout.bsc import PayoutConfigError, get_payout_client
from app.common.payout.service import (
    WithdrawalError,
    create_withdrawal,
    quote_withdrawal,
)
from app.common.settings import get_settings
from app.common.telegram_bot import send_message


async def auto_withdraw_once() -> None:
    settings = get_settings()
    try:
        client = get_payout_client()
    except PayoutConfigError as e:
        logger.warning("[auto-withdraw] hot wallet not configured: {} — skipping", e)
        return

    now = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        configs = (
            await session.execute(
                select(AutoWithdrawalConfig).where(
                    AutoWithdrawalConfig.enabled.is_(True)
                )
            )
        ).scalars().all()

        for cfg in configs:
            try:
                await _evaluate_config(session, cfg, now, settings, client)
            except Exception:
                logger.exception(
                    "[auto-withdraw] error evaluating user {}", cfg.user_id
                )


async def _evaluate_config(session, cfg: AutoWithdrawalConfig, now, settings, client) -> None:
    # Skip if user has a non-terminal withdrawal in flight (avoid stacking).
    from app.common.db.models import WithdrawalRequest, WithdrawalStatus

    in_flight = (
        await session.execute(
            select(WithdrawalRequest.id)
            .where(
                WithdrawalRequest.user_id == cfg.user_id,
                WithdrawalRequest.status.in_(
                    (
                        WithdrawalStatus.pending,
                        WithdrawalStatus.submitting,
                        WithdrawalStatus.submitted,
                    )
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if in_flight is not None:
        return

    balance = await get_balance(session, cfg.user_id)

    # Trigger evaluation.
    if cfg.mode is AutoWithdrawMode.time:
        if cfg.next_run_at is None or cfg.next_run_at > now:
            return
    else:  # threshold
        if cfg.threshold_usd is None or balance < cfg.threshold_usd:
            return
        # Cooldown: prevent threshold-mode auto-withdraws from firing back-
        # to-back (which would burn fees on tiny payouts the moment the
        # balance crosses the threshold each cycle). Bypassed when the
        # balance is at least 2× the threshold — at that point the user
        # has clearly accumulated meaningful value, so making them wait
        # out the cooldown looks like a bug from their side.
        cooldown = timedelta(
            minutes=int(settings.auto_withdraw_threshold_cooldown_min)
        )
        if (
            cfg.last_run_at is not None
            and (now - cfg.last_run_at) < cooldown
            and balance < cfg.threshold_usd * 2
        ):
            return

    # Amount policy.
    if cfg.amount_policy is AutoWithdrawAmountPolicy.full:
        gross = balance
    else:
        if cfg.fixed_amount_usd is None or balance < cfg.fixed_amount_usd:
            # Fixed amount but insufficient balance — skip until next cycle.
            return
        gross = cfg.fixed_amount_usd

    if gross < settings.withdrawal_min_usd:
        return

    # Live fee check — skip silently if fee >= amount.
    try:
        quote = await quote_withdrawal(gross, client=client)
    except PayoutConfigError as e:
        logger.warning("[auto-withdraw] cannot quote fee: {}", e)
        return
    if quote.net_usdt <= Decimal(0):
        logger.info(
            "[auto-withdraw] user {} skipped — fee {}$ ≥ amount {}$",
            cfg.user_id, quote.fee_usd, gross,
        )
        return

    try:
        wr = await create_withdrawal(
            session,
            user_id=cfg.user_id,
            amount_usd=gross,
            to_address=cfg.to_address,
            source=WithdrawalSource.auto,
            client=client,
        )
    except WithdrawalError as e:
        logger.info("[auto-withdraw] user {} skipped: {}", cfg.user_id, e)
        # No state change; we'll retry next cycle.
        await session.rollback()
        return

    cfg.last_run_at = now
    cfg.last_withdrawal_id = wr.id
    if cfg.mode is AutoWithdrawMode.time and cfg.interval_hours:
        cfg.next_run_at = now + timedelta(hours=int(cfg.interval_hours))

    await session.commit()
    logger.info(
        "[auto-withdraw] user {} → withdrawal #{} amount={} fee={}",
        cfg.user_id, wr.id, wr.amount_usd, wr.fee_usd,
    )
    try:
        await send_message(
            cfg.user_id,
            "🤖 برداشت خودکار فعال شد و درخواست شما در حال پردازش است.\n"
            f"مبلغ: <b>{wr.amount_usd}$</b>\n"
            f"کارمزد: <b>{wr.fee_usd}$</b>\n"
            f"دریافتی: <b>{wr.net_usdt} USDT</b>",
        )
    except Exception:
        logger.exception("[auto-withdraw] failed to notify user {}", cfg.user_id)
