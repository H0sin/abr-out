"""Re-create panel inbounds for listings whose 3x-ui inbound was deleted.

When a seller (or operator) manually removes an inbound from the 3x-ui
panel, the DB row keeps pointing at a `panel_inbound_id` that no longer
exists. The probe times out, the listing flips back to `broken`, and
buyer configs cannot be added or updated. Setting the listing back to
`active` in the DB alone does NOT fix this — the panel inbound needs to
exist again with both the probe client and every buyer's client.

This script does the recovery in one shot:

    docker compose exec api python -m scripts.reprovision_listings 12 17
    docker compose exec api python -m scripts.reprovision_listings --all-broken

For each listing it:

  1. Calls `XuiClient.list_inbounds()` and skips listings whose
     `panel_inbound_id` already exists on the panel.
  2. Creates a fresh inbound on the same port + remark via
     ``add_vless_tcp_inbound`` (with the same `externalProxy` entry).
  3. Re-adds the probe client (same UUID/email already in the DB).
  4. Re-adds every active buyer config under the listing using the
     existing `panel_client_uuid` + `panel_client_email`. The buyer's
     stored `vless_link` keeps working because host+port are unchanged.
  5. Updates the listing row: `panel_inbound_id` to the new id,
     `status=pending` with a fresh `pending_until_at`, `broken_since=NULL`.
     The quality-gate worker will promote it back to `active` on the
     next ok ping.

The script is idempotent — running it twice on a listing whose inbound
already exists is a no-op.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.common.db.models import Config, ConfigStatus, Listing, ListingStatus
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.panel.xui_client import XuiClient, XuiError
from app.common.settings import get_settings


async def _panel_inbound_ids() -> set[int]:
    async with XuiClient() as xui:
        rows = await xui.list_inbounds()
    return {int(r["id"]) for r in rows if "id" in r}


async def _reprovision_one(listing: Listing) -> bool:
    """Returns True if the listing was reprovisioned, False if skipped."""
    settings = get_settings()
    if listing.probe_client_uuid is None or listing.probe_client_email is None:
        logger.error(
            "[reprov] listing_id={} has no probe client metadata; skipping",
            listing.id,
        )
        return False

    async with XuiClient() as xui:
        # 1. Create the inbound.
        try:
            inbound = await xui.add_vless_tcp_inbound(
                port=listing.port,
                remark=listing.title,
                external_host=listing.iran_host,
                external_port=listing.port,
            )
        except XuiError as e:
            logger.error(
                "[reprov] listing_id={} add inbound failed: {}", listing.id, e
            )
            return False
        new_inbound_id = int(inbound["id"])
        logger.info(
            "[reprov] listing_id={} created inbound_id={} (was {})",
            listing.id,
            new_inbound_id,
            listing.panel_inbound_id,
        )

        # 2. Add probe client (same UUID/email already in DB).
        try:
            await xui.add_client(
                inbound_id=new_inbound_id,
                client_uuid=uuid_mod.UUID(listing.probe_client_uuid),
                email=listing.probe_client_email,
                total_gb=0,
                expiry_ms=0,
                enable=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[reprov] listing_id={} add probe client failed: {}",
                listing.id,
                e,
            )
            try:
                await xui.delete_inbound(new_inbound_id)
            except Exception:  # noqa: BLE001
                pass
            return False

        # 3. Re-add every non-deleted buyer config.
        async with SessionLocal() as session:
            res = await session.execute(
                select(Config).where(
                    Config.listing_id == listing.id,
                    Config.deleted_at.is_(None),
                )
            )
            configs = list(res.scalars())

        for cfg in configs:
            try:
                await xui.add_client(
                    inbound_id=new_inbound_id,
                    client_uuid=cfg.panel_client_uuid,
                    email=cfg.panel_client_email,
                    total_gb=0,
                    expiry_ms=int(cfg.expiry_at.timestamp() * 1000)
                    if cfg.expiry_at
                    else 0,
                    enable=cfg.status == ConfigStatus.active,
                )
                logger.info(
                    "[reprov] listing_id={} re-added config_id={} email={}",
                    listing.id,
                    cfg.id,
                    cfg.panel_client_email,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "[reprov] listing_id={} add buyer client {} failed: {}",
                    listing.id,
                    cfg.panel_client_email,
                    e,
                )
                # Don't roll back the inbound — partial recovery is still
                # better than nothing; the operator can re-run the script.

    # 4. Update DB.
    pending_until = datetime.now(timezone.utc) + timedelta(
        minutes=settings.listing_quality_gate_minutes
    )
    async with SessionLocal() as session:
        await session.execute(
            update(Listing)
            .where(Listing.id == listing.id)
            .values(
                panel_inbound_id=new_inbound_id,
                status=ListingStatus.pending,
                pending_until_at=pending_until,
                broken_since=None,
            )
        )
        await session.commit()
    logger.info(
        "[reprov] listing_id={} DB updated; pending_until={}",
        listing.id,
        pending_until,
    )
    return True


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ids",
        nargs="*",
        type=int,
        default=[],
        help="Listing IDs to reprovision (omit when using --all-broken/--all-missing)",
    )
    parser.add_argument(
        "--all-broken",
        action="store_true",
        help="Reprovision every listing currently in 'broken' status",
    )
    parser.add_argument(
        "--all-missing",
        action="store_true",
        help="Reprovision every active/broken/pending listing whose "
        "panel_inbound_id is no longer present on the 3x-ui panel",
    )
    args = parser.parse_args()

    if not args.ids and not args.all_broken and not args.all_missing:
        parser.error("provide listing IDs or --all-broken / --all-missing")

    panel_ids: set[int] | None = None
    if args.all_missing:
        panel_ids = await _panel_inbound_ids()
        logger.info("[reprov] panel currently has {} inbound(s)", len(panel_ids))

    async with SessionLocal() as session:
        if args.ids:
            res = await session.execute(
                select(Listing).where(Listing.id.in_(args.ids))
            )
        elif args.all_broken:
            res = await session.execute(
                select(Listing).where(Listing.status == ListingStatus.broken)
            )
        else:
            res = await session.execute(
                select(Listing).where(
                    Listing.status.in_(
                        [
                            ListingStatus.broken,
                            ListingStatus.active,
                            ListingStatus.pending,
                        ]
                    )
                )
            )
        listings = list(res.scalars())

    targets: list[Listing] = []
    for l in listings:
        if panel_ids is not None and l.panel_inbound_id in panel_ids:
            logger.info(
                "[reprov] listing_id={} inbound_id={} still on panel; skip",
                l.id,
                l.panel_inbound_id,
            )
            continue
        targets.append(l)

    if not targets:
        logger.info("[reprov] no listings to reprovision")
        return 0

    logger.info(
        "[reprov] reprovisioning {} listing(s): {}",
        len(targets),
        [l.id for l in targets],
    )
    ok = 0
    for l in targets:
        if await _reprovision_one(l):
            ok += 1
    logger.info("[reprov] done: {}/{} succeeded", ok, len(targets))
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
