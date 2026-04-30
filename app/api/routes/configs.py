from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import current_user
from app.common.db.models import (
    Config,
    ConfigStatus,
    Listing,
    ListingStatus,
    User,
)
from app.common.db.session import SessionLocal
from app.common.db.wallet import get_balance
from app.common.logging import logger
from app.common.panel.xui_client import XuiClient, XuiError
from app.common.settings import get_settings

router = APIRouter(prefix="/api/configs", tags=["configs"])

# Minimum balance required to create a new config (USD).
# Pay-as-you-go: real billing happens later via the worker.
MIN_BALANCE_FOR_NEW_CONFIG = Decimal("0.5")


class ConfigOut(BaseModel):
    id: int
    listing_id: int
    listing_title: str
    panel_client_email: str
    vless_link: str
    status: str
    last_traffic_bytes: int


class ConfigCreateIn(BaseModel):
    listing_id: int


@router.get("", response_model=list[ConfigOut])
async def list_my_configs(
    user: User = Depends(current_user),
) -> list[ConfigOut]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Config, Listing)
            .join(Listing, Listing.id == Config.listing_id)
            .where(Config.buyer_user_id == user.telegram_id)
            .order_by(Config.created_at.desc())
        )
        rows = result.all()
    return [
        ConfigOut(
            id=c.id,
            listing_id=c.listing_id,
            listing_title=l.title,
            panel_client_email=c.panel_client_email,
            vless_link=c.vless_link,
            status=c.status.value,
            last_traffic_bytes=c.last_traffic_bytes,
        )
        for (c, l) in rows
    ]


@router.post("", response_model=ConfigOut, status_code=201)
async def create_config(
    body: ConfigCreateIn,
    user: User = Depends(current_user),
) -> ConfigOut:
    """
    Buy: create a new client on the seller's listing.
    Pay-as-you-go — wallet is debited later by the worker based on actual usage.
    Requires a small minimum balance to prevent abuse.
    """
    settings = get_settings()

    async with SessionLocal() as session:
        balance = await get_balance(session, user.telegram_id)
        if balance < MIN_BALANCE_FOR_NEW_CONFIG:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Insufficient balance. Top up at least "
                    f"{MIN_BALANCE_FOR_NEW_CONFIG}$ first."
                ),
            )

        listing = await session.get(Listing, body.listing_id)
        if listing is None or listing.status != ListingStatus.active:
            raise HTTPException(404, detail="listing not found or inactive")

        if listing.seller_user_id == user.telegram_id:
            raise HTTPException(400, detail="cannot buy from your own listing")

        # Already has a config for this listing?
        existing = await session.execute(
            select(Config).where(
                Config.listing_id == listing.id,
                Config.buyer_user_id == user.telegram_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(409, detail="already have a config for this listing")

        if listing.panel_inbound_id is None:
            raise HTTPException(
                500,
                detail="listing not provisioned on panel yet (admin action pending)",
            )

        client_uuid = uuid.uuid4()
        email = f"u{user.telegram_id}-l{listing.id}-{client_uuid.hex[:6]}"

        # Create the client on 3x-ui
        try:
            async with XuiClient() as xui:
                await xui.add_client(
                    inbound_id=listing.panel_inbound_id,
                    client_uuid=client_uuid,
                    email=email,
                    total_gb=0,  # unlimited; we bill per actual usage
                    expiry_ms=0,
                    enable=True,
                )
        except XuiError as e:
            logger.exception("3x-ui addClient failed: {}", e)
            raise HTTPException(502, detail="panel error") from e

        vless_link = (
            f"vless://{client_uuid}@{settings.xui_panel_host_public}:{listing.port}"
            f"?type=tcp&security=none&encryption=none#{email}"
        )

        config = Config(
            listing_id=listing.id,
            buyer_user_id=user.telegram_id,
            panel_client_uuid=client_uuid,
            panel_client_email=email,
            vless_link=vless_link,
            status=ConfigStatus.active,
        )
        session.add(config)
        listing.sales_count = listing.sales_count + 1
        await session.commit()
        await session.refresh(config)

    return ConfigOut(
        id=config.id,
        listing_id=listing.id,
        listing_title=listing.title,
        panel_client_email=config.panel_client_email,
        vless_link=config.vless_link,
        status=config.status.value,
        last_traffic_bytes=config.last_traffic_bytes,
    )
