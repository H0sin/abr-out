"""Listing quality gate + dynamic health.

New seller listings are created in ``ListingStatus.pending`` with a
``pending_until_at`` deadline. The Iran-side prober ([scripts/iran-prober](scripts/iran-prober/))
fetches them via ``GET /internal/prober/listings`` and posts back ping
samples through the seller's own VLESS-TCP tunnel (using the dedicated
probe client added when the listing was created).

This job runs every 30s and performs three passes:

1. **Pending pass.** Promotes any pending listing that has at least one
   ``ok=true`` sample recorded after ``created_at`` to ``active``
   (clears ``pending_until_at``). When ``pending_until_at`` elapses
   without a successful ping, the listing is moved to ``broken``
   instead of being deleted — the panel inbound + probe client stay
   put so the seller can hit "retry test" (resets to ``pending`` for
   another 5-min window) or wait for the periodic broken-reprobe
   cadence to recover it automatically.

2. **Demote pass.** ``active`` -> ``broken`` when the listing has gone
   ``listing_broken_after_minutes`` (default 10) without an ok sample
   while still being probed (we require recent ``last_probed_at`` so a
   brand-new active listing whose probes haven't landed yet isn't
   demoted prematurely). The row is hidden from the marketplace but
   kept around — buyer configs continue to bill if the tunnel briefly
   recovers, and the prober still re-tests the host on a slower cadence
   (see `prober.list_targets`).

3. **Recover pass.** ``broken`` -> ``active`` on the first
    ``ok=true`` PingSample recorded after ``broken_since``. ``broken_since``
    is cleared, ``recovered_at`` is stamped, and the row reappears in the
    marketplace automatically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, select, update

from app.common.db.models import Listing, ListingStatus, PingSample
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.settings import get_settings


async def listing_quality_gate_once() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    demote_cutoff = now - timedelta(
        minutes=settings.listing_broken_after_minutes
    )
    # Product policy: a single successful probe is enough to recover from
    # ``broken`` so the listing returns to the marketplace immediately.
    recovery_n = 1
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
            # Don't demote without recent evidence that the prober is
            # actually reaching this row. Using ``last_probed_at`` (the
            # cache populated on every sample arrival) guards against
            # two failure modes:
            #   1. A brand-new active listing whose first probes haven't
            #      landed yet — both timestamps are NULL.
            #   2. A schema/cache invariant breaking (e.g. a fresh
            #      column added before backfill, like 0009 did) — we
            #      refuse to demote until an actual sample has arrived
            #      after the cache was populated.
            last_probed = listing.last_probed_at
            if last_probed is None or last_probed < demote_cutoff:
                continue
            await session.execute(
                update(Listing)
                .where(Listing.id == listing.id)
                .values(
                    status=ListingStatus.broken,
                    broken_since=now,
                    recovered_at=None,
                )
            )
            demoted.append(listing.id)

        # 3) Recover pass: broken -> active after first ok=true sample
        # since broken_since.
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
                .values(
                    status=ListingStatus.active,
                    broken_since=None,
                    recovered_at=now,
                )
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
    """Pending lifecycle.

    - Promotes any pending listing that has at least one ``ok=true``
      sample since ``created_at`` to ``active``.
    - Demotes any pending listing whose ``pending_until_at`` deadline
      has elapsed without a successful ping to ``broken`` (keeping the
      panel inbound + probe client intact). The seller's UI shows the
      row with a "connection failed" badge and a "retry test" button
      that resets it back to ``pending`` for another quality-gate pass.
    """
    promoted = 0
    failed_ids: list[int] = []

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

        # Still no ok sample. Mark as broken once the deadline elapses
        # (a missing deadline is treated as "now" so legacy rows do not
        # linger forever in ``pending``). Keep the panel inbound + probe
        # client around so the seller can hit "retry" or the periodic
        # broken-reprobe cadence picks it up.
        deadline = listing.pending_until_at
        if deadline is None or deadline <= now:
            await session.execute(
                update(Listing)
                .where(Listing.id == listing.id)
                .values(
                    status=ListingStatus.broken,
                    broken_since=now,
                    pending_until_at=None,
                )
            )
            failed_ids.append(listing.id)

    if promoted or failed_ids:
        logger.info(
            "[quality_gate] promoted={} failed={} (failed_ids={})",
            promoted,
            len(failed_ids),
            failed_ids,
        )
