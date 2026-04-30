"""Aggregate recent ping samples into listings.avg_ping_ms."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.sql import func

from app.common.db.models import Listing, PingSample
from app.common.db.session import SessionLocal
from app.common.logging import logger


async def aggregate_pings_once() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    async with SessionLocal() as session:
        stmt = (
            select(PingSample.listing_id, func.avg(PingSample.rtt_ms))
            .where(PingSample.sampled_at >= cutoff, PingSample.ok.is_(True))
            .group_by(PingSample.listing_id)
        )
        rows = (await session.execute(stmt)).all()
        for listing_id, avg in rows:
            await session.execute(
                update(Listing)
                .where(Listing.id == listing_id)
                .values(avg_ping_ms=int(avg) if avg is not None else None)
            )
        await session.commit()
        logger.debug("aggregate_pings: updated {} listings", len(rows))
