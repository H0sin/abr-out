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
    disabled = "disabled"


class ConfigStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


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
    swapwallet = "swapwallet"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"


class SwapWalletTxStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    cancelled = "cancelled"
    failed = "failed"


# --- Tables ---


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.user, nullable=False
    )
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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_wallet_transactions_user_currency", "user_id", "currency"),
        Index("ix_wallet_transactions_created_at", "created_at"),
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
    sales_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
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
    vless_link: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ConfigStatus] = mapped_column(
        Enum(ConfigStatus, name="config_status"),
        default=ConfigStatus.active,
        nullable=False,
    )
    last_traffic_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    listing: Mapped[Listing] = relationship(back_populates="configs")
    buyer: Mapped[User] = relationship(back_populates="configs")

    __table_args__ = (
        UniqueConstraint("listing_id", "buyer_user_id", name="uq_configs_listing_buyer"),
        Index("ix_configs_buyer_status", "buyer_user_id", "status"),
    )


class UsageEvent(Base):
    """One unit of measured traffic. Source of usage_* wallet transactions."""

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


class SwapWalletTx(Base):
    """Tracks every SwapWallet top-up request from creation to settlement."""

    __tablename__ = "swapwallet_transactions"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    amount_irt: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invoice_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[SwapWalletTxStatus] = mapped_column(
        Enum(SwapWalletTxStatus, name="swapwallet_tx_status"),
        default=SwapWalletTxStatus.pending,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_swapwallet_tx_user", "user_id"),
        Index("ix_swapwallet_tx_created_at", "created_at"),
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
