from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select

from app.api.deps import current_user
from app.common.db.models import (
    Config,
    ConfigStatus,
    Listing,
    ListingStatus,
    OutboundUsage,
    User,
)
from app.common.db.session import SessionLocal
from app.common.logging import logger
from app.common.notifications import notify_listing_buyers, notify_users
from app.common.panel.xui_client import XuiClient, XuiError
from app.common.settings import get_settings

router = APIRouter(prefix="/api/listings", tags=["listings"])

# Hard cap: a single seller may have at most this many listings whose
# ``status`` is anything other than ``deleted``. Soft-deleted rows do not
# count, so the seller can always free a slot via DELETE.
MAX_LISTINGS_PER_SELLER = 5


def _buyer_price(raw: Decimal, commission_mult: Decimal) -> Decimal:
    """Return the buyer-facing per-GB price (commission-inclusive).

    Quantized to 4 decimal places so the wire representation is stable and
    matches the precision used elsewhere in the UI.
    """
    from decimal import ROUND_HALF_UP

    return (raw * commission_mult).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


async def _load_total_gb_by_listing(session, listing_ids: list[int]) -> dict[int, float]:
    if not listing_ids:
        return {}

    total_rows = await session.execute(
        select(
            OutboundUsage.listing_id,
            func.coalesce(func.sum(OutboundUsage.gb), 0).label("total_gb"),
        )
        .where(OutboundUsage.listing_id.in_(listing_ids))
        .group_by(OutboundUsage.listing_id)
    )
    return {int(listing_id): float(total_gb or 0) for listing_id, total_gb in total_rows.all()}

# ASCII-only remark/title: latin letters, digits, space, dash, underscore, dot.
# Persian/Arabic and other non-ASCII are rejected so that 3x-ui inbound remarks
# (and downstream client emails) never contain RTL text or non-ASCII bytes.
_ASCII_TITLE_RE = re.compile(r"^[A-Za-z0-9 ._-]+$")
# IPv4-only for the seller's Iranian endpoint. Domains are rejected because
# the buyer's vless link points directly at this address; a stable, public
# IP avoids DNS-based leakage and makes the seller-side tunnel setup explicit.
_IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$"
)


class ListingOut(BaseModel):
    id: int
    # title/iran_host/seller_username are seller-identifying; the marketplace
    # browse endpoint blanks them out so buyers cannot reach sellers off-bot.
    # The seller's own /mine view and the create response still populate them.
    title: str | None = None
    iran_host: str | None = None
    port: int | None = None
    price_per_gb_usd: Decimal
    # Commission-inclusive price shown to buyers. Computed as
    # ``price_per_gb_usd * (1 + commission_pct)``. Sellers still see the raw
    # ``price_per_gb_usd`` on their /mine view; the marketplace UI uses this
    # field exclusively (no separate fee/commission line is shown to buyers).
    buyer_price_per_gb_usd: Decimal = Decimal("0")
    avg_ping_ms: int | None
    sales_count: int
    seller_username: str | None = None
    status: str
    # Marketplace stats shown on the buy card.
    total_gb_sold: float = 0.0
    gb_sold_24h: float = 0.0
    # Stability percentage (0-100), computed by ``aggregate_pings_once`` as
    # ok_count*100/total over the last 24h of PingSample rows. ``None`` when
    # there are no samples yet (typical for freshly-promoted listings).
    stability_pct: int | None = None


class ListingCreateIn(BaseModel):
    title: str = Field(min_length=2, max_length=128)
    iran_host: str = Field(min_length=7, max_length=15)
    port: int = Field(ge=1, le=65535)
    price_per_gb_usd: Decimal = Field(gt=0)

    @field_validator("title")
    @classmethod
    def _ascii_title(cls, v: str) -> str:
        v = v.strip()
        if not _ASCII_TITLE_RE.fullmatch(v):
            raise ValueError(
                "title must be English letters, digits, space, dot, dash, or underscore"
            )
        return v

    @field_validator("iran_host")
    @classmethod
    def _ipv4_only(cls, v: str) -> str:
        v = v.strip()
        if not _IPV4_RE.fullmatch(v):
            raise ValueError("iran_host must be a valid IPv4 address")
        return v


@router.get("", response_model=list[ListingOut])
async def list_active(
    _: User = Depends(current_user),
) -> list[ListingOut]:
    """Browse active listings (the marketplace feed)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    min_stab = settings.marketplace_min_stability_pct
    recovery_cutoff = now - timedelta(hours=settings.marketplace_recovery_grace_hours)
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing, User)
            .join(User, User.telegram_id == Listing.seller_user_id)
            .where(
                Listing.status == ListingStatus.active,
                or_(
                    Listing.stability_pct.is_(None),
                    Listing.stability_pct >= min_stab,
                    Listing.recovered_at >= recovery_cutoff,
                ),
            )
            .order_by(Listing.price_per_gb_usd.asc())
        )
        rows = result.all()
        listing_ids = [l.id for (l, _u) in rows]
        total_gb_by_listing = await _load_total_gb_by_listing(session, listing_ids)
        # Sum 24h GB per listing in a single query.
        if listing_ids:
            usage_24h_rows = await session.execute(
                select(
                    OutboundUsage.listing_id,
                    func.coalesce(func.sum(OutboundUsage.gb), 0).label("gb24"),
                )
                .where(
                    OutboundUsage.listing_id.in_(listing_ids),
                    OutboundUsage.sampled_at >= cutoff_24h,
                )
                .group_by(OutboundUsage.listing_id)
            )
            gb_24h_by_listing: dict[int, float] = {
                int(lid): float(gb24) for (lid, gb24) in usage_24h_rows.all()
            }
        else:
            gb_24h_by_listing = {}
    commission_mult = Decimal("1") + get_settings().commission_pct
    # Hide seller-identifying fields (title/iran_host/port/seller_username)
    # so a buyer cannot match a listing back to the seller's Telegram or
    # external IP and contact them outside the bot.
    return [
        ListingOut(
            id=l.id,
            price_per_gb_usd=l.price_per_gb_usd,
            buyer_price_per_gb_usd=_buyer_price(l.price_per_gb_usd, commission_mult),
            avg_ping_ms=l.avg_ping_ms,
            sales_count=l.sales_count,
            status=l.status.value,
            total_gb_sold=total_gb_by_listing.get(l.id, float(l.total_gb_sold or 0)),
            gb_sold_24h=gb_24h_by_listing.get(l.id, 0.0),
            stability_pct=l.stability_pct,
        )
        for (l, _u) in rows
    ]


@router.get("/mine", response_model=list[ListingOut])
async def list_my(
    user: User = Depends(current_user),
) -> list[ListingOut]:
    """Listings owned by the current user (seller view)."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Listing)
            .where(
                Listing.seller_user_id == user.telegram_id,
                Listing.status != ListingStatus.deleted,
            )
            .order_by(Listing.created_at.desc())
        )
        listings = result.scalars().all()
        total_gb_by_listing = await _load_total_gb_by_listing(
            session,
            [l.id for l in listings],
        )
    commission_mult = Decimal("1") + get_settings().commission_pct
    return [
        ListingOut(
            id=l.id,
            title=l.title,
            iran_host=l.iran_host,
            port=l.port,
            price_per_gb_usd=l.price_per_gb_usd,
            buyer_price_per_gb_usd=_buyer_price(l.price_per_gb_usd, commission_mult),
            avg_ping_ms=l.avg_ping_ms,
            sales_count=l.sales_count,
            seller_username=user.username,
            status=l.status.value,
            total_gb_sold=total_gb_by_listing.get(l.id, float(l.total_gb_sold or 0)),
            gb_sold_24h=0.0,
            stability_pct=l.stability_pct,
        )
        for l in listings
    ]


@router.post("", response_model=ListingOut, status_code=201)
async def create_listing(
    body: ListingCreateIn,
    user: User = Depends(current_user),
) -> ListingOut:
    """Seller creates a new listing. It is published immediately as 'active'."""
    logger.info(
        "[listings.create] seller_user_id={} title={!r} iran_host={} port={} price_per_gb_usd={}",
        user.telegram_id,
        body.title,
        body.iran_host,
        body.port,
        body.price_per_gb_usd,
    )
    async with SessionLocal() as session:
        # Enforce the per-seller listing cap. ``deleted`` rows are the only
        # state that frees a slot; ``active`` and ``disabled`` both count.
        active_count = (
            await session.execute(
                select(func.count())
                .select_from(Listing)
                .where(
                    Listing.seller_user_id == user.telegram_id,
                    Listing.status != ListingStatus.deleted,
                )
            )
        ).scalar_one()
        if int(active_count) >= MAX_LISTINGS_PER_SELLER:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"max {MAX_LISTINGS_PER_SELLER} listings per seller; "
                    "delete an existing one before creating another"
                ),
            )

        # uniqueness on port is enforced by the DB; surface a friendly error
        existing = await session.execute(
            select(Listing).where(Listing.port == body.port)
        )
        if existing.scalar_one_or_none() is not None:
            logger.warning(
                "[listings.create] port {} already used; rejecting", body.port
            )
            raise HTTPException(409, detail="port already used by another listing")

    # Provision a VLESS-TCP inbound on the foreign 3x-ui panel BEFORE
    # persisting, so the listing is only saved when the panel-side resource
    # exists and we have its id. We also add a dedicated "probe" client to
    # the inbound so the Iran-side prober can build a real VLESS-TCP tunnel
    # through it and measure end-to-end L7 latency (mirrors 3x-ui's own
    # outbound-test feature).
    panel_inbound_id: int | None = None
    probe_uuid = uuid.uuid4()
    try:
        async with XuiClient() as xui:
            inbound = await xui.add_vless_tcp_inbound(
                port=body.port,
                remark=body.title,
                external_host=body.iran_host,
                external_port=body.port,
            )
            raw_id = inbound.get("id")
            if raw_id is None:
                logger.error(
                    "[listings.create] 3x-ui add inbound returned no id; obj={}",
                    inbound,
                )
                raise HTTPException(502, detail="panel error: missing inbound id")
            panel_inbound_id = int(raw_id)
            probe_email = f"probe-{panel_inbound_id}"
            try:
                await xui.add_client(
                    inbound_id=panel_inbound_id,
                    client_uuid=probe_uuid,
                    email=probe_email,
                    total_bytes=0,
                    expiry_ms=0,
                    enable=True,
                )
            except Exception as e:
                # Roll back the inbound we just created — leaving an inbound
                # without a probe client would silently disable the quality
                # gate for that listing.
                logger.exception(
                    "[listings.create] add probe client failed; rolling back inbound {}: {}",
                    panel_inbound_id,
                    e,
                )
                try:
                    await xui.delete_inbound(panel_inbound_id)
                except Exception as cleanup_err:  # noqa: BLE001
                    logger.exception(
                        "[listings.create] cleanup inbound {} failed: {}",
                        panel_inbound_id,
                        cleanup_err,
                    )
                raise HTTPException(502, detail="panel error: add probe client") from e
        logger.info(
            "[listings.create] 3x-ui inbound provisioned id={} port={} probe_email={}",
            panel_inbound_id,
            body.port,
            probe_email,
        )
    except XuiError as e:
        logger.exception("[listings.create] 3x-ui add inbound failed: {}", e)
        raise HTTPException(502, detail="panel error") from e
    except HTTPException:
        raise
    except Exception as e:  # transport / unexpected
        logger.exception("[listings.create] 3x-ui add inbound unexpected error: {}", e)
        raise HTTPException(502, detail="panel error") from e

    async with SessionLocal() as session:
        settings = get_settings()
        pending_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.listing_quality_gate_minutes
        )
        listing = Listing(
            seller_user_id=user.telegram_id,
            title=body.title,
            iran_host=body.iran_host,
            port=body.port,
            price_per_gb_usd=body.price_per_gb_usd,
            # Listings start in pending. The quality-gate worker promotes
            # to active on the first ok=true ping sample, or hard-deletes
            # the row (and panel inbound) once pending_until_at passes.
            status=ListingStatus.pending,
            panel_inbound_id=panel_inbound_id,
            probe_client_uuid=str(probe_uuid),
            probe_client_email=probe_email,
            pending_until_at=pending_until,
        )
        session.add(listing)
        try:
            await session.commit()
            await session.refresh(listing)
        except Exception as e:
            await session.rollback()
            logger.exception(
                "[listings.create] DB commit failed; rolling back panel inbound {}: {}",
                panel_inbound_id,
                e,
            )
            try:
                async with XuiClient() as xui:
                    await xui.delete_inbound(panel_inbound_id)
            except Exception as cleanup_err:
                logger.exception(
                    "[listings.create] failed to cleanup panel inbound {}: {}",
                    panel_inbound_id,
                    cleanup_err,
                )
            raise HTTPException(500, detail="failed to save listing") from e

    logger.info(
        "[listings.create] listing_id={} provisioned with panel_inbound_id={}",
        listing.id,
        listing.panel_inbound_id,
    )

    commission_mult = Decimal("1") + get_settings().commission_pct
    return ListingOut(
        id=listing.id,
        title=listing.title,
        iran_host=listing.iran_host,
        port=listing.port,
        price_per_gb_usd=listing.price_per_gb_usd,
        buyer_price_per_gb_usd=_buyer_price(listing.price_per_gb_usd, commission_mult),
        avg_ping_ms=listing.avg_ping_ms,
        sales_count=listing.sales_count,
        seller_username=user.username,
        status=listing.status.value,
        total_gb_sold=float(listing.total_gb_sold or 0),
        gb_sold_24h=0.0,
        stability_pct=listing.stability_pct,
    )


# --- Lifecycle: disable / enable / edit / delete -----------------------------


class ListingPatchIn(BaseModel):
    """Subset of editable listing fields. ``port`` is intentionally absent —
    the seller may never change the port because it is bound to a 3x-ui
    inbound and to every issued ``vless://`` URI."""

    title: str | None = Field(default=None, min_length=2, max_length=128)
    iran_host: str | None = Field(default=None, min_length=7, max_length=15)
    price_per_gb_usd: Decimal | None = Field(default=None, gt=0)

    @field_validator("title")
    @classmethod
    def _ascii_title(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not _ASCII_TITLE_RE.fullmatch(v):
            raise ValueError(
                "title must be English letters, digits, space, dot, dash, or underscore"
            )
        return v

    @field_validator("iran_host")
    @classmethod
    def _ipv4_only(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not _IPV4_RE.fullmatch(v):
            raise ValueError("iran_host must be a valid IPv4 address")
        return v


def _listing_to_out(l: Listing, seller_username: str | None) -> ListingOut:
    commission_mult = Decimal("1") + get_settings().commission_pct
    return ListingOut(
        id=l.id,
        title=l.title,
        iran_host=l.iran_host,
        port=l.port,
        price_per_gb_usd=l.price_per_gb_usd,
        buyer_price_per_gb_usd=_buyer_price(l.price_per_gb_usd, commission_mult),
        avg_ping_ms=l.avg_ping_ms,
        sales_count=l.sales_count,
        seller_username=seller_username,
        status=l.status.value,
        total_gb_sold=float(l.total_gb_sold or 0),
        gb_sold_24h=0.0,
        stability_pct=l.stability_pct,
    )


async def _load_owned_listing(session, listing_id: int, user: User) -> Listing:
    listing = await session.get(Listing, listing_id)
    if listing is None or listing.status == ListingStatus.deleted:
        raise HTTPException(404, detail="listing not found")
    if listing.seller_user_id != user.telegram_id:
        raise HTTPException(403, detail="not your listing")
    return listing


def _rewrite_vless_host(link: str, new_host: str, port: int) -> str:
    """Replace the ``@host:port`` portion of an existing vless URI."""
    return re.sub(
        r"(vless://[^@]+@)[^:?#]+:\d+",
        rf"\1{new_host}:{port}",
        link,
        count=1,
    )


@router.post("/{listing_id}/disable", response_model=ListingOut)
async def disable_listing(
    listing_id: int,
    user: User = Depends(current_user),
) -> ListingOut:
    """Seller pauses the listing: every active client is disabled in 3x-ui
    and a Telegram message is sent once to each affected buyer."""
    async with SessionLocal() as session:
        listing = await _load_owned_listing(session, listing_id, user)
        if listing.status == ListingStatus.disabled:
            return _listing_to_out(listing, user.username)

        configs = (
            await session.execute(
                select(Config).where(
                    Config.listing_id == listing.id,
                    Config.status == ConfigStatus.active,
                )
            )
        ).scalars().all()

        if listing.panel_inbound_id is not None and configs:
            try:
                async with XuiClient() as xui:
                    for c in configs:
                        try:
                            await xui.update_client_enabled(
                                inbound_id=listing.panel_inbound_id,
                                client_uuid=c.panel_client_uuid,
                                email=c.panel_client_email,
                                enable=False,
                            )
                        except XuiError as e:
                            logger.warning(
                                "[listings.disable] panel disable failed cfg={} err={}",
                                c.id,
                                e,
                            )
            except Exception:
                logger.exception("[listings.disable] panel session error")

        for c in configs:
            c.status = ConfigStatus.disabled

        listing.status = ListingStatus.disabled
        listing.disabled_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(listing)

        await notify_listing_buyers(
            session,
            listing.id,
            (
                f"⚠️ اوت‌باند #{listing.id} توسط فروشنده غیرفعال شد. "
                "کانفیگ شما متوقف شد. لطفاً اوت‌باند دیگری انتخاب کنید."
            ),
        )

    return _listing_to_out(listing, user.username)


@router.post("/{listing_id}/enable", response_model=ListingOut)
async def enable_listing(
    listing_id: int,
    user: User = Depends(current_user),
) -> ListingOut:
    """Seller resumes the listing. Configs are NOT auto-re-enabled — each
    buyer must opt back in from their own configs page."""
    async with SessionLocal() as session:
        listing = await _load_owned_listing(session, listing_id, user)
        if listing.status == ListingStatus.active:
            return _listing_to_out(listing, user.username)
        listing.status = ListingStatus.active
        listing.disabled_at = None
        await session.commit()
        await session.refresh(listing)
    return _listing_to_out(listing, user.username)


@router.post("/{listing_id}/retry", response_model=ListingOut)
async def retry_listing(
    listing_id: int,
    user: User = Depends(current_user),
) -> ListingOut:
    """Re-run the quality gate for a ``broken`` listing.

    Sellers see a "retry test" button on listings whose first quality
    check failed (or that fell out of the marketplace later). Hitting it
    flips the row back to ``pending`` with a fresh deadline; the
    Iran-side prober already includes pending listings on every cycle,
    so a successful ping in the next ``listing_quality_gate_minutes``
    window promotes it back to ``active`` automatically.
    """
    async with SessionLocal() as session:
        listing = await _load_owned_listing(session, listing_id, user)
        if listing.status != ListingStatus.broken:
            raise HTTPException(
                409, detail="retry only allowed for broken listings"
            )
        settings = get_settings()
        listing.status = ListingStatus.pending
        listing.pending_until_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.listing_quality_gate_minutes
        )
        listing.broken_since = None
        await session.commit()
        await session.refresh(listing)
        logger.info(
            "[listings.retry] listing_id={} reset to pending until={}",
            listing.id,
            listing.pending_until_at,
        )
    return _listing_to_out(listing, user.username)


@router.patch("/{listing_id}", response_model=ListingOut)
async def patch_listing(
    listing_id: int,
    body: ListingPatchIn,
    user: User = Depends(current_user),
) -> ListingOut:
    """Edit a listing in place. ``port`` is immutable. Host / price changes
    cascade to the buyer's vless link and (for opted-in configs on a price
    increase) trigger an automatic disable + Telegram notification.
    """
    async with SessionLocal() as session:
        settings = get_settings()
        listing = await _load_owned_listing(session, listing_id, user)

        old_title = listing.title
        old_host = listing.iran_host
        old_price = Decimal(listing.price_per_gb_usd)

        title_changed = body.title is not None and body.title != old_title
        host_changed = body.iran_host is not None and body.iran_host != old_host
        price_changed = (
            body.price_per_gb_usd is not None
            and body.price_per_gb_usd != old_price
        )
        price_increased = (
            body.price_per_gb_usd is not None
            and body.price_per_gb_usd > old_price
        )

        if body.title is not None:
            listing.title = body.title
        if body.price_per_gb_usd is not None:
            listing.price_per_gb_usd = body.price_per_gb_usd
        if host_changed:
            listing.iran_host = body.iran_host  # type: ignore[assignment]

        # If a seller edits a non-sellable listing (broken/disabled), force a
        # fresh quality-gate cycle so the tunnel is re-validated before re-sale.
        edited = title_changed or host_changed or price_changed
        if edited and listing.status in {ListingStatus.broken, ListingStatus.disabled}:
            listing.status = ListingStatus.pending
            listing.pending_until_at = datetime.now(timezone.utc) + timedelta(
                minutes=settings.listing_quality_gate_minutes
            )
            listing.broken_since = None
            listing.disabled_at = None
            listing.recovered_at = None

        # Host change: rewrite every active/disabled config's vless link so
        # the buyer's clipboard QR keeps working without a re-buy.
        if host_changed:
            cfgs = (
                await session.execute(
                    select(Config).where(
                        Config.listing_id == listing.id,
                        Config.status != ConfigStatus.deleted,
                    )
                )
            ).scalars().all()
            for c in cfgs:
                c.vless_link = _rewrite_vless_host(
                    c.vless_link, listing.iran_host, listing.port
                )

        # Price increase: disable opted-in active configs in the panel
        # before committing so the panel/DB stay consistent.
        opted_in_disabled: list[Config] = []
        if price_increased and listing.panel_inbound_id is not None:
            opted_in_active = (
                await session.execute(
                    select(Config).where(
                        Config.listing_id == listing.id,
                        Config.status == ConfigStatus.active,
                        Config.auto_disable_on_price_increase.is_(True),
                    )
                )
            ).scalars().all()
            if opted_in_active:
                try:
                    async with XuiClient() as xui:
                        for c in opted_in_active:
                            try:
                                await xui.update_client_enabled(
                                    inbound_id=listing.panel_inbound_id,
                                    client_uuid=c.panel_client_uuid,
                                    email=c.panel_client_email,
                                    enable=False,
                                )
                                c.status = ConfigStatus.disabled
                                opted_in_disabled.append(c)
                            except XuiError as e:
                                logger.warning(
                                    "[listings.patch] price-disable failed cfg={} err={}",
                                    c.id,
                                    e,
                                )
                except Exception:
                    logger.exception("[listings.patch] panel session error")

        await session.commit()
        await session.refresh(listing)

        if host_changed:
            await notify_listing_buyers(
                session,
                listing.id,
                (
                    f"ℹ️ آدرس IP اوت‌باند #{listing.id} از <code>{old_host}</code> "
                    f"به <code>{listing.iran_host}</code> تغییر کرد. "
                    "لطفاً تغییرات لازم را در کلاینت/پنل خود انجام دهید."
                ),
            )
        if price_increased and opted_in_disabled:
            await notify_listing_buyers(
                session,
                listing.id,
                (
                    f"⚠️ قیمت اوت‌باند #{listing.id} افزایش یافت؛ کانفیگ شما "
                    "طبق تنظیماتتان به‌صورت خودکار غیرفعال شد. در صورت تمایل "
                    "از منوی کانفیگ‌ها مجدداً فعال کنید."
                ),
                only_with_price_flag=True,
            )
        if edited and listing.status == ListingStatus.pending:
            await notify_users(
                [listing.seller_user_id],
                (
                    f"🧪 اوت\u200cباند <code>{listing.title}</code> (#{listing.id}) "
                    "پس از ویرایش، دوباره در وضعیت <b>pending</b> قرار گرفت "
                    "تا تست کیفیت اتصال انجام شود."
                ),
            )

    return _listing_to_out(listing, user.username)


@router.delete("/{listing_id}", status_code=204, response_model=None)
async def delete_listing(
    listing_id: int,
    user: User = Depends(current_user),
) -> None:
    """Soft-delete a listing. All non-deleted child configs are removed
    from 3x-ui, soft-deleted in the DB, and their buyers are notified.
    The 3x-ui inbound itself is also removed (best-effort).
    """
    async with SessionLocal() as session:
        listing = await _load_owned_listing(session, listing_id, user)

        cfgs = (
            await session.execute(
                select(Config).where(
                    Config.listing_id == listing.id,
                    Config.status != ConfigStatus.deleted,
                )
            )
        ).scalars().all()

        affected_buyer_ids = sorted({c.buyer_user_id for c in cfgs})

        if listing.panel_inbound_id is not None:
            try:
                async with XuiClient() as xui:
                    for c in cfgs:
                        try:
                            await xui.delete_client(
                                listing.panel_inbound_id, c.panel_client_uuid
                            )
                        except XuiError as e:
                            logger.warning(
                                "[listings.delete] delClient failed cfg={} err={}",
                                c.id,
                                e,
                            )
                    try:
                        await xui.delete_inbound(listing.panel_inbound_id)
                    except XuiError as e:
                        logger.warning(
                            "[listings.delete] delete_inbound failed id={} err={}",
                            listing.panel_inbound_id,
                            e,
                        )
            except Exception:
                logger.exception("[listings.delete] panel session error")

        now = datetime.now(timezone.utc)
        for c in cfgs:
            c.status = ConfigStatus.deleted
            c.deleted_at = now

        listing.status = ListingStatus.deleted
        listing.deleted_at = now

        await session.commit()

        # Notify each affected buyer once (deleted configs are excluded by
        # notify_listing_buyers, so we send directly using the snapshot).
        from app.common.notifications import notify_users

        await notify_users(
            affected_buyer_ids,
            (
                f"❌ اوت‌باند #{listing_id} توسط فروشنده حذف شد. "
                "کانفیگ شما حذف شد؛ لطفاً اوت‌باند دیگری انتخاب کنید."
            ),
        )
