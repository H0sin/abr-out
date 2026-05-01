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
   and signed wallet entries:

   * **Outbound** (seller credit) — diff against ``Listing.last_outbound_*``.
     3x-ui has no per-inbound total reset endpoint, so we keep an anchor on
     our side. A negative diff means the panel was reset out-of-band; we
     treat ``delta = current``. Insert ``OutboundUsage`` and a positive
     ``WalletTransaction`` of type ``usage_credit`` for the seller.
   * **Configs** (buyer debits) — for each ``ClientTraffic`` we look up the
     matching ``Config`` by ``panel_client_email`` and compute
     ``delta = current_total - Config.last_snapshot_bytes``. In the normal
     flow ``last_snapshot_bytes`` is 0 (we just reset the panel) so ``delta``
     equals the panel reading. Insert ``ConfigUsage`` and a negative
     ``WalletTransaction`` of type ``usage_debit`` for the buyer.
   * **Commission** — ``commission = sum(buyer_debits_abs) - seller_credit``;
     inserted (signed) as ``WalletTransaction`` of type ``commission`` for
     the first configured admin. May be negative when system / handshake
     traffic on the inbound exceeds what we can attribute to clients.

5. Anchor maintenance, decided by the just-observed ``reset_succeeded``:

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
that cycle's traffic is lost (we already zeroed the panel). The snapshot is
still in memory at that point so the failure is logged loudly with the raw
byte counts; an operator can recover via a manual ``adjustment`` wallet
entry. We accept this in exchange for the smallest possible read→reset gap.
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
    admin_id: int | None,
    commission_pct: Decimal,
    reset_attempted: bool,
    reset_succeeded: bool,
) -> bool:
    """Insert usage + wallet rows for one inbound and update anchors.

    The caller has already attempted (or skipped) the panel-side reset; the
    outcome is passed in via ``reset_attempted``/``reset_succeeded`` so this
    function can set ``Config.last_snapshot_bytes`` to 0 (reset OK) or to
    the just-observed total (reset failed/skipped, next cycle must diff).

    Returns ``True`` if any usage row was inserted.
    """
    # --- outbound (seller credit) ---
    delta_up = snap.up - listing.last_outbound_up_bytes
    delta_down = snap.down - listing.last_outbound_down_bytes
    if delta_up < 0 or delta_down < 0:
        logger.warning(
            "[poll] listing={} panel-side outbound reset detected "
            "(prev_up={}, prev_down={}, now_up={}, now_down={}); "
            "treating delta=current",
            listing.id,
            listing.last_outbound_up_bytes,
            listing.last_outbound_down_bytes,
            snap.up,
            snap.down,
        )
        delta_up = snap.up
        delta_down = snap.down
    delta_total = delta_up + delta_down

    seller_credit = ZERO
    had_outbound = delta_total > 0
    if had_outbound:
        gb = _q_gb(Decimal(delta_total) / _BYTES_PER_GB)
        seller_credit = _q_usd(gb * listing.price_per_gb_usd)
        session.add(
            OutboundUsage(
                listing_id=listing.id,
                seller_user_id=listing.seller_user_id,
                panel_inbound_id=snap.inbound_id,
                cycle_id=cycle_id,
                delta_up_bytes=delta_up,
                delta_down_bytes=delta_down,
                delta_total_bytes=delta_total,
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
                    note=f"outbound {delta_total}B @ {listing.price_per_gb_usd}/GB",
                    idempotency_key=f"poll:{cycle_id}:outbound:{listing.id}",
                )
            )

    # Always advance the diff anchor.
    listing.last_outbound_up_bytes = snap.up
    listing.last_outbound_down_bytes = snap.down

    # --- per-config (buyer debits) ---
    cfg_rows = (
        await session.execute(
            select(Config).where(
                Config.listing_id == listing.id,
                Config.status == ConfigStatus.active,
            )
        )
    ).scalars().all()
    by_email: dict[str, Config] = {c.panel_client_email: c for c in cfg_rows}

    total_buyer_debit = ZERO
    had_config = False
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
            had_config = True
            gb = _q_gb(Decimal(delta) / _BYTES_PER_GB)
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
                    panel_email=ct.email,
                    reset_attempted=reset_attempted,
                    reset_succeeded=reset_succeeded,
                    sampled_at=sampled_at,
                )
            )
            if buyer_debit_abs > 0:
                session.add(
                    WalletTransaction(
                        user_id=cfg.buyer_user_id,
                        amount=-buyer_debit_abs,
                        type=TxnType.usage_debit,
                        ref=f"config:{cfg.id}",
                        note=(
                            f"usage {delta}B @ {listing.price_per_gb_usd}/GB "
                            f"+{commission_pct}"
                        ),
                        idempotency_key=f"poll:{cycle_id}:config:{cfg.id}",
                    )
                )
                total_buyer_debit += buyer_debit_abs

        # Update anchor regardless of whether delta > 0:
        # - reset succeeded => panel at 0, anchor = 0
        # - reset failed/skipped => anchor = current panel total so next cycle
        #   bills via diff (next_total - current_total) and avoids double-count
        cfg.last_snapshot_bytes = 0 if reset_succeeded else current_total

    # --- commission (admin) ---
    if admin_id is not None and (had_outbound or had_config):
        commission = _q_usd(total_buyer_debit - seller_credit)
        if commission != ZERO:
            session.add(
                WalletTransaction(
                    user_id=admin_id,
                    amount=commission,
                    type=TxnType.commission,
                    ref=f"listing:{listing.id}",
                    note=(
                        f"buyer_debits={total_buyer_debit} "
                        f"seller_credit={seller_credit}"
                    ),
                    idempotency_key=f"poll:{cycle_id}:commission:{listing.id}",
                )
            )
            if commission < 0:
                logger.warning(
                    "[poll] listing={} negative commission={} "
                    "(seller credit exceeds attributed buyer debits)",
                    listing.id,
                    commission,
                )

    return had_outbound or had_config


async def _process_listing(
    xui: XuiClient,
    *,
    listing_id: int,
    cycle_id: uuid.UUID,
    sampled_at: datetime,
    admin_id: int | None,
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
                admin_id=admin_id,
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
        # The panel was already reset (if reset_succeeded), so this cycle's
        # data is at risk. Log the raw snapshot so an operator can recover.
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
    admin_ids = sorted(settings.admin_ids)
    admin_id = admin_ids[0] if admin_ids else None
    if admin_id is None:
        logger.warning(
            "[poll] cycle={} no admin id configured — commission rows skipped",
            cycle_id,
        )

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
                admin_id=admin_id,
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
"""Per-cycle traffic polling and billing.

Algorithm (read → reset):

1. ``cycle_id = uuid4()`` is generated for the run.
2. For each active listing with a provisioned inbound, we fetch one
   :class:`InboundSnapshot` from the panel — that single round-trip carries
   both the inbound-level totals (``up`` / ``down``) used to bill the seller
   and the per-client ``clientStats[]`` used to bill each buyer.
3. In one DB transaction per inbound:

   * **Outbound** — diff against ``Listing.last_outbound_*_bytes`` (3x-ui has
     no per-inbound total reset endpoint, so we keep an anchor on our side).
     A negative diff means the panel was reset out-of-band; we treat
     ``delta = current``. Insert ``OutboundUsage`` and a positive
     ``WalletTransaction`` of type ``usage_credit`` for the seller.
   * **Configs** — for each ``ClientTraffic`` we look up the matching
     ``Config`` by ``panel_client_email``. ``delta = current_total -
     Config.last_snapshot_bytes``; in the normal flow ``last_snapshot_bytes``
     is 0 because we reset the panel last cycle, so ``delta`` equals the
     panel reading. Insert ``ConfigUsage`` and a negative
     ``WalletTransaction`` of type ``usage_debit`` for the buyer.
   * **Commission** — ``commission = sum(buyer_debits_abs) - seller_credit``;
     inserted (signed) as ``WalletTransaction`` of type ``commission`` for
     the first configured admin. May be negative when system / handshake
     traffic on the inbound exceeds what we can attribute to clients.

4. Only after the DB transaction commits do we ask the panel to reset client
   counters. On success, ``Config.last_snapshot_bytes`` is left at 0 for
   every config under this listing. On failure, ``last_snapshot_bytes`` is
   set to the just-read total per client so the next cycle bills via diff
   and avoids double-counting.

All wallet rows use ``idempotency_key = poll:{cycle_id}:{kind}:{entity_id}``
which is ``UNIQUE`` on ``wallet_transactions``, so re-running a partially
applied cycle is safe.

Errors are isolated per inbound — a panel failure on listing A does not
prevent listings B..N from being polled.
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
    admin_id: int | None,
    commission_pct: Decimal,
) -> tuple[bool, dict[int, int]]:
    """Insert usage + wallet rows for one inbound.

    Returns ``(had_traffic, per_config_totals)`` where ``per_config_totals``
    maps ``Config.id`` -> total bytes read from the panel for that config in
    this cycle. The caller uses it to set fallback ``last_snapshot_bytes``
    if the subsequent panel reset fails.
    """
    # --- outbound (seller credit) ---
    delta_up = snap.up - listing.last_outbound_up_bytes
    delta_down = snap.down - listing.last_outbound_down_bytes
    if delta_up < 0 or delta_down < 0:
        # Counter went backwards => panel was reset out-of-band.
        logger.warning(
            "[poll] listing={} panel-side outbound reset detected "
            "(prev_up={}, prev_down={}, now_up={}, now_down={}); "
            "treating delta=current",
            listing.id,
            listing.last_outbound_up_bytes,
            listing.last_outbound_down_bytes,
            snap.up,
            snap.down,
        )
        delta_up = snap.up
        delta_down = snap.down
    delta_total = delta_up + delta_down

    seller_credit = ZERO
    had_outbound = delta_total > 0
    if had_outbound:
        gb = _q_gb(Decimal(delta_total) / _BYTES_PER_GB)
        seller_credit = _q_usd(gb * listing.price_per_gb_usd)
        session.add(
            OutboundUsage(
                listing_id=listing.id,
                seller_user_id=listing.seller_user_id,
                panel_inbound_id=snap.inbound_id,
                cycle_id=cycle_id,
                delta_up_bytes=delta_up,
                delta_down_bytes=delta_down,
                delta_total_bytes=delta_total,
                gb=gb,
                seller_credit_usd=seller_credit,
                panel_total_up_bytes=snap.up,
                panel_total_down_bytes=snap.down,
                reset_attempted=False,
                reset_succeeded=False,
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
                    note=f"outbound {delta_total}B @ {listing.price_per_gb_usd}/GB",
                    idempotency_key=f"poll:{cycle_id}:outbound:{listing.id}",
                )
            )

    # Always advance the diff anchor — when delta was 0, snap matches the
    # stored value already; when reset was detected, we re-anchor to the
    # post-reset baseline.
    listing.last_outbound_up_bytes = snap.up
    listing.last_outbound_down_bytes = snap.down

    # --- per-config (buyer debits) ---
    cfg_rows = (
        await session.execute(
            select(Config).where(
                Config.listing_id == listing.id,
                Config.status == ConfigStatus.active,
            )
        )
    ).scalars().all()
    by_email: dict[str, Config] = {c.panel_client_email: c for c in cfg_rows}

    per_config_totals: dict[int, int] = {}
    total_buyer_debit = ZERO
    had_config = False
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
        per_config_totals[cfg.id] = current_total
        delta = current_total - cfg.last_snapshot_bytes
        if delta < 0:
            # Should not happen in normal flow (last_snapshot is 0 after a
            # successful reset). If it does, the panel was reset under us.
            logger.warning(
                "[poll] config={} panel-side reset detected "
                "(prev={}, now={}); treating delta=current",
                cfg.id,
                cfg.last_snapshot_bytes,
                current_total,
            )
            delta = current_total
        if delta == 0:
            continue
        had_config = True
        gb = _q_gb(Decimal(delta) / _BYTES_PER_GB)
        buyer_debit_abs = _q_usd(
            gb * listing.price_per_gb_usd * (Decimal("1") + commission_pct)
        )
        # We only track per-config total bytes (not up/down separately),
        # so split the delta proportionally to the panel reading for audit.
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
                panel_email=ct.email,
                reset_attempted=False,
                reset_succeeded=False,
                sampled_at=sampled_at,
            )
        )
        if buyer_debit_abs > 0:
            session.add(
                WalletTransaction(
                    user_id=cfg.buyer_user_id,
                    amount=-buyer_debit_abs,
                    type=TxnType.usage_debit,
                    ref=f"config:{cfg.id}",
                    note=(
                        f"usage {delta}B @ {listing.price_per_gb_usd}/GB "
                        f"+{commission_pct}"
                    ),
                    idempotency_key=f"poll:{cycle_id}:config:{cfg.id}",
                )
            )
            total_buyer_debit += buyer_debit_abs

    # --- commission (admin) ---
    if admin_id is not None and (had_outbound or had_config):
        commission = _q_usd(total_buyer_debit - seller_credit)
        if commission != ZERO:
            session.add(
                WalletTransaction(
                    user_id=admin_id,
                    amount=commission,
                    type=TxnType.commission,
                    ref=f"listing:{listing.id}",
                    note=(
                        f"buyer_debits={total_buyer_debit} "
                        f"seller_credit={seller_credit}"
                    ),
                    idempotency_key=f"poll:{cycle_id}:commission:{listing.id}",
                )
            )
            if commission < 0:
                logger.warning(
                    "[poll] listing={} negative commission={} "
                    "(seller credit exceeds attributed buyer debits)",
                    listing.id,
                    commission,
                )

    return (had_outbound or had_config), per_config_totals


async def _process_listing(
    xui: XuiClient,
    *,
    listing_id: int,
    cycle_id: uuid.UUID,
    sampled_at: datetime,
    admin_id: int | None,
    commission_pct: Decimal,
    reset_enabled: bool,
) -> dict[str, Any]:
    """Run one (read → DB commit → reset) sub-cycle for a single listing."""
    stats: dict[str, Any] = {
        "listing_id": listing_id,
        "ok": False,
        "had_traffic": False,
        "reset_attempted": False,
        "reset_succeeded": False,
        "error": None,
    }

    # --- read snapshot ---
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

    # --- DB transaction: bill ---
    per_config_totals: dict[int, int] = {}
    try:
        async with SessionLocal() as session:
            listing = await session.get(
                Listing, listing_id, with_for_update=True
            )
            if listing is None:
                stats["error"] = "listing vanished mid-cycle"
                return stats
            had_traffic, per_config_totals = await _bill_inbound(
                session,
                listing=listing,
                snap=snap,
                cycle_id=cycle_id,
                sampled_at=sampled_at,
                admin_id=admin_id,
                commission_pct=commission_pct,
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
        logger.exception("[poll] listing={} billing failed", listing_id)
        return stats

    # --- panel reset (after DB commit) ---
    if not reset_enabled:
        stats["ok"] = True
        return stats
    if not snap.clients:
        stats["ok"] = True
        return stats
    stats["reset_attempted"] = True
    try:
        await xui.reset_inbound_clients_traffic(inbound_id)
        stats["reset_succeeded"] = True
        async with SessionLocal() as session:
            await session.execute(
                update(Config)
                .where(Config.listing_id == listing_id)
                .values(last_snapshot_bytes=0)
            )
            await session.execute(
                update(OutboundUsage)
                .where(
                    OutboundUsage.cycle_id == cycle_id,
                    OutboundUsage.listing_id == listing_id,
                )
                .values(reset_attempted=True, reset_succeeded=True)
            )
            await session.execute(
                update(ConfigUsage)
                .where(
                    ConfigUsage.cycle_id == cycle_id,
                    ConfigUsage.listing_id == listing_id,
                )
                .values(reset_attempted=True, reset_succeeded=True)
            )
            await session.commit()
    except Exception as e:  # noqa: BLE001
        stats["error"] = f"reset failed: {e!r}"
        logger.exception(
            "[poll] listing={} panel reset failed; setting fallback anchors",
            listing_id,
        )
        try:
            async with SessionLocal() as session:
                for config_id, total_bytes in per_config_totals.items():
                    await session.execute(
                        update(Config)
                        .where(Config.id == config_id)
                        .values(last_snapshot_bytes=total_bytes)
                    )
                await session.execute(
                    update(OutboundUsage)
                    .where(
                        OutboundUsage.cycle_id == cycle_id,
                        OutboundUsage.listing_id == listing_id,
                    )
                    .values(reset_attempted=True, reset_succeeded=False)
                )
                await session.execute(
                    update(ConfigUsage)
                    .where(
                        ConfigUsage.cycle_id == cycle_id,
                        ConfigUsage.listing_id == listing_id,
                    )
                    .values(reset_attempted=True, reset_succeeded=False)
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "[poll] listing={} failed to record fallback anchors",
                listing_id,
            )
        return stats

    stats["ok"] = True
    return stats


async def poll_traffic_once() -> None:
    """One full poll cycle across every active, provisioned listing."""
    settings = get_settings()
    cycle_id = uuid.uuid4()
    sampled_at = datetime.now(timezone.utc)
    admin_ids = sorted(settings.admin_ids)
    admin_id = admin_ids[0] if admin_ids else None
    if admin_id is None:
        logger.warning(
            "[poll] cycle={} no admin id configured — commission rows skipped",
            cycle_id,
        )

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
                admin_id=admin_id,
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
"""Per-cycle traffic polling and billing.

Skeleton — full implementation in next phase. The plan:
  1. for each active inbound, fetch client traffics from 3x-ui
  2. for each known config, compute delta_bytes vs last_traffic_bytes
  3. insert one usage_event + 3 wallet_transactions (buyer/seller/admin)
     all in a single DB transaction with idempotency keys.
"""
from __future__ import annotations

from app.common.logging import logger


async def poll_traffic_once() -> None:
    logger.debug("poll_traffic: noop (skeleton)")
