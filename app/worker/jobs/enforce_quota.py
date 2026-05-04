"""Enforce per-config volume caps (``Config.total_gb_limit``) on our side.

The 3x-ui panel has a per-client ``totalGB`` field that would normally
disable the client once it crosses the threshold. Our poll-traffic loop
resets each client's panel-side counters every cycle, so the panel-side
cap *never* triggers — the bot has to keep its own running tally and
disable the client when it crosses the buyer-supplied cap.

Cumulative usage per config is the sum of ``ConfigUsage.delta_total_bytes``
inserted by ``poll_traffic`` (this is the same value the API surfaces as
``last_traffic_bytes``). When that sum reaches or exceeds
``total_gb_limit × 1024³`` for an active config, we:

* flip the client to ``disabled`` in the 3x-ui panel,
* set ``Config.status = disabled`` (we do *not* set ``auto_disabled_at``
  — that field is reserved for the balance-enforcement worker which uses
  it to know which configs to auto-re-enable on top-up; quota disables are
  permanent until the buyer manually deletes/repurchases),
* restart Xray once at the end if anything changed (so any open VLESS
  session is torn down immediately and stops burning bandwidth),
* notify the buyer once per disabled config.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.sql import func

from app.common.db.models import (
    Config,
    ConfigStatus,
    ConfigUsage,
    Listing,
    ListingStatus,
)
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.notifications import notify_users
from app.common.panel.xui_client import XuiClient, XuiError

_BYTES_PER_GB = 1024**3


async def _set_panel_enabled(
    inbound_id: int,
    client_uuid: uuid.UUID,
    email: str,
    enable: bool,
) -> bool:
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
            "[enforce_quota] panel update_client_enabled failed "
            "inbound={} email={} enable={} err={}",
            inbound_id,
            email,
            enable,
            e,
        )
    except Exception:
        logger.exception(
            "[enforce_quota] panel session error inbound={} email={}",
            inbound_id,
            email,
        )
    return False


async def _restart_xray_best_effort() -> None:
    try:
        async with XuiClient() as xui:
            await xui.restart_xray()
        logger.info("[enforce_quota] xray restart requested")
    except Exception:
        logger.exception("[enforce_quota] xray restart failed")


async def enforce_quotas_once() -> None:
    """Disable any active config whose cumulative usage hit its cap."""
    disabled_buyers: list[int] = []
    notifications: list[tuple[int, str, Decimal]] = []  # (buyer_id, name, limit_gb)

    async with SessionLocal() as session:
        # Pull every active config that has a buyer-set cap, joined with
        # its listing (we need the inbound id to flip the panel client).
        rows = (
            await session.execute(
                select(Config, Listing)
                .join(Listing, Listing.id == Config.listing_id)
                .where(
                    Config.status == ConfigStatus.active,
                    Config.total_gb_limit.is_not(None),
                )
            )
        ).all()
        if not rows:
            return

        cfg_ids = [int(c.id) for (c, _l) in rows]

        # Cumulative bytes per config from config_usage — this matches the
        # number the API exposes as ``last_traffic_bytes``.
        usage_rows = (
            await session.execute(
                select(
                    ConfigUsage.config_id,
                    func.coalesce(func.sum(ConfigUsage.delta_total_bytes), 0),
                )
                .where(ConfigUsage.config_id.in_(cfg_ids))
                .group_by(ConfigUsage.config_id)
            )
        ).all()
        used_by_cfg: dict[int, int] = {
            int(cid): int(total) for cid, total in usage_rows
        }

        any_changed = False
        for cfg, lst in rows:
            limit_gb = cfg.total_gb_limit
            if limit_gb is None or limit_gb <= 0:
                continue
            limit_bytes = int(
                (Decimal(limit_gb) * Decimal(_BYTES_PER_GB)).to_integral_value()
            )
            used_bytes = used_by_cfg.get(int(cfg.id), 0)
            if used_bytes < limit_bytes:
                continue

            logger.info(
                "[enforce_quota] disabling config={} buyer={} listing={} "
                "used={}B limit={}GB",
                cfg.id,
                cfg.buyer_user_id,
                cfg.listing_id,
                used_bytes,
                limit_gb,
            )

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
            # Always flip the DB row even if the panel call failed: poll_traffic
            # uses ``ConfigStatus.active`` to decide who to bill, so we must
            # stop billing this config immediately. We deliberately leave
            # ``auto_disabled_at`` NULL so the balance-enforcement worker
            # never auto-re-enables a quota-exceeded config.
            await session.execute(
                update(Config)
                .where(Config.id == cfg.id)
                .values(status=ConfigStatus.disabled)
            )
            any_changed = True
            disabled_buyers.append(int(cfg.buyer_user_id))
            notifications.append(
                (int(cfg.buyer_user_id), cfg.name, Decimal(limit_gb))
            )

        if any_changed:
            await session.commit()

    if not notifications:
        return

    # Restart Xray once so already-established VLESS sessions are torn
    # down and stop consuming bandwidth.
    await _restart_xray_best_effort()

    for buyer_id, name, limit_gb in notifications:
        try:
            await notify_users(
                [buyer_id],
                (
                    f"⚠️ کانفیگ «{name}» شما به سقف حجم تعیین‌شده "
                    f"({limit_gb} گیگابایت) رسید و غیرفعال شد.\n"
                    "برای ادامهٔ استفاده، یک کانفیگ جدید خریداری کنید."
                ),
            )
        except Exception:
            logger.exception(
                "[enforce_quota] notify failed buyer={}", buyer_id
            )

    logger.info(
        "[enforce_quota] disabled_configs={} distinct_buyers={}",
        len(notifications),
        len(set(disabled_buyers)),
    )
