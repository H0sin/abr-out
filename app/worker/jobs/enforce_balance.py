"""Disable configs for buyers whose balance reached zero, re-enable on top-up.

Runs every poll cycle. Two passes:

1. **Disable pass.** For every distinct buyer who currently has at least one
   ``ConfigStatus.active`` config, compute their wallet balance. If it is at
   or below zero, we first force a full traffic poll so any bytes already
   consumed since the previous poll are billed *before* we cut the user
   off; then we disable every active config they own in the 3x-ui panel
   and the DB, stamp ``Config.auto_disabled_at`` so we can recognise the
   row later, and notify the buyer once. After the batch, we ask the
   panel to restart Xray so any already-established connections are torn
   down immediately (otherwise an open VLESS connection keeps pumping
   traffic until Xray re-reads the config).

2. **Re-enable pass.** For every distinct buyer who has at least one config
   with ``auto_disabled_at IS NOT NULL`` (i.e. was previously auto-disabled
   by us), re-check the balance; if it has recovered above zero, re-enable
   those rows in the panel and the DB, clear ``auto_disabled_at``, and
   notify the buyer once. Configs that were disabled by the seller, the
   buyer themselves, or for any other reason have ``auto_disabled_at = NULL``
   and are deliberately ignored — those must be re-activated manually.

3x-ui calls and the DB updates are best-effort per-config: a failure on one
config logs and continues so a single bad client never blocks the whole
batch. The buyer notification is also best-effort.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update

from app.common.db.models import (
    Config,
    ConfigStatus,
    Listing,
    ListingStatus,
)
from app.common.db.session import SessionLocal
from app.common.db.wallet import get_balance
from app.common.logging import logger
from app.common.notifications import notify_users
from app.common.panel.xui_client import XuiClient, XuiError
from app.worker.jobs.poll_traffic import poll_traffic_once


async def _set_panel_enabled(
    inbound_id: int,
    client_uuid: uuid.UUID,
    email: str,
    enable: bool,
) -> bool:
    """Best-effort flip of a single 3x-ui client's enable flag."""
    try:
        async with XuiClient() as xui:
            await xui.update_client_enabled(
                inbound_id=inbound_id,
                client_uuid=client_uuid,
                email=email,
                enable=enable,
            )
        return True
    except XuiError as e:
        logger.warning(
            "[enforce_balance] panel update_client_enabled failed "
            "inbound={} email={} enable={} err={}",
            inbound_id,
            email,
            enable,
            e,
        )
    except Exception:
        logger.exception(
            "[enforce_balance] panel session error inbound={} email={}",
            inbound_id,
            email,
        )
    return False


async def _restart_xray_best_effort() -> None:
    """Ask the panel to restart Xray; never raise."""
    try:
        async with XuiClient() as xui:
            await xui.restart_xray()
        logger.info("[enforce_balance] xray restart requested")
    except Exception:
        logger.exception("[enforce_balance] xray restart failed")


async def _select_buyers_at_or_below_zero(session) -> list[int]:
    """Return distinct buyer ids that have ≥1 active config and balance ≤ 0."""
    rows = (
        await session.execute(
            select(Config.buyer_user_id)
            .where(Config.status == ConfigStatus.active)
            .distinct()
        )
    ).all()
    candidates = [int(r[0]) for r in rows]
    out: list[int] = []
    for buyer_id in candidates:
        bal = await get_balance(session, buyer_id)
        if bal <= Decimal("0"):
            out.append(buyer_id)
    return out


async def enforce_balances_once() -> None:
    now = datetime.now(timezone.utc)

    # --- Pre-check: are there any buyers who need disabling? ------------
    # We only want to incur the cost of a full traffic poll when at least
    # one buyer is actually below zero — otherwise this job stays cheap.
    async with SessionLocal() as session:
        candidates = await _select_buyers_at_or_below_zero(session)

    if candidates:
        # Bill the latest traffic before cutting the user off, otherwise
        # everything they used in the current poll window would be free.
        # poll_traffic_once already isolates failures per-listing, so a
        # bad inbound won't prevent disabling.
        try:
            await poll_traffic_once()
        except Exception:
            logger.exception(
                "[enforce_balance] pre-disable poll failed; proceeding anyway"
            )

    async with SessionLocal() as session:
        # --- Pass 1: disable for buyers at or below zero balance ----------
        # Re-evaluate after the poll: a buyer might have just been billed
        # and only now crossed zero, or (unlikely) a top-up landed.
        active_buyer_rows = (
            await session.execute(
                select(Config.buyer_user_id)
                .where(Config.status == ConfigStatus.active)
                .distinct()
            )
        ).all()
        active_buyers = [int(r[0]) for r in active_buyer_rows]

        disabled_users: list[int] = []
        for buyer_id in active_buyers:
            balance = await get_balance(session, buyer_id)
            if balance > Decimal("0"):
                continue

            rows = (
                await session.execute(
                    select(Config, Listing)
                    .join(Listing, Listing.id == Config.listing_id)
                    .where(
                        Config.buyer_user_id == buyer_id,
                        Config.status == ConfigStatus.active,
                    )
                )
            ).all()
            if not rows:
                continue

            any_changed = False
            for cfg, lst in rows:
                if (
                    lst.panel_inbound_id is not None
                    and lst.status != ListingStatus.deleted
                ):
                    await _set_panel_enabled(
                        inbound_id=lst.panel_inbound_id,
                        client_uuid=cfg.panel_client_uuid,
                        email=cfg.panel_client_email,
                        enable=False,
                    )
                # Always flip the DB row even if the panel call failed: the
                # poll-traffic worker uses ``ConfigStatus.active`` to decide
                # who to bill, so we must stop billing this user immediately.
                await session.execute(
                    update(Config)
                    .where(Config.id == cfg.id)
                    .values(
                        status=ConfigStatus.disabled,
                        auto_disabled_at=now,
                    )
                )
                any_changed = True

            if any_changed:
                disabled_users.append(buyer_id)

        # --- Pass 2: re-enable for buyers whose balance recovered --------
        auto_disabled_buyer_rows = (
            await session.execute(
                select(Config.buyer_user_id)
                .where(
                    Config.status == ConfigStatus.disabled,
                    Config.auto_disabled_at.is_not(None),
                )
                .distinct()
            )
        ).all()
        auto_disabled_buyers = [int(r[0]) for r in auto_disabled_buyer_rows]

        reenabled_users: list[int] = []
        for buyer_id in auto_disabled_buyers:
            balance = await get_balance(session, buyer_id)
            if balance <= Decimal("0"):
                continue

            rows = (
                await session.execute(
                    select(Config, Listing)
                    .join(Listing, Listing.id == Config.listing_id)
                    .where(
                        Config.buyer_user_id == buyer_id,
                        Config.status == ConfigStatus.disabled,
                        Config.auto_disabled_at.is_not(None),
                    )
                )
            ).all()
            if not rows:
                continue

            any_changed = False
            for cfg, lst in rows:
                # Skip configs whose listing is no longer sellable; user
                # must re-buy or the seller must restore the listing.
                if lst.status not in {ListingStatus.active, ListingStatus.pending}:
                    continue
                ok = True
                if lst.panel_inbound_id is not None:
                    ok = await _set_panel_enabled(
                        inbound_id=lst.panel_inbound_id,
                        client_uuid=cfg.panel_client_uuid,
                        email=cfg.panel_client_email,
                        enable=True,
                    )
                # If the panel call failed, leave the row disabled so the
                # next tick retries; otherwise flip back to active.
                if ok:
                    await session.execute(
                        update(Config)
                        .where(Config.id == cfg.id)
                        .values(
                            status=ConfigStatus.active,
                            auto_disabled_at=None,
                        )
                    )
                    any_changed = True

            if any_changed:
                reenabled_users.append(buyer_id)

        await session.commit()

    # --- Restart Xray once if anything changed on the panel --------------
    # Without this, a VLESS connection that's already established stays
    # alive and keeps consuming bandwidth until Xray re-reads the config
    # on its own (which can be never, in practice). We restart only when
    # we actually flipped at least one client to disabled.
    if disabled_users:
        await _restart_xray_best_effort()

    # --- Notifications (best-effort, outside the DB session) -------------
    for buyer_id in disabled_users:
        await notify_users(
            [buyer_id],
            (
                "⚠️ موجودی کیف پول شما به صفر رسید.\n"
                "همهٔ کانفیگ‌های فعال شما موقتاً غیرفعال شدند.\n"
                "برای ادامهٔ استفاده، کیف پول را شارژ کنید؛ بلافاصله "
                "پس از تأیید شارژ، کانفیگ‌ها به‌صورت خودکار فعال می‌شوند."
            ),
        )

    for buyer_id in reenabled_users:
        await notify_users(
            [buyer_id],
            (
                "✅ موجودی کیف پول شما به‌روزرسانی شد و کانفیگ‌های شما "
                "دوباره فعال شدند. استفاده مجدد بلامانع است."
            ),
        )

    if disabled_users or reenabled_users:
        logger.info(
            "[enforce_balance] disabled_users={} reenabled_users={}",
            len(disabled_users),
            len(reenabled_users),
        )
