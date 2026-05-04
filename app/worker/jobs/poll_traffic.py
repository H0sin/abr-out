"""Per-cycle traffic polling and billing.

Algorithm (read → reset → DB):

1. ``cycle_id = uuid4()`` is generated for the run.
2. For each active listing with a provisioned inbound, we fetch one
   :class:`InboundSnapshot` from the panel — that single round-trip carries
   both the inbound-level totals (``up`` / ``down``) used to bill the seller
   and the per-client ``clientStats[]`` used to bill each buyer.
3. **Immediately** after the read we ask the panel to reset client counters
   (``resetAllClientTraffics/{inbound_id}``). Keeping the read→reset gap as
   small as possible minimises traffic that flows in between and would
   otherwise be lost from billing.
4. Then we open a DB transaction and "with leisure" insert all usage rows
   and signed wallet entries.

Anchor maintenance, decided by the just-observed ``reset_succeeded``:

* Reset succeeded → ``Config.last_snapshot_bytes = 0`` (panel is at zero
  now, so next cycle's reading is the next cycle's full delta).
* Reset failed (or disabled) → ``Config.last_snapshot_bytes = current_total``
  (panel still has the just-observed bytes, so next cycle bills via
  ``next_total - current_total`` and we don't double-count).

All wallet rows use ``idempotency_key = poll:{cycle_id}:{kind}:{entity_id}``
which is ``UNIQUE`` on ``wallet_transactions``, so re-running a partially
applied cycle is safe.

Errors are isolated per inbound — a panel failure on listing A does not
prevent listings B..N from being polled.

Known trade-off: if the DB write fails *after* a successful panel reset,
that cycle's traffic is lost. The snapshot is still in memory at that point
so the failure is logged loudly with the raw byte counts; an operator can
recover via a manual ``adjustment`` wallet entry. We accept this in
exchange for the smallest possible read→reset gap.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.common.db.models import (
    Config,
    ConfigStatus,
    ConfigUsage,
    Listing,
    ListingStatus,
    OutboundUsage,
    TxnType,
    WalletTransaction,
)
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.panel.xui_client import (
    InboundSnapshot,
    XuiClient,
    XuiError,
)
from app.common.settings import get_settings

ZERO = Decimal("0")
_BYTES_PER_GB = Decimal(1024**3)
# Minimum delta (in bytes) to record a usage row / wallet transaction.
# Anything smaller than this is forgiven — keeps the DB free of noise rows
# from idle clients, keep-alives, and rounding artifacts.
_MIN_BILLABLE_BYTES = 512 * 1024  # 0.5 MiB


def _q_usd(x: Decimal) -> Decimal:
    """Quantise to wallet precision (8 fractional digits)."""
    return x.quantize(Decimal("0.00000001"))


def _q_gb(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.0000000001"))


async def _refresh_listing_sales_totals(
    session: AsyncSession,
    listing_ids: list[int],
) -> None:
    if not listing_ids:
        return

    unique_listing_ids = list(dict.fromkeys(listing_ids))
    total_rows = (
        await session.execute(
            select(
                OutboundUsage.listing_id,
                func.coalesce(func.sum(OutboundUsage.gb), ZERO).label("total_gb"),
            )
            .where(OutboundUsage.listing_id.in_(unique_listing_ids))
            .group_by(OutboundUsage.listing_id)
        )
    ).all()
    total_by_listing = {
        int(listing_id): total_gb or ZERO for listing_id, total_gb in total_rows
    }

    for listing_id in unique_listing_ids:
        await session.execute(
            update(Listing)
            .where(Listing.id == listing_id)
            .values(total_gb_sold=total_by_listing.get(listing_id, ZERO))
        )


async def _bill_inbound(
    session: AsyncSession,
    *,
    listing: Listing,
    snap: InboundSnapshot,
    cycle_id: uuid.UUID,
    sampled_at: datetime,
    commission_pct: Decimal,
    reset_attempted: bool,
    reset_succeeded: bool,
) -> bool:
    """Insert per-cycle usage rows and signed wallet entries.

    Two-tier billing:

    * **Seller** is credited once per cycle from the inbound-level delta
      (``snap.up + snap.down`` minus the listing's outbound anchors).
      One :class:`OutboundUsage` row + one ``usage_credit``
      ``WalletTransaction`` (``poll:{cycle_id}:outbound:{listing_id}``).
    * **Buyer** is debited per-config from the per-client delta. One
      :class:`ConfigUsage` row + one ``usage_debit`` ``WalletTransaction``
      (``poll:{cycle_id}:debit:{cfg_id}``) per config that moved bytes.
      Buyer pays ``gb × price × (1 + commission_pct)``; the surcharge
      difference is implicit platform profit (not booked).

    Returns ``True`` if any usage row was inserted.
    """
    # --- inbound-level (seller credit) ---
    current_inbound_total = snap.up + snap.down
    prev_inbound_total = (
        listing.last_outbound_up_bytes + listing.last_outbound_down_bytes
    )
    outbound_delta = current_inbound_total - prev_inbound_total
    if outbound_delta < 0:
        logger.warning(
            "[poll] listing={} panel-side inbound reset detected "
            "(prev={}, now={}); treating delta=current",
            listing.id,
            prev_inbound_total,
            current_inbound_total,
        )
        outbound_delta = current_inbound_total

    had_traffic = False
    if outbound_delta >= _MIN_BILLABLE_BYTES:
        had_traffic = True
        gb = _q_gb(Decimal(outbound_delta) / _BYTES_PER_GB)
        seller_credit = _q_usd(gb * listing.price_per_gb_usd)
        # Split the delta proportionally to the panel reading for audit.
        if current_inbound_total > 0:
            d_up = int(round(outbound_delta * (snap.up / current_inbound_total)))
            d_down = outbound_delta - d_up
        else:
            d_up = 0
            d_down = 0
        session.add(
            OutboundUsage(
                listing_id=listing.id,
                seller_user_id=listing.seller_user_id,
                panel_inbound_id=listing.panel_inbound_id,
                cycle_id=cycle_id,
                delta_up_bytes=d_up,
                delta_down_bytes=d_down,
                delta_total_bytes=outbound_delta,
                gb=gb,
                seller_credit_usd=seller_credit,
                panel_total_up_bytes=snap.up,
                panel_total_down_bytes=snap.down,
                reset_attempted=reset_attempted,
                reset_succeeded=reset_succeeded,
                sampled_at=sampled_at,
            )
        )
        if seller_credit > 0:
            session.add(
                WalletTransaction(
                    user_id=listing.seller_user_id,
                    amount=seller_credit,
                    type=TxnType.usage_credit,
                    ref=f"listing:{listing.id}",
                    note=(
                        f"outbound {outbound_delta}B @ "
                        f"{listing.price_per_gb_usd}/GB"
                    ),
                    idempotency_key=f"poll:{cycle_id}:outbound:{listing.id}",
                )
            )

    # Advance the inbound anchors. If the global resetAllTraffics succeeds
    # at the end of the cycle, poll_traffic_once() will bulk-zero these.
    listing.last_outbound_up_bytes = snap.up
    listing.last_outbound_down_bytes = snap.down

    # --- per-config (buyer debit only) ---
    cfg_rows = (
        await session.execute(
            select(Config).where(
                Config.listing_id == listing.id,
                Config.status == ConfigStatus.active,
            )
        )
    ).scalars().all()
    by_email: dict[str, Config] = {c.panel_client_email: c for c in cfg_rows}

    for ct in snap.clients:
        cfg = by_email.get(ct.email)
        if cfg is None:
            logger.debug(
                "[poll] listing={} skip unknown panel client email={!r}",
                listing.id,
                ct.email,
            )
            continue
        current_total = ct.up + ct.down
        delta = current_total - cfg.last_snapshot_bytes
        if delta < 0:
            logger.warning(
                "[poll] config={} panel-side reset detected "
                "(prev={}, now={}); treating delta=current",
                cfg.id,
                cfg.last_snapshot_bytes,
                current_total,
            )
            delta = current_total
        if delta >= _MIN_BILLABLE_BYTES:
            had_traffic = True
            gb = _q_gb(Decimal(delta) / _BYTES_PER_GB)
            buyer_debit = _q_usd(
                gb * listing.price_per_gb_usd * (Decimal("1") + commission_pct)
            )
            # Split the delta proportionally to the panel reading for audit.
            if current_total > 0:
                d_up = int(round(delta * (ct.up / current_total)))
                d_down = delta - d_up
            else:
                d_up = 0
                d_down = 0
            session.add(
                ConfigUsage(
                    config_id=cfg.id,
                    listing_id=listing.id,
                    buyer_user_id=cfg.buyer_user_id,
                    seller_user_id=listing.seller_user_id,
                    cycle_id=cycle_id,
                    delta_up_bytes=d_up,
                    delta_down_bytes=d_down,
                    delta_total_bytes=delta,
                    gb=gb,
                    buyer_debit_usd=buyer_debit,
                    seller_credit_usd=ZERO,
                    panel_email=ct.email,
                    reset_attempted=reset_attempted,
                    reset_succeeded=reset_succeeded,
                    sampled_at=sampled_at,
                )
            )
            if buyer_debit > 0:
                # توضیح: فقط شماره اوتباند (بدون هیچ اثری از فروشنده)
                final_price = listing.price_per_gb_usd * (Decimal("1") + commission_pct)
                commission_percent = int(commission_pct * 100)
                session.add(
                    WalletTransaction(
                        user_id=cfg.buyer_user_id,
                        amount=-buyer_debit,
                        type=TxnType.usage_debit,
                        ref=f"config:{cfg.id}",
                        note=(
                            f"بابت اوت‌باند #{listing.id} با قیمت {final_price:.2f} دلار (قیمت فروشنده + کارمزد {commission_percent} درصد)"
                        ),
                        idempotency_key=f"poll:{cycle_id}:debit:{cfg.id}",
                    )
                )

        # Update anchor regardless of whether delta > 0:
        # - client reset succeeded => panel at 0, anchor = 0
        # - client reset failed/skipped => anchor = current panel total so
        #   next cycle bills via diff (next_total - current_total) and
        #   avoids double-count.
        cfg.last_snapshot_bytes = 0 if reset_succeeded else current_total

    return had_traffic


async def _process_listing(
    xui: XuiClient,
    *,
    listing_id: int,
    cycle_id: uuid.UUID,
    sampled_at: datetime,
    commission_pct: Decimal,
    reset_enabled: bool,
) -> dict[str, Any]:
    """Run one (read → reset → DB) sub-cycle for a single listing."""
    stats: dict[str, Any] = {
        "listing_id": listing_id,
        "ok": False,
        "had_traffic": False,
        "reset_attempted": False,
        "reset_succeeded": False,
        "error": None,
    }

    # --- 1. read snapshot ---
    inbound_id: int | None = None
    snap: InboundSnapshot | None = None
    try:
        async with SessionLocal() as s0:
            listing = await s0.get(Listing, listing_id)
        if listing is None or listing.panel_inbound_id is None:
            stats["error"] = "listing missing or unprovisioned"
            return stats
        inbound_id = listing.panel_inbound_id
        snap = await xui.get_inbound_snapshot(inbound_id)
    except XuiError as e:
        stats["error"] = f"read failed: {e!r}"
        logger.exception("[poll] listing={} panel read failed", listing_id)
        return stats
    except Exception as e:  # noqa: BLE001
        stats["error"] = f"read failed: {e!r}"
        logger.exception("[poll] listing={} read failed", listing_id)
        return stats

    # --- 2. reset panel IMMEDIATELY (before any DB work) ---
    reset_attempted = False
    reset_succeeded = False
    if reset_enabled and snap.clients:
        reset_attempted = True
        try:
            await xui.reset_inbound_clients_traffic(inbound_id)
            reset_succeeded = True
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[poll] listing={} panel reset failed; will diff next cycle: {!r}",
                listing_id,
                e,
            )
            stats["error"] = f"reset failed: {e!r}"
    stats["reset_attempted"] = reset_attempted
    stats["reset_succeeded"] = reset_succeeded

    # --- 3. DB transaction: bill at leisure ---
    try:
        async with SessionLocal() as session:
            listing = await session.get(
                Listing, listing_id, with_for_update=True
            )
            if listing is None:
                stats["error"] = "listing vanished mid-cycle"
                logger.error(
                    "[poll] listing={} vanished after reset_succeeded={}; "
                    "snapshot lost: up={} down={} clients={}",
                    listing_id,
                    reset_succeeded,
                    snap.up,
                    snap.down,
                    [(c.email, c.up + c.down) for c in snap.clients],
                )
                return stats
            had_traffic = await _bill_inbound(
                session,
                listing=listing,
                snap=snap,
                cycle_id=cycle_id,
                sampled_at=sampled_at,
                commission_pct=commission_pct,
                reset_attempted=reset_attempted,
                reset_succeeded=reset_succeeded,
            )
            await session.commit()
            stats["had_traffic"] = had_traffic
    except IntegrityError as e:
        # Most likely an idempotency_key collision from a duplicate run of
        # the same cycle_id — safe to treat as already-applied.
        logger.warning(
            "[poll] listing={} duplicate cycle (already applied): {}",
            listing_id,
            e,
        )
        stats["ok"] = True
        return stats
    except Exception as e:  # noqa: BLE001
        stats["error"] = f"bill failed: {e!r}"
        logger.error(
            "[poll] listing={} BILLING FAILED after reset_succeeded={}; "
            "snapshot up={} down={} clients={} — manual adjustment may be needed: {!r}",
            listing_id,
            reset_succeeded,
            snap.up,
            snap.down,
            [(c.email, c.up + c.down) for c in snap.clients],
            e,
        )
        return stats

    stats["ok"] = True
    return stats


async def poll_traffic_once() -> None:
    """One full poll cycle across every provisioned (non-deleted) listing.

    We deliberately do NOT filter by ``status == active``: a listing that's
    been demoted to ``broken`` or ``disabled`` may still be carrying traffic
    on the panel (the prober's view of health is not the panel's view of
    traffic). Skipping them would mean buyers stop being charged and the
    seller stops being credited even though bytes are flowing — a money
    leak in both directions. So we bill every provisioned listing and let
    zero-delta cycles be no-ops.
    """
    settings = get_settings()
    cycle_id = uuid.uuid4()
    sampled_at = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        # Bill EVERY non-deleted, provisioned listing — regardless of status.
        # A ``broken`` (or even ``disabled``) listing might still be passing
        # traffic on the panel; if we filtered by status==active we'd silently
        # drop those bytes (buyer not debited, seller not credited). We poll
        # them all and let the panel reading drive billing — listings that
        # really aren't moving traffic just produce a zero-delta no-op.
        rows = (
            await session.execute(
                select(Listing.id)
                .where(
                    Listing.status != ListingStatus.deleted,
                    Listing.panel_inbound_id.is_not(None),
                )
                .order_by(Listing.id)
            )
        ).all()
    listing_ids = [r[0] for r in rows]

    if not listing_ids:
        logger.debug("[poll] cycle={} no provisioned listings", cycle_id)
        return

    logger.info(
        "[poll] cycle={} listings={} reset_enabled={}",
        cycle_id,
        len(listing_ids),
        settings.traffic_reset_enabled,
    )

    summary: dict[str, int] = defaultdict(int)
    processed_ids: list[int] = []
    async with XuiClient() as xui:
        for lid in listing_ids:
            res = await _process_listing(
                xui,
                listing_id=lid,
                cycle_id=cycle_id,
                sampled_at=sampled_at,
                commission_pct=settings.commission_pct,
                reset_enabled=settings.traffic_reset_enabled,
            )
            summary["total"] += 1
            if res["ok"]:
                summary["ok"] += 1
                processed_ids.append(lid)
            else:
                summary["failed"] += 1
            if res["had_traffic"]:
                summary["billed"] += 1
            if res["reset_attempted"] and not res["reset_succeeded"]:
                summary["reset_failed"] += 1

        # --- end-of-cycle: reset all inbound up/down totals on the panel ---
        # 3x-ui has no per-inbound endpoint for this; resetAllTraffics zeros
        # every inbound's totals in one shot. Safe because the panel is
        # dedicated to abr-out. Per-client counters were already reset
        # individually in step 2 of each listing's sub-cycle.
        outbound_reset_ok = False
        if settings.traffic_reset_enabled and processed_ids:
            try:
                await xui.reset_all_inbounds_stat()
                outbound_reset_ok = True
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "[poll] cycle={} resetAllTraffics failed: {!r}",
                    cycle_id,
                    e,
                )

    # If the global reset succeeded, zero the listings' outbound anchors so
    # the next cycle reads fresh totals (symmetric with the per-client flow).
    # On failure the anchors stay at the just-observed values (set inside
    # _bill_inbound) so the next cycle still diffs correctly.
    if processed_ids:
        async with SessionLocal() as session:
            if outbound_reset_ok:
                await session.execute(
                    update(Listing)
                    .where(Listing.id.in_(processed_ids))
                    .values(
                        last_outbound_up_bytes=0,
                        last_outbound_down_bytes=0,
                    )
                )
            await _refresh_listing_sales_totals(session, processed_ids)
            await session.commit()

    logger.info(
        "[poll] cycle={} done: total={} ok={} billed={} failed={} "
        "reset_failed={} outbound_reset_ok={}",
        cycle_id,
        summary["total"],
        summary["ok"],
        summary["billed"],
        summary["failed"],
        summary["reset_failed"],
        outbound_reset_ok,
    )