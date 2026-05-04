from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.sql import func

from app.api.deps import require_internal_token
from app.common.db.models import Listing, ListingStatus, PingSample
from app.common.db.session import SessionLocal
from app.common.settings import get_settings

router = APIRouter(prefix="/internal/prober", tags=["prober"])


class ListingTarget(BaseModel):
    listing_id: int
    iran_host: str
    port: int
    # Identifies the dedicated probe client added to the seller's 3x-ui
    # inbound at listing-creation time. The Iran-side prober uses these
    # to build a real VLESS-TCP tunnel and measure end-to-end L7 latency
    # through it (mirrors 3x-ui's own outbound-test feature). May be
    # ``None`` for legacy rows created before the quality-gate feature;
    # the prober skips those.
    panel_inbound_id: int | None = None
    probe_client_uuid: str | None = None
    probe_client_email: str | None = None
    protocol_hint: str = "vless+tcp"


class PingSampleIn(BaseModel):
    listing_id: int
    rtt_ms: int | None = None
    ok: bool = True
    sampled_at: datetime | None = None


@router.get(
    "/listings",
    response_model=list[ListingTarget],
    dependencies=[Depends(require_internal_token)],
)
async def list_targets() -> list[ListingTarget]:
    """Return listings the Iran-side prober should test this cycle.

    - ``pending`` and ``active`` listings are always included.
    - ``broken`` listings are included only when their
      ``last_probed_at`` is older than ``listing_broken_probe_minutes``
      (default 10 min) so dead hosts are not hammered while we still
      detect recovery on a useful cadence.
    """
    settings = get_settings()
    broken_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.listing_broken_probe_minutes
    )
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing).where(
                (
                    Listing.status.in_(
                        [ListingStatus.pending, ListingStatus.active]
                    )
                )
                | (
                    (Listing.status == ListingStatus.broken)
                    & (
                        (Listing.last_probed_at.is_(None))
                        | (Listing.last_probed_at < broken_cutoff)
                    )
                )
            )
        )
        return [
            ListingTarget(
                listing_id=r.id,
                iran_host=r.iran_host,
                port=r.port,
                panel_inbound_id=r.panel_inbound_id,
                probe_client_uuid=r.probe_client_uuid,
                probe_client_email=r.probe_client_email,
            )
            for r in result.scalars().all()
        ]


@router.post(
    "/samples",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_internal_token)],
)
async def post_samples(samples: list[PingSampleIn]) -> Response:
    if not samples:
        return Response(status_code=204)
    async with SessionLocal() as session:
        # Insert raw samples first; PingSample is the source of truth
        # consumed by aggregate_pings_once and the recovery check.
        rows: list[PingSample] = []
        # Per-listing latest timestamps so we can update Listing bookkeeping
        # with one UPDATE per listing per field instead of N round-trips.
        latest_probed: dict[int, datetime] = {}
        latest_ok: dict[int, datetime] = {}
        for s in samples:
            sampled_at = s.sampled_at or datetime.now(timezone.utc)
            # تقسیم پینگ بر ۲.۵ قبل از ذخیره
            rtt_ms = None
            if s.rtt_ms is not None:
                rtt_ms = int(round(s.rtt_ms / 1.3))
            rows.append(
                PingSample(
                    listing_id=s.listing_id,
                    rtt_ms=rtt_ms,
                    ok=s.ok,
                    sampled_at=s.sampled_at,
                )
                if s.sampled_at
                else PingSample(
                    listing_id=s.listing_id, rtt_ms=rtt_ms, ok=s.ok
                )
            )
            prev = latest_probed.get(s.listing_id)
            if prev is None or sampled_at > prev:
                latest_probed[s.listing_id] = sampled_at
            if s.ok:
                prev_ok = latest_ok.get(s.listing_id)
                if prev_ok is None or sampled_at > prev_ok:
                    latest_ok[s.listing_id] = sampled_at
        session.add_all(rows)

        # Bookkeeping on Listing. We use GREATEST() to advance the
        # timestamps monotonically: out-of-order delivery from a slow
        # prober can never rewind ``last_probed_at`` / ``last_ok_ping_at``.
        for listing_id, ts in latest_probed.items():
            await session.execute(
                update(Listing)
                .where(Listing.id == listing_id)
                .values(
                    last_probed_at=func.greatest(
                        func.coalesce(Listing.last_probed_at, ts), ts
                    )
                )
            )
        for listing_id, ts in latest_ok.items():
            await session.execute(
                update(Listing)
                .where(Listing.id == listing_id)
                .values(
                    last_ok_ping_at=func.greatest(
                        func.coalesce(Listing.last_ok_ping_at, ts), ts
                    )
                )
            )

        await session.commit()
    return Response(status_code=204)
