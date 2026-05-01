from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_internal_token
from app.common.db.models import Listing, ListingStatus, PingSample
from app.common.db.session import SessionLocal

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
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing).where(
                Listing.status.in_([ListingStatus.pending, ListingStatus.active])
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
        session.add_all(
            [
                PingSample(
                    listing_id=s.listing_id,
                    rtt_ms=s.rtt_ms,
                    ok=s.ok,
                    sampled_at=s.sampled_at,
                )
                if s.sampled_at
                else PingSample(listing_id=s.listing_id, rtt_ms=s.rtt_ms, ok=s.ok)
                for s in samples
            ]
        )
        await session.commit()
    return Response(status_code=204)
