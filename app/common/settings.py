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
    # Optional explicit target for posting marketplace announcements. Set this
    # to the channel's numeric chat id (preferred for private channels). When
    # empty, we fall back to ``required_channel``.
    required_channel_post_chat_id: str = Field(default="")
    # Backup bot: a separate Telegram bot used only to deliver scheduled
    # database dumps to admin chats. Empty disables the backup job.
    backup_bot_token: str = Field(default="")
    # Hours between automatic DB backups (set to 0 to disable scheduling).
    backup_interval_hours: int = Field(default=24)

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
    min_topup_usd: Decimal = Decimal("1")
    traffic_poll_interval_sec: int = 60
    # Kill-switch for the panel-side reset step of the read→reset poller.
    # When False, the poller still reads and bills via diff but does not
    # call resetAllClientTraffics — useful for debugging.
    traffic_reset_enabled: bool = True

    # Listing quality gate. Sellers' new listings start in ``pending`` and
    # are promoted to ``active`` on the first ok=true PingSample. If no
    # successful ping arrives within this many minutes the listing is
    # marked ``broken`` (panel inbound is kept; seller can hit "retry").
    # Tuned generously because the Iran link is often flaky on first try.
    listing_quality_gate_minutes: int = 15

    # Dynamic health gate for established listings. After an ``active``
    # listing has gone this many minutes without a single ok=true ping
    # sample, the worker demotes it to ``broken``: it disappears from the
    # marketplace but the Iran prober still re-tests it (throttled by
    # ``listing_broken_probe_minutes`` so we don't hammer dead hosts).
    # Recovery requires ``listing_recovery_consecutive_ok`` successful
    # samples in a row. Defaults are deliberately lenient — Iranian
    # filtering causes lots of transient failures and we don't want to
    # empty the marketplace on every blip.
    listing_broken_after_minutes: int = 30
    listing_broken_probe_minutes: int = 5
    listing_recovery_consecutive_ok: int = 1

    # Minimum stability (percent of ok=true ping samples) required for an
    # ``active`` listing to be visible in the buyer marketplace. Listings
    # whose ``stability_pct`` is below this threshold are hidden from the
    # browse feed (but still visible to their seller and still re-probed).
    # ``None``/null stability (e.g. brand-new listings without enough
    # samples yet) is always shown.
    marketplace_min_stability_pct: int = 50
    # Window size (in hours) used by ``aggregate_pings_once`` when computing
    # ``stability_pct``.
    marketplace_stability_window_hours: int = 12
    # After a ``broken`` -> ``active`` recovery, Browse temporarily bypasses
    # the stability threshold for this many hours so healed listings can be
    # sold immediately.
    marketplace_recovery_grace_hours: int = 24

    # Payments
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""
    # Plisio (alternate gateway, used for sub-threshold top-ups because
    # NowPayments enforces a relatively high per-coin minimum).
    plisio_secret_key: str = ""
    # Top-ups strictly below this USD amount are routed to Plisio (when
    # configured); >= this amount are routed to NowPayments.
    plisio_threshold_usd: Decimal = Decimal("10")
    # How long a hosted invoice stays payable, in minutes. Bumped well above
    # the gateways' defaults (Plisio = 60m) so customers paying out of an
    # exchange — where withdrawals can stall for KYC/manual review — still
    # land on a live invoice. Plisio caps this at 2880 (48h).
    invoice_expire_min: int = 720

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

    @property
    def required_channel_post_chat(self) -> int | str | None:
        """Best-effort chat target for channel announcements.

        Prefers the dedicated numeric env var, then falls back to the required
        join channel. Public channels may work with ``@username``; private
        channels generally need the numeric ``-100...`` id.
        """
        raw = self.required_channel_post_chat_id.strip() or self.required_channel.strip()
        if not raw:
            return None
        if raw.lstrip("-").isdigit():
            return int(raw)
        return raw


@lru_cache
def get_settings() -> Settings:
    return Settings()
