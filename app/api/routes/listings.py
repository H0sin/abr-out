from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import current_user
from app.common.db.models import Listing, ListingStatus, User
from app.common.db.session import SessionLocal

router = APIRouter(prefix="/api/listings", tags=["listings"])


class ListingOut(BaseModel):
    id: int
    title: str
    iran_host: str
    port: int
    price_per_gb_usd: Decimal
    avg_ping_ms: int | None
    sales_count: int
    seller_username: str | None
    status: str


class ListingCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    iran_host: str = Field(min_length=3, max_length=255)
    port: int = Field(ge=1, le=65535)
    price_per_gb_usd: Decimal = Field(gt=0)


@router.get("", response_model=list[ListingOut])
async def list_active(
    _: User = Depends(current_user),
) -> list[ListingOut]:
    """Browse active listings (the marketplace feed)."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing, User)
            .join(User, User.telegram_id == Listing.seller_user_id)
            .where(Listing.status == ListingStatus.active)
            .order_by(Listing.price_per_gb_usd.asc())
        )
        rows = result.all()
    return [
        ListingOut(
            id=l.id,
            title=l.title,
            iran_host=l.iran_host,
            port=l.port,
            price_per_gb_usd=l.price_per_gb_usd,
            avg_ping_ms=l.avg_ping_ms,
            sales_count=l.sales_count,
            seller_username=u.username,
            status=l.status.value,
        )
        for (l, u) in rows
    ]


@router.get("/mine", response_model=list[ListingOut])
async def list_my(
    user: User = Depends(current_user),
) -> list[ListingOut]:
    """Listings owned by the current user (seller view)."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing)
            .where(Listing.seller_user_id == user.telegram_id)
            .order_by(Listing.created_at.desc())
        )
        listings = result.scalars().all()
    return [
        ListingOut(
            id=l.id,
            title=l.title,
            iran_host=l.iran_host,
            port=l.port,
            price_per_gb_usd=l.price_per_gb_usd,
            avg_ping_ms=l.avg_ping_ms,
            sales_count=l.sales_count,
            seller_username=user.username,
            status=l.status.value,
        )
        for l in listings
    ]


@router.post("", response_model=ListingOut, status_code=201)
async def create_listing(
    body: ListingCreateIn,
    user: User = Depends(current_user),
) -> ListingOut:
    """
    Seller creates a new listing. It starts in 'pending' state and an admin
    must approve + provision the foreign 3x-ui inbound before it goes 'active'.
    """
    async with SessionLocal() as session:
        # uniqueness on port is enforced by the DB; surface a friendly error
        existing = await session.execute(
            select(Listing).where(Listing.port == body.port)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(409, detail="port already used by another listing")

        listing = Listing(
            seller_user_id=user.telegram_id,
            title=body.title,
            iran_host=body.iran_host,
            port=body.port,
            price_per_gb_usd=body.price_per_gb_usd,
            status=ListingStatus.pending,
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

    return ListingOut(
        id=listing.id,
        title=listing.title,
        iran_host=listing.iran_host,
        port=listing.port,
        price_per_gb_usd=listing.price_per_gb_usd,
        avg_ping_ms=listing.avg_ping_ms,
        sales_count=listing.sales_count,
        seller_username=user.username,
        status=listing.status.value,
    )
