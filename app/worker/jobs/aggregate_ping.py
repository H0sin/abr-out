"""Aggregate recent ping samples into ``Listing.avg_ping_ms`` and
``Listing.stability_pct``.

- ``avg_ping_ms``: average rtt of ok=true samples in the last 1 hour (used
  on the Browse card as the "ping" badge).
- ``stability_pct``: percentage of ok=true samples in the last configurable
    marketplace window (``ok_count * 100 / total``). ``None`` when no samples
    in the window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, select, update
from sqlalchemy.sql import func

from app.common.db.models import Listing, PingSample
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.settings import get_settings


async def aggregate_pings_once() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    cutoff_1h = now - timedelta(hours=1)
    cutoff_stability = now - timedelta(
        hours=settings.marketplace_stability_window_hours
    )

    async with SessionLocal() as session:
        # 1h average rtt, ok-only.
        avg_rows = (
            await session.execute(
                select(PingSample.listing_id, func.avg(PingSample.rtt_ms))
                .where(
                    PingSample.sampled_at >= cutoff_1h,
                    PingSample.ok.is_(True),
                )
                .group_by(PingSample.listing_id)
            )
        ).all()
        avg_by_listing: dict[int, int | None] = {
            int(lid): (int(avg) if avg is not None else None)
            for (lid, avg) in avg_rows
        }

        # Stability percentage from ALL samples (ok and not-ok) in the
        # configured marketplace window.
        ok_int = case((PingSample.ok.is_(True), 1), else_=0)
        stab_rows = (
            await session.execute(
                select(
                    PingSample.listing_id,
                    func.count().label("total"),
                    func.sum(ok_int).label("ok_count"),
                )
                .where(PingSample.sampled_at >= cutoff_stability)
                .group_by(PingSample.listing_id)
            )
        ).all()
        stab_by_listing: dict[int, int] = {}
        for lid, total, ok_count in stab_rows:
            total_i = int(total or 0)
            if total_i <= 0:
                continue
            ok_i = int(ok_count or 0)
            stab_by_listing[int(lid)] = round(ok_i * 100 / total_i)

        # Iterate over the union of keys so that a listing with samples in
        # only one window still gets that field refreshed.
        listing_ids = set(avg_by_listing) | set(stab_by_listing)
        for listing_id in listing_ids:
            await session.execute(
                update(Listing)
                .where(Listing.id == listing_id)
                .values(
                    avg_ping_ms=avg_by_listing.get(listing_id),
                    stability_pct=stab_by_listing.get(listing_id),
                )
            )
        await session.commit()
        logger.debug(
            "aggregate_pings: updated {} listings (avg) / {} listings (stability)",
            len(avg_by_listing),
            len(stab_by_listing),
        )

