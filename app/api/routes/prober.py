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
            ListingTarget(listing_id=r.id, iran_host=r.iran_host, port=r.port)
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
