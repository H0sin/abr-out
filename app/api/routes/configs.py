from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import current_user
from app.common.db.models import (
    Config,
    ConfigStatus,
    ConfigUsage,
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

# Hard cap: a single buyer may have at most this many non-deleted
# configs under one listing. Soft-deleted (DELETE) is the only state
# that frees a slot.
MAX_CONFIGS_PER_LISTING_PER_BUYER = 5

# ASCII-only config name: latin letters, digits, space, dash, underscore, dot.
# Persian/Arabic and other non-ASCII are rejected so that the panel client
# email (which derives from this name) stays plain ASCII.
_NAME_ALLOWED = re.compile(r"^[A-Za-z0-9 ._-]+$")
_NAME_MAX_LEN = 32


def _sanitize_name(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = s[:_NAME_MAX_LEN]
    if not s or not _NAME_ALLOWED.fullmatch(s):
        return ""
    return s


class ConfigOut(BaseModel):
    id: int
    listing_id: int
    listing_title: str
    name: str
    panel_client_email: str
    vless_link: str
    status: str
    last_traffic_bytes: int
    expiry_at: datetime | None = None
    total_gb_limit: float | None = None
    auto_disable_on_price_increase: bool = False


class ConfigCreateIn(BaseModel):
    listing_id: int
    name: str = Field(min_length=1, max_length=64)
    expiry_days: int | None = Field(default=None, ge=1, le=3650)
    total_gb_limit: float | None = Field(default=None, gt=0, le=100000)
    auto_disable_on_price_increase: bool = False


class ConfigPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    auto_disable_on_price_increase: bool | None = None


def _to_out(c: Config, l: Listing, total_used_bytes: int = 0) -> ConfigOut:
    return ConfigOut(
        id=c.id,
        listing_id=c.listing_id,
        listing_title=l.title,
        name=c.name,
        panel_client_email=c.panel_client_email,
        vless_link=c.vless_link,
        status=c.status.value,
        last_traffic_bytes=total_used_bytes,
        expiry_at=c.expiry_at,
        total_gb_limit=(
            float(c.total_gb_limit) if c.total_gb_limit is not None else None
        ),
        auto_disable_on_price_increase=bool(c.auto_disable_on_price_increase),
    )


@router.get("", response_model=list[ConfigOut])
async def list_my_configs(
    user: User = Depends(current_user),
) -> list[ConfigOut]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Config, Listing)
            .join(Listing, Listing.id == Config.listing_id)
            .where(
                Config.buyer_user_id == user.telegram_id,
                Config.status != ConfigStatus.deleted,
            )
            .order_by(Config.created_at.desc())
        )
        rows = result.all()
        # Aggregate cumulative used bytes per config from config_usage.
        config_ids = [c.id for (c, _l) in rows]
        totals: dict[int, int] = {}
        if config_ids:
            usage_rows = (
                await session.execute(
                    select(
                        ConfigUsage.config_id,
                        func.coalesce(
                            func.sum(ConfigUsage.delta_total_bytes), 0
                        ),
                    )
                    .where(ConfigUsage.config_id.in_(config_ids))
                    .group_by(ConfigUsage.config_id)
                )
            ).all()
            totals = {cid: int(total) for cid, total in usage_rows}
    return [_to_out(c, l, totals.get(c.id, 0)) for (c, l) in rows]


@router.post("", response_model=ConfigOut, status_code=201)
async def create_config(
    body: ConfigCreateIn,
    user: User = Depends(current_user),
) -> ConfigOut:
    """
    Buy: create a new client on the seller's listing.

    Pay-as-you-go — the wallet is debited later by the worker based on
    actual traffic; the buyer-supplied ``expiry_days`` and ``total_gb_limit``
    are informational/panel-enforced caps the buyer chose for their own
    config (e.g. for resale). When the buyer's wallet runs out, the
    enforce-balance worker disables the config regardless of those caps.
    """
    settings = get_settings()

    name = _sanitize_name(body.name)
    if not name:
        raise HTTPException(422, detail="invalid name")

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

        if listing.panel_inbound_id is None:
            raise HTTPException(
                500,
                detail="listing not provisioned on panel yet (admin action pending)",
            )

        # Per-listing cap for this buyer. ``deleted`` rows do not count.
        existing_count = (
            await session.execute(
                select(func.count())
                .select_from(Config)
                .where(
                    Config.listing_id == listing.id,
                    Config.buyer_user_id == user.telegram_id,
                    Config.status != ConfigStatus.deleted,
                )
            )
        ).scalar_one()
        if int(existing_count) >= MAX_CONFIGS_PER_LISTING_PER_BUYER:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"max {MAX_CONFIGS_PER_LISTING_PER_BUYER} configs per "
                    "listing for the same buyer; delete one to free a slot"
                ),
            )

        client_uuid = uuid.uuid4()
        # Panel email = sanitized name + 6-char uuid suffix (panel uniqueness).
        email_safe = re.sub(r"\s+", "_", name)
        email = f"{email_safe}-{client_uuid.hex[:6]}"

        expiry_at: datetime | None = None
        expiry_ms = 0
        if body.expiry_days is not None:
            expiry_at = datetime.now(timezone.utc) + timedelta(days=body.expiry_days)
            expiry_ms = int(expiry_at.timestamp() * 1000)

        total_gb_limit: Decimal | None = None
        total_gb_xui = 0
        if body.total_gb_limit is not None:
            total_gb_limit = Decimal(str(body.total_gb_limit))
            # XuiClient.add_client expects an int GB value.
            total_gb_xui = max(1, int(round(body.total_gb_limit)))

        try:
            async with XuiClient() as xui:
                await xui.add_client(
                    inbound_id=listing.panel_inbound_id,
                    client_uuid=client_uuid,
                    email=email,
                    total_gb=total_gb_xui,
                    expiry_ms=expiry_ms,
                    enable=True,
                )
        except XuiError as e:
            logger.exception("3x-ui addClient failed: {}", e)
            raise HTTPException(502, detail="panel error") from e

        # Customer connects to the seller's Iranian endpoint (host:port that
        # the seller entered when creating the listing). The seller is
        # responsible for running a tunnel inbound on their Iran panel that
        # forwards this port to our foreign outbound panel
        # (settings.xui_panel_host_public). See Sell page for instructions.
        vless_link = (
            f"vless://{client_uuid}@{listing.iran_host}:{listing.port}"
            f"?type=tcp&security=none&encryption=none#{email}"
        )

        config = Config(
            listing_id=listing.id,
            buyer_user_id=user.telegram_id,
            panel_client_uuid=client_uuid,
            panel_client_email=email,
            name=name,
            expiry_at=expiry_at,
            total_gb_limit=total_gb_limit,
            vless_link=vless_link,
            status=ConfigStatus.active,
            auto_disable_on_price_increase=bool(
                body.auto_disable_on_price_increase
            ),
        )
        session.add(config)
        listing.sales_count = listing.sales_count + 1
        await session.commit()
        await session.refresh(config)

    return _to_out(config, listing)


# --- Lifecycle: disable / enable / delete / patch ---------------------------


async def _load_owned_config(
    session, config_id: int, user: User
) -> tuple[Config, Listing]:
    row = (
        await session.execute(
            select(Config, Listing)
            .join(Listing, Listing.id == Config.listing_id)
            .where(Config.id == config_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, detail="config not found")
    config, listing = row
    if config.buyer_user_id != user.telegram_id:
        raise HTTPException(403, detail="not your config")
    if config.status == ConfigStatus.deleted:
        raise HTTPException(404, detail="config not found")
    return config, listing


@router.post("/{config_id}/disable", response_model=ConfigOut)
async def disable_config(
    config_id: int,
    user: User = Depends(current_user),
) -> ConfigOut:
    async with SessionLocal() as session:
        config, listing = await _load_owned_config(session, config_id, user)
        if config.status == ConfigStatus.disabled:
            return _to_out(config, listing)
        if listing.panel_inbound_id is not None:
            try:
                async with XuiClient() as xui:
                    await xui.update_client_enabled(
                        inbound_id=listing.panel_inbound_id,
                        client_uuid=config.panel_client_uuid,
                        email=config.panel_client_email,
                        enable=False,
                    )
            except XuiError as e:
                logger.warning(
                    "[configs.disable] panel call failed cfg={} err={}", config.id, e
                )
        config.status = ConfigStatus.disabled
        await session.commit()
        await session.refresh(config)
    return _to_out(config, listing)


@router.post("/{config_id}/enable", response_model=ConfigOut)
async def enable_config(
    config_id: int,
    user: User = Depends(current_user),
) -> ConfigOut:
    async with SessionLocal() as session:
        config, listing = await _load_owned_config(session, config_id, user)
        if listing.status != ListingStatus.active:
            raise HTTPException(
                409,
                detail="parent listing is not active; cannot re-enable this config",
            )
        if config.status == ConfigStatus.active:
            return _to_out(config, listing)
        if listing.panel_inbound_id is not None:
            try:
                async with XuiClient() as xui:
                    await xui.update_client_enabled(
                        inbound_id=listing.panel_inbound_id,
                        client_uuid=config.panel_client_uuid,
                        email=config.panel_client_email,
                        enable=True,
                    )
            except XuiError as e:
                logger.warning(
                    "[configs.enable] panel call failed cfg={} err={}", config.id, e
                )
        config.status = ConfigStatus.active
        await session.commit()
        await session.refresh(config)
    return _to_out(config, listing)


@router.patch("/{config_id}", response_model=ConfigOut)
async def patch_config(
    config_id: int,
    body: ConfigPatchIn,
    user: User = Depends(current_user),
) -> ConfigOut:
    async with SessionLocal() as session:
        config, listing = await _load_owned_config(session, config_id, user)
        if body.name is not None:
            new_name = _sanitize_name(body.name)
            if not new_name:
                raise HTTPException(422, detail="invalid name")
            config.name = new_name
        if body.auto_disable_on_price_increase is not None:
            config.auto_disable_on_price_increase = bool(
                body.auto_disable_on_price_increase
            )
        await session.commit()
        await session.refresh(config)
    return _to_out(config, listing)


@router.delete("/{config_id}", status_code=204, response_model=None)
async def delete_config(
    config_id: int,
    user: User = Depends(current_user),
) -> None:
    async with SessionLocal() as session:
        config, listing = await _load_owned_config(session, config_id, user)
        if listing.panel_inbound_id is not None:
            try:
                async with XuiClient() as xui:
                    await xui.delete_client(
                        listing.panel_inbound_id, config.panel_client_uuid
                    )
            except XuiError as e:
                logger.warning(
                    "[configs.delete] panel delClient failed cfg={} err={}",
                    config.id,
                    e,
                )
        config.status = ConfigStatus.deleted
        config.deleted_at = datetime.now(timezone.utc)
        await session.commit()
