"""Listing quality gate.

New seller listings are created in ``ListingStatus.pending`` with a
``pending_until_at`` deadline. The Iran-side prober ([scripts/iran-prober](scripts/iran-prober/))
fetches them via ``GET /internal/prober/listings`` and posts back ping
samples through the seller's own VLESS-TCP tunnel (using the dedicated
probe client added when the listing was created).

This job runs every 30s and:

1. Promotes any pending listing that has at least one ``ok=true`` sample
   recorded after ``created_at`` to ``active`` (clears ``pending_until_at``).
2. Hard-deletes any pending listing whose ``pending_until_at`` has passed
   without a single successful ping. "Hard" means: best-effort
   ``XuiClient.delete_inbound`` then ``DELETE FROM listings`` (the
   PingSample/Config CASCADEs clean up child rows).

The hard-delete path mirrors the seller's UX: the front-end shows a
"awaiting quality check" banner while polling ``/api/listings/mine`` and
falls back to a "rejected" toast when the row disappears.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, exists, select, update

from app.common.db.models import Listing, ListingStatus, PingSample
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.panel.xui_client import XuiClient, XuiError


async def listing_quality_gate_once() -> None:
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Listing).where(Listing.status == ListingStatus.pending)
            )
        ).scalars().all()
        if not rows:
            return

        promoted = 0
        rejected_ids: list[int] = []
        rejected_inbound_ids: list[int] = []

        for listing in rows:
            # Has any ok=true sample been recorded since this listing was
            # created? We compare to ``created_at`` (not ``pending_until_at``)
            # so a slightly-late sample still promotes correctly.
            had_ok = (
                await session.execute(
                    select(
                        exists().where(
                            PingSample.listing_id == listing.id,
                            PingSample.ok.is_(True),
                            PingSample.sampled_at >= listing.created_at,
                        )
                    )
                )
            ).scalar()

            if had_ok:
                await session.execute(
                    update(Listing)
                    .where(Listing.id == listing.id)
                    .values(
                        status=ListingStatus.active,
                        pending_until_at=None,
                    )
                )
                promoted += 1
                continue

            # Still no ok sample. Reject only after the deadline has passed
            # (a missing deadline is treated as "now" so legacy rows do not
            # linger forever).
            deadline = listing.pending_until_at
            if deadline is None or deadline <= now:
                rejected_ids.append(listing.id)
                if listing.panel_inbound_id is not None:
                    rejected_inbound_ids.append(int(listing.panel_inbound_id))

        if rejected_ids:
            # Best-effort panel cleanup BEFORE the DB delete so a transient
            # panel error does not leave orphan inbounds. We swallow XuiError
            # because the row will be removed regardless.
            for inbound_id in rejected_inbound_ids:
                try:
                    async with XuiClient() as xui:
                        await xui.delete_inbound(inbound_id)
                except XuiError as e:
                    logger.warning(
                        "[quality_gate] panel delete_inbound {} failed: {}",
                        inbound_id,
                        e,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception(
                        "[quality_gate] panel delete_inbound {} unexpected: {}",
                        inbound_id,
                        e,
                    )

            await session.execute(
                delete(Listing).where(Listing.id.in_(rejected_ids))
            )

        await session.commit()
        if promoted or rejected_ids:
            logger.info(
                "[quality_gate] promoted={} rejected={} (ids={})",
                promoted,
                len(rejected_ids),
                rejected_ids,
            )
