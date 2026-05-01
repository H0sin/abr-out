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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.db.models import (
    Config,
    ConfigStatus,
    ConfigUsage,
    Listing,
    ListingStatus,
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


def _q_usd(x: Decimal) -> Decimal:
    """Quantise to wallet precision (8 fractional digits)."""
    return x.quantize(Decimal("0.00000001"))


def _q_gb(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.0000000001"))


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
    """Insert per-config usage + paired wallet rows and update anchors.

    Billing is driven entirely by per-client (buyer config) deltas. For each
    client with traffic we insert exactly two wallet rows in the same DB
    transaction: a ``usage_debit`` on the buyer for
    ``gb × price × (1 + commission_pct)`` and a ``usage_credit`` on the
    seller for ``gb × price``. The surcharge difference is implicit
    platform profit and is **not** booked anywhere — no ``commission`` row
    is created. The inbound-level (``snap.up`` / ``snap.down``) totals are
    no longer billed; the ``last_outbound_*_bytes`` anchors on the listing
    are still advanced so they don't grow stale.

    Returns ``True`` if any usage row was inserted.
    """
    # Advance the inbound-level anchors (informational only — not billed).
    if snap.up >= listing.last_outbound_up_bytes:
        listing.last_outbound_up_bytes = snap.up
    else:
        # Panel-side reset detected; resync to current.
        listing.last_outbound_up_bytes = snap.up
    if snap.down >= listing.last_outbound_down_bytes:
        listing.last_outbound_down_bytes = snap.down
    else:
        listing.last_outbound_down_bytes = snap.down

    # --- per-config (paired buyer debit + seller credit) ---
    cfg_rows = (
        await session.execute(
            select(Config).where(
                Config.listing_id == listing.id,
                Config.status == ConfigStatus.active,
            )
        )
    ).scalars().all()
    by_email: dict[str, Config] = {c.panel_client_email: c for c in cfg_rows}

    had_traffic = False
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
        if delta > 0:
            had_traffic = True
            gb = _q_gb(Decimal(delta) / _BYTES_PER_GB)
            seller_credit_abs = _q_usd(gb * listing.price_per_gb_usd)
            buyer_debit_abs = _q_usd(
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
                    buyer_debit_usd=buyer_debit_abs,
                    seller_credit_usd=seller_credit_abs,
                    panel_email=ct.email,
                    reset_attempted=reset_attempted,
                    reset_succeeded=reset_succeeded,
                    sampled_at=sampled_at,
                )
            )
            note = (
                f"usage {delta}B @ {listing.price_per_gb_usd}/GB "
                f"+{commission_pct}"
            )
            if buyer_debit_abs > 0:
                session.add(
                    WalletTransaction(
                        user_id=cfg.buyer_user_id,
                        amount=-buyer_debit_abs,
                        type=TxnType.usage_debit,
                        ref=f"config:{cfg.id}",
                        note=note,
                        idempotency_key=f"poll:{cycle_id}:debit:{cfg.id}",
                    )
                )
            if seller_credit_abs > 0:
                session.add(
                    WalletTransaction(
                        user_id=listing.seller_user_id,
                        amount=seller_credit_abs,
                        type=TxnType.usage_credit,
                        ref=f"config:{cfg.id}",
                        note=note,
                        idempotency_key=f"poll:{cycle_id}:credit:{cfg.id}",
                    )
                )

        # Update anchor regardless of whether delta > 0:
        # - reset succeeded => panel at 0, anchor = 0
        # - reset failed/skipped => anchor = current panel total so next cycle
        #   bills via diff (next_total - current_total) and avoids double-count
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
    """One full poll cycle across every active, provisioned listing."""
    settings = get_settings()
    cycle_id = uuid.uuid4()
    sampled_at = datetime.now(timezone.utc)

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Listing.id)
                .where(
                    Listing.status == ListingStatus.active,
                    Listing.panel_inbound_id.is_not(None),
                )
                .order_by(Listing.id)
            )
        ).all()
    listing_ids = [r[0] for r in rows]

    if not listing_ids:
        logger.debug("[poll] cycle={} no active listings", cycle_id)
        return

    logger.info(
        "[poll] cycle={} listings={} reset_enabled={}",
        cycle_id,
        len(listing_ids),
        settings.traffic_reset_enabled,
    )

    summary: dict[str, int] = defaultdict(int)
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
            else:
                summary["failed"] += 1
            if res["had_traffic"]:
                summary["billed"] += 1
            if res["reset_attempted"] and not res["reset_succeeded"]:
                summary["reset_failed"] += 1

    logger.info(
        "[poll] cycle={} done: total={} ok={} billed={} failed={} reset_failed={}",
        cycle_id,
        summary["total"],
        summary["ok"],
        summary["billed"],
        summary["failed"],
        summary["reset_failed"],
    )