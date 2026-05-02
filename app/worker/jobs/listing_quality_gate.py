"""Listing quality gate + dynamic health.

New seller listings are created in ``ListingStatus.pending`` with a
``pending_until_at`` deadline. The Iran-side prober ([scripts/iran-prober](scripts/iran-prober/))
fetches them via ``GET /internal/prober/listings`` and posts back ping
samples through the seller's own VLESS-TCP tunnel (using the dedicated
probe client added when the listing was created).

This job runs every 30s and performs three passes:

1. **Pending pass.** Promotes any pending listing that has at least one
   ``ok=true`` sample recorded after ``created_at`` to ``active``
   (clears ``pending_until_at``). Hard-deletes any pending listing whose
   ``pending_until_at`` has passed without a single successful ping
   (best-effort ``XuiClient.delete_inbound`` then
   ``DELETE FROM listings``).

2. **Demote pass.** ``active`` -> ``broken`` when the listing has gone
   ``listing_broken_after_minutes`` (default 10) without an ok sample
   while still being probed (we require at least one PingSample after
   ``created_at`` so a brand-new active listing whose probes haven't
   landed yet isn't demoted prematurely). The row is hidden from the
   marketplace but kept around — buyer configs continue to bill if the
   tunnel briefly recovers, and the prober still re-tests the host on a
   slower cadence (see `prober.list_targets`).

3. **Recover pass.** ``broken`` -> ``active`` when the last
   ``listing_recovery_consecutive_ok`` (default 2) PingSample rows since
   ``broken_since`` are all ``ok=true``. ``broken_since`` is cleared and
   the row reappears in the marketplace automatically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, select, update

from app.common.db.models import Listing, ListingStatus, PingSample
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.panel.xui_client import XuiClient, XuiError
from app.common.settings import get_settings


async def listing_quality_gate_once() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    demote_cutoff = now - timedelta(
        minutes=settings.listing_broken_after_minutes
    )
    recovery_n = max(1, int(settings.listing_recovery_consecutive_ok))
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Listing).where(Listing.status == ListingStatus.pending)
            )
        ).scalars().all()
        if rows:
            await _pending_pass(session, rows, now)

        # 2) Demote pass: active -> broken.
        active_rows = (
            await session.execute(
                select(Listing).where(Listing.status == ListingStatus.active)
            )
        ).scalars().all()
        demoted: list[int] = []
        for listing in active_rows:
            last_ok = listing.last_ok_ping_at
            if last_ok is not None and last_ok >= demote_cutoff:
                continue
            # Don't demote a fresh active listing whose first probes
            # haven't arrived yet. Require evidence that the prober is
            # actually running against this row.
            seen_any = (
                await session.execute(
                    select(
                        exists().where(
                            PingSample.listing_id == listing.id,
                            PingSample.sampled_at >= listing.created_at,
                        )
                    )
                )
            ).scalar()
            if not seen_any:
                continue
            await session.execute(
                update(Listing)
                .where(Listing.id == listing.id)
                .values(status=ListingStatus.broken, broken_since=now)
            )
            demoted.append(listing.id)

        # 3) Recover pass: broken -> active after N consecutive ok=true
        # samples since broken_since.
        broken_rows = (
            await session.execute(
                select(Listing).where(Listing.status == ListingStatus.broken)
            )
        ).scalars().all()
        recovered: list[int] = []
        for listing in broken_rows:
            since = listing.broken_since or listing.created_at
            recent = (
                await session.execute(
                    select(PingSample.ok)
                    .where(
                        PingSample.listing_id == listing.id,
                        PingSample.sampled_at > since,
                    )
                    .order_by(PingSample.sampled_at.desc())
                    .limit(recovery_n)
                )
            ).all()
            if len(recent) < recovery_n:
                continue
            if not all(bool(r[0]) for r in recent):
                continue
            await session.execute(
                update(Listing)
                .where(Listing.id == listing.id)
                .values(status=ListingStatus.active, broken_since=None)
            )
            recovered.append(listing.id)

        await session.commit()
        if demoted or recovered:
            logger.info(
                "[health] demoted={} recovered={} (demoted_ids={} recovered_ids={})",
                len(demoted),
                len(recovered),
                demoted,
                recovered,
            )


async def _pending_pass(session, rows, now: datetime) -> None:
    """Original pending->active / pending->hard-delete logic."""
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

    if promoted or rejected_ids:
        logger.info(
            "[quality_gate] promoted={} rejected={} (ids={})",
            promoted,
            len(rejected_ids),
            rejected_ids,
        )
