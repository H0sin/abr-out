from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str = Field(default="")
    admin_telegram_ids: str = Field(default="")  # comma-separated

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

    # Payments
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""

    # SwapWallet
    swapwallet_api_key: str = ""
    # Optional fallback if SwapWallet rate API is unreachable (Toman per 1 USDT).
    # 0 means no fallback → raise an error.
    swapwallet_fallback_rate: int = 0

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
