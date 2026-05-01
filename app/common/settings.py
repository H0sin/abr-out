from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str = Field(default="")
    bot_username: str = Field(default="")  # without leading @, e.g. "abrout_bot"
    admin_telegram_ids: str = Field(default="")  # comma-separated
    # Forced channel subscription gate. Either an @username or a numeric
    # -100… chat id. Empty disables the gate. The bot must be an admin of
    # the channel for getChatMember to work.
    required_channel: str = Field(default="")
    # Optional explicit join URL. If empty and required_channel starts with
    # "@", a t.me link is derived automatically.
    required_channel_url: str = Field(default="")

    # Database
    postgres_user: str = "abrout"
    postgres_password: str = "abrout"
    postgres_db: str = "abrout"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

    # 3x-ui panel
    xui_base_url: str = "http://panel:54321"
    xui_username: str = "admin"
    xui_password: str = "admin"
    xui_panel_host_public: str = "panel.example.com"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_internal_token: str = "change-me-internal-token"

    # Wallet & billing
    commission_pct: Decimal = Decimal("0.15")
    min_topup_usd: Decimal = Decimal("2")
    traffic_poll_interval_sec: int = 60
    # Kill-switch for the panel-side reset step of the read→reset poller.
    # When False, the poller still reads and bills via diff but does not
    # call resetAllClientTraffics — useful for debugging.
    traffic_reset_enabled: bool = True

    # Payments
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""

    # --- USDT-BSC withdrawals ---
    # Public BSC RPC (mainnet by default; switch to testnet for staging).
    bsc_rpc_url: str = "https://bsc-dataseed1.binance.org"
    # Hex private key (with or without 0x prefix) of the hot wallet that
    # holds USDT-BSC + BNB-for-gas. Empty disables withdrawals.
    bsc_hot_wallet_private_key: str = ""
    # USDT (BEP20) contract on BSC mainnet. Override for testnet deploys.
    bsc_usdt_contract: str = "0x55d398326f99059fF775485246999027B3197955"
    # Floor on a single withdrawal request, in USD.
    withdrawal_min_usd: Decimal = Decimal("1")
    # Per-request hard cap on a single withdrawal, in USD.
    withdrawal_max_usd: Decimal = Decimal("1000")
    # Per-user 24-hour rolling cap on the sum of non-failed withdrawals, in USD.
    withdrawal_max_usd_per_day: Decimal = Decimal("5000")
    # Gas units used by a BEP20 ``transfer`` (slightly above 21k+~30k typical).
    withdrawal_gas_limit: int = 80000
    # Multiplier on the gas estimate to absorb price spikes between quote and broadcast.
    withdrawal_fee_buffer_pct: Decimal = Decimal("1.20")
    # If a row sits in ``submitting`` longer than this (because the worker
    # crashed between the status update and the actual broadcast), revert it
    # to ``pending`` so the next tick retries.
    withdrawal_submitting_recovery_min: int = 5
    # If a ``submitted`` row has neither a receipt nor an in-mempool tx after
    # this many minutes, alert admin and freeze it (no auto-refund).
    withdrawal_submitted_alert_min: int = 60
    # Minimum spacing between threshold-mode auto-withdrawals for the same
    # user, in minutes — prevents a tight loop of micro-payouts.
    auto_withdraw_threshold_cooldown_min: int = 60
    # BNB/USDT live ticker used to convert gas (BNB) to USD for the fee quote.
    bnb_price_feed_url: str = (
        "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT"
    )
    # BscScan API for the admin "withdrawal wallet" view (read-only on-chain
    # transaction history). Empty key falls back to RPC eth_getLogs scanning.
    # We use Etherscan's unified multi-chain V2 endpoint and pin chainid=56
    # (BSC) at call sites — the legacy api.bscscan.com V1 was deprecated.
    bscscan_api_key: str = ""
    bscscan_base_url: str = "https://api.etherscan.io/v2/api"

    # Public URL settings.
    # Set DOMAIN to your Cloudflare-fronted domain (e.g. example.com).
    # webhook_base_url is auto-derived as https://{domain} unless overridden.
    domain: str = ""
    webhook_base_url: str = ""

    # Logging
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        # Used by Alembic
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def public_base_url(self) -> str:
        """Effective public URL: explicit webhook_base_url, else https://{domain}."""
        if self.webhook_base_url:
            return self.webhook_base_url.rstrip("/")
        if self.domain:
            return f"https://{self.domain.strip().lstrip('/').rstrip('/')}"
        return ""

    @property
    def admin_ids(self) -> set[int]:
        return {
            int(x.strip())
            for x in self.admin_telegram_ids.split(",")
            if x.strip().isdigit()
        }

    @property
    def effective_required_channel_url(self) -> str:
        """Best-effort join URL for the required channel."""
        if self.required_channel_url:
            return self.required_channel_url.strip()
        ch = self.required_channel.strip()
        if ch.startswith("@") and len(ch) > 1:
            return f"https://t.me/{ch[1:]}"
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
