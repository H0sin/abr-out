"""One-shot recovery: force every listing to active and re-add a probe inbound.

Use when the 3x-ui panel was wiped/rebuilt and you want every DB listing
back online from scratch:

    docker compose exec worker python -m scripts.force_reactivate_all

For every non-deleted listing in the DB (active / pending / broken /
disabled — all of them), this script:

  1. Creates a fresh VLESS-TCP inbound on the panel (same port + remark
     + externalProxy as the listing's iran_host).
  2. Adds the probe client (same UUID/email already stored on the row,
     so the Iran-side prober keeps working without any config change).
  3. Re-adds every non-deleted buyer config under the listing with its
     existing panel_client_uuid + panel_client_email — this preserves
     each buyer's vless link.
  4. Updates the listing row in DB:
        - status         = active
        - panel_inbound_id = <new id>
        - pending_until_at = NULL
        - broken_since   = NULL
        - disabled_at    = NULL

Idempotent only at the DB level. Running it twice will create duplicate
inbounds on the panel — wipe the panel between runs if you need to redo.
"""
from __future__ import annotations

import asyncio
import sys
import uuid as uuid_mod

from sqlalchemy import select, update

from app.common.db.models import Config, ConfigStatus, Listing, ListingStatus
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.panel.xui_client import XuiClient, XuiError


async def _process(listing: Listing) -> bool:
    if not listing.probe_client_uuid or not listing.probe_client_email:
        # Generate fresh probe metadata if the row was created before
        # the probe-client feature existed.
        listing_probe_uuid = uuid_mod.uuid4()
        listing_probe_email = f"probe-listing-{listing.id}"
    else:
        listing_probe_uuid = uuid_mod.UUID(listing.probe_client_uuid)
        listing_probe_email = listing.probe_client_email

    async with XuiClient() as xui:
        try:
            inbound = await xui.add_vless_tcp_inbound(
                port=listing.port,
                remark=listing.title,
                external_host=listing.iran_host,
                external_port=listing.port,
            )
        except XuiError as e:
            logger.error("[force] listing_id={} add inbound failed: {}", listing.id, e)
            return False
        new_inbound_id = int(inbound["id"])
        logger.info(
            "[force] listing_id={} created inbound_id={} (was {})",
            listing.id,
            new_inbound_id,
            listing.panel_inbound_id,
        )

        try:
            await xui.add_client(
                inbound_id=new_inbound_id,
                client_uuid=listing_probe_uuid,
                email=listing_probe_email,
                total_gb=0,
                expiry_ms=0,
                enable=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[force] listing_id={} add probe client failed: {}", listing.id, e
            )
            try:
                await xui.delete_inbound(new_inbound_id)
            except Exception:  # noqa: BLE001
                pass
            return False

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
                    "[force] listing_id={} re-added config_id={} email={}",
                    listing.id,
                    cfg.id,
                    cfg.panel_client_email,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "[force] listing_id={} add buyer client {} failed: {}",
                    listing.id,
                    cfg.panel_client_email,
                    e,
                )

    async with SessionLocal() as session:
        await session.execute(
            update(Listing)
            .where(Listing.id == listing.id)
            .values(
                panel_inbound_id=new_inbound_id,
                probe_client_uuid=str(listing_probe_uuid),
                probe_client_email=listing_probe_email,
                status=ListingStatus.active,
                pending_until_at=None,
                broken_since=None,
                disabled_at=None,
            )
        )
        await session.commit()
    logger.info("[force] listing_id={} now active on inbound_id={}", listing.id, new_inbound_id)
    return True


async def main() -> int:
    async with SessionLocal() as session:
        res = await session.execute(
            select(Listing).where(Listing.status != ListingStatus.deleted)
        )
        listings = list(res.scalars())

    if not listings:
        logger.info("[force] no listings to process")
        return 0

    logger.info("[force] processing {} listing(s): {}", len(listings), [l.id for l in listings])
    ok = 0
    for l in listings:
        if await _process(l):
            ok += 1
    logger.info("[force] done: {}/{} succeeded", ok, len(listings))
    return 0 if ok == len(listings) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
