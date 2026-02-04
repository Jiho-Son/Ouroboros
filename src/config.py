"""Strictly typed configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings — loaded from .env or environment variables."""

    # KIS Open API
    KIS_APP_KEY: str
    KIS_APP_SECRET: str
    KIS_ACCOUNT_NO: str  # format: "XXXXXXXX-XX"
    KIS_BASE_URL: str = "https://openapivts.koreainvestment.com:9443"

    # Google Gemini
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-pro"

    # External Data APIs (optional — for data-driven decisions)
    NEWS_API_KEY: str | None = None
    NEWS_API_PROVIDER: str = "alphavantage"  # "alphavantage" or "newsapi"
    MARKET_DATA_API_KEY: str | None = None

    # Legacy field names (for backward compatibility)
    ALPHA_VANTAGE_API_KEY: str | None = None
    NEWSAPI_KEY: str | None = None

    # Risk Management
    CIRCUIT_BREAKER_PCT: float = Field(default=-3.0, le=0.0)
    FAT_FINGER_PCT: float = Field(default=30.0, gt=0.0, le=100.0)
    CONFIDENCE_THRESHOLD: int = Field(default=80, ge=0, le=100)

    # Database
    DB_PATH: str = "data/trade_logs.db"

    # Rate Limiting (requests per second for KIS API)
    # Conservative limit to avoid EGW00201 "초당 거래건수 초과" errors.
    # KIS API real limit is ~2 RPS; 2.0 provides maximum safety.
    RATE_LIMIT_RPS: float = 2.0

    # Trading mode
    MODE: str = Field(default="paper", pattern="^(paper|live)$")

    # Market selection (comma-separated market codes)
    ENABLED_MARKETS: str = "KR"

    # Backup and Disaster Recovery (optional)
    BACKUP_ENABLED: bool = True
    BACKUP_DIR: str = "data/backups"
    S3_ENDPOINT_URL: str | None = None  # For MinIO, Backblaze B2, etc.
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET_NAME: str | None = None
    S3_REGION: str = "us-east-1"

    # Telegram Notifications (optional)
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    TELEGRAM_ENABLED: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def account_number(self) -> str:
        return self.KIS_ACCOUNT_NO.split("-")[0]

    @property
    def account_product_code(self) -> str:
        return self.KIS_ACCOUNT_NO.split("-")[1]

    @property
    def enabled_market_list(self) -> list[str]:
        """Parse ENABLED_MARKETS into list of market codes."""
        return [m.strip() for m in self.ENABLED_MARKETS.split(",") if m.strip()]
