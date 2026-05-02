from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- Enums ---


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class ListingStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    # Listing was active but stopped responding to probes. Hidden from the
    # marketplace; the prober still re-tests it on a slower cadence and
    # ``listing_quality_gate`` flips it back to ``active`` after a sustained
    # recovery (see ``Listing.broken_since`` / ``last_ok_ping_at``).
    broken = "broken"
    disabled = "disabled"
    deleted = "deleted"


class ConfigStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    deleted = "deleted"


class TxnType(str, enum.Enum):
    topup = "topup"
    usage_debit = "usage_debit"
    usage_credit = "usage_credit"
    commission = "commission"
    refund = "refund"
    payout = "payout"
    adjustment = "adjustment"


class PaymentGateway(str, enum.Enum):
    manual = "manual"
    nowpayments = "nowpayments"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"


class SupportDirection(str, enum.Enum):
    in_ = "in"
    out = "out"


class BroadcastStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class WithdrawalStatus(str, enum.Enum):
    pending = "pending"
    submitting = "submitting"
    submitted = "submitted"
    confirmed = "confirmed"
    failed = "failed"
    refunded = "refunded"


class WithdrawalSource(str, enum.Enum):
    manual = "manual"
    auto = "auto"


class AutoWithdrawMode(str, enum.Enum):
    time = "time"
    threshold = "threshold"


class AutoWithdrawAmountPolicy(str, enum.Enum):
    full = "full"
    fixed = "fixed"


# --- Tables ---


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.user, nullable=False
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    listings: Mapped[list[Listing]] = relationship(back_populates="seller")
    configs: Mapped[list[Config]] = relationship(back_populates="buyer")


class WalletTransaction(Base):
    """Signed wallet ledger. Balance = SUM(amount) per (user_id, currency)."""

    __tablename__ = "wallet_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    type: Mapped[TxnType] = mapped_column(Enum(TxnType, name="txn_type"), nullable=False)
    ref: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    created_by_admin_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_wallet_transactions_user_currency", "user_id", "currency"),
        Index("ix_wallet_transactions_created_at", "created_at"),
        Index("ix_wallet_transactions_user_type_created", "user_id", "type", "created_at"),
    )


class Listing(Base):
    """A seller's outbound: maps to one inbound on our foreign 3x-ui."""

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    iran_host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    panel_inbound_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    price_per_gb_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, name="listing_status"),
        default=ListingStatus.pending,
        nullable=False,
    )
    total_gb_sold: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), default=Decimal("0"), nullable=False
    )
    avg_ping_ms: Mapped[int | None] = mapped_column(Integer)
    # Stability % over the last 24h, computed by ``aggregate_pings_once`` from
    # ``PingSample`` (ok_count * 100 / total). ``None`` when no samples yet.
    stability_pct: Mapped[int | None] = mapped_column(Integer)
    # Probe client (added to the 3x-ui inbound at listing-creation time) used
    # by the Iran-side ``iran-prober`` script to build a real VLESS-TCP tunnel
    # and measure end-to-end L7 latency through it.
    probe_client_uuid: Mapped[str | None] = mapped_column(String(64))
    probe_client_email: Mapped[str | None] = mapped_column(String(128))
    # Quality-gate deadline. Listings start in ``pending`` and are promoted to
    # ``active`` on the first successful ping; if no ok=true sample arrives by
    # this timestamp the listing is hard-deleted by ``listing_quality_gate``.
    pending_until_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last time any PingSample was recorded (ok or not). Updated by the
    # ``/internal/prober/samples`` ingestion path and consumed by the
    # prober target endpoint to throttle re-probes of ``broken`` listings.
    last_probed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last time an ok=true PingSample was seen. Drives the active->broken
    # demotion in ``listing_quality_gate``.
    last_ok_ping_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the listing was demoted to ``broken``; ``None`` for any other
    # status. Used as the lower bound when counting consecutive ok samples
    # for recovery.
    broken_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sales_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Last-seen panel-level cumulative up/down for this inbound.
    # Used by the traffic poller for diff-based outbound billing
    # (3x-ui has no per-inbound total reset endpoint).
    last_outbound_up_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    last_outbound_down_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    seller: Mapped[User] = relationship(back_populates="listings")
    configs: Mapped[list[Config]] = relationship(back_populates="listing")

    __table_args__ = (
        Index("ix_listings_status_price", "status", "price_per_gb_usd"),
    )


class Config(Base):
    """One client (config) belonging to a buyer under a seller's listing."""

    __tablename__ = "configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    buyer_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    panel_client_uuid: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), default=uuid.uuid4, nullable=False
    )
    panel_client_email: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    expiry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_gb_limit: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    vless_link: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ConfigStatus] = mapped_column(
        Enum(ConfigStatus, name="config_status"),
        default=ConfigStatus.active,
        nullable=False,
    )
    last_traffic_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Fallback diff anchor: only set when a panel-side reset failed in the
    # previous poll cycle. In the normal read→reset flow this stays at 0
    # because the panel counters are zeroed each cycle.
    last_snapshot_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    auto_disable_on_price_increase: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    listing: Mapped[Listing] = relationship(back_populates="configs")
    buyer: Mapped[User] = relationship(back_populates="configs")

    __table_args__ = (
        Index("ix_configs_buyer_status", "buyer_user_id", "status"),
        Index("ix_configs_listing_buyer", "listing_id", "buyer_user_id"),
    )


class OutboundUsage(Base):
    """Per-cycle traffic recorded at the inbound (seller outbound) level.

    One row per (listing, poll cycle) when ``delta_total_bytes > 0``.
    Source of the seller-side ``usage_credit`` wallet transaction.
    """

    __tablename__ = "outbound_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    seller_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    panel_inbound_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    delta_up_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_down_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gb: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    seller_credit_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False
    )
    panel_total_up_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    panel_total_down_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reset_attempted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    reset_succeeded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_outbound_usage_sampled_at", "sampled_at"),
        Index("ix_outbound_usage_listing_time", "listing_id", "sampled_at"),
        UniqueConstraint(
            "listing_id", "cycle_id", name="uq_outbound_usage_listing_cycle"
        ),
    )


class ConfigUsage(Base):
    """Per-cycle traffic recorded at the client (buyer config) level.

    One row per (config, poll cycle) when ``delta_total_bytes > 0``.
    Source of the buyer-side ``usage_debit`` wallet transaction.
    """

    __tablename__ = "config_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("configs.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seller_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    delta_up_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_down_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gb: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    buyer_debit_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False
    )
    seller_credit_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, server_default="0"
    )
    panel_email: Mapped[str] = mapped_column(String(128), nullable=False)
    reset_attempted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    reset_succeeded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_config_usage_sampled_at", "sampled_at"),
        Index("ix_config_usage_config_time", "config_id", "sampled_at"),
        UniqueConstraint(
            "config_id", "cycle_id", name="uq_config_usage_config_cycle"
        ),
    )


class UsageEvent(Base):
    """Legacy per-cycle billing unit (kept for historical data).

    Superseded by :class:`OutboundUsage` + :class:`ConfigUsage` since the
    read→reset poller landed; no new rows are inserted here.
    """

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("configs.id", ondelete="CASCADE"), nullable=False
    )
    listing_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seller_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gb: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    seller_credit_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    buyer_debit_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    commission_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_usage_events_sampled_at", "sampled_at"),
        Index("ix_usage_events_config", "config_id", "sampled_at"),
    )


class Rating(Base):
    __tablename__ = "ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    buyer_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..5
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("listing_id", "buyer_user_id", name="uq_ratings_listing_buyer"),
    )


class PingSample(Base):
    __tablename__ = "ping_samples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    rtt_ms: Mapped[int | None] = mapped_column(Integer)  # null = failure
    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_ping_samples_listing_time", "listing_id", "sampled_at"),
    )


class PaymentIntent(Base):
    __tablename__ = "payment_intents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    gateway: Mapped[PaymentGateway] = mapped_column(
        Enum(PaymentGateway, name="payment_gateway"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"),
        default=PaymentStatus.pending,
        nullable=False,
    )
    external_ref: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SupportMessage(Base):
    """Inbound (user→admins) and outbound (admin→user) support messages."""

    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[SupportDirection] = mapped_column(
        Enum(SupportDirection, name="support_direction"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Original message id in the user's chat (for forward/copy context).
    user_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # If admin replied, which admin telegram_id did it.
    replied_by_admin_id: Mapped[int | None] = mapped_column(BigInteger)
    # If we replied to a previous inbound message, link it.
    replied_to_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("support_messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_support_messages_user_created", "user_id", "created_at"),
        Index("ix_support_messages_direction_created", "direction", "created_at"),
    )


class Broadcast(Base):
    """Admin broadcast job: a snapshot of the audience filter and progress."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON-serialised audience filter, e.g.
    # {"kind":"all"|"buyers"|"sellers"|"date_range","from":"...","to":"..."}
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[BroadcastStatus] = mapped_column(
        Enum(BroadcastStatus, name="broadcast_status"),
        default=BroadcastStatus.queued,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WithdrawalRequest(Base):
    """A user-initiated USDT-BSC withdrawal from their wallet balance.

    Lifecycle: pending → submitting → submitted → confirmed | failed → refunded.
    The matching ledger entries are a ``payout`` debit at request time and a
    ``refund`` credit if the on-chain send fails.
    """

    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    net_usdt: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    to_address: Mapped[str] = mapped_column(String(64), nullable=False)
    chain: Mapped[str] = mapped_column(String(16), default="BSC", nullable=False)
    asset: Mapped[str] = mapped_column(String(16), default="USDT", nullable=False)
    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus, name="withdrawal_status"),
        default=WithdrawalStatus.pending,
        nullable=False,
    )
    source: Mapped[WithdrawalSource] = mapped_column(
        Enum(WithdrawalSource, name="withdrawal_source"),
        default=WithdrawalSource.manual,
        nullable=False,
    )
    tx_hash: Mapped[str | None] = mapped_column(String(80))
    error_msg: Mapped[str | None] = mapped_column(Text)
    gas_price_wei: Mapped[Decimal | None] = mapped_column(Numeric(40, 0))
    gas_used: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_withdrawal_requests_user_status", "user_id", "status"),
        Index("ix_withdrawal_requests_status_created", "status", "created_at"),
    )


class AutoWithdrawalConfig(Base):
    """Per-user auto-withdraw rule. One row per user (PK = user_id)."""

    __tablename__ = "auto_withdrawal_configs"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    mode: Mapped[AutoWithdrawMode] = mapped_column(
        Enum(AutoWithdrawMode, name="auto_withdraw_mode"), nullable=False
    )
    interval_hours: Mapped[int | None] = mapped_column(Integer)
    threshold_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    amount_policy: Mapped[AutoWithdrawAmountPolicy] = mapped_column(
        Enum(AutoWithdrawAmountPolicy, name="auto_withdraw_amount_policy"),
        nullable=False,
    )
    fixed_amount_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    to_address: Mapped[str] = mapped_column(String(64), nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_withdrawal_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("withdrawal_requests.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_auto_withdrawal_configs_enabled", "enabled"),
    )
