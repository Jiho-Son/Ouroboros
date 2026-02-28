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
    KIS_BASE_URL: str = "https://openapivts.koreainvestment.com:29443"

    # Google Gemini
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.0-flash"

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

    # Smart Scanner Configuration
    RSI_OVERSOLD_THRESHOLD: int = Field(default=30, ge=0, le=50)
    RSI_MOMENTUM_THRESHOLD: int = Field(default=70, ge=50, le=100)
    VOL_MULTIPLIER: float = Field(default=2.0, gt=1.0, le=10.0)
    SCANNER_TOP_N: int = Field(default=3, ge=1, le=10)
    POSITION_SIZING_ENABLED: bool = True
    POSITION_BASE_ALLOCATION_PCT: float = Field(default=5.0, gt=0.0, le=30.0)
    POSITION_MIN_ALLOCATION_PCT: float = Field(default=1.0, gt=0.0, le=20.0)
    POSITION_MAX_ALLOCATION_PCT: float = Field(default=10.0, gt=0.0, le=50.0)
    POSITION_VOLATILITY_TARGET_SCORE: float = Field(default=50.0, gt=0.0, le=100.0)

    # Database
    DB_PATH: str = "data/trade_logs.db"

    # Rate Limiting (requests per second for KIS API)
    # Conservative limit to avoid EGW00201 "초당 거래건수 초과" errors.
    # KIS API real limit is ~2 RPS; 2.0 provides maximum safety.
    RATE_LIMIT_RPS: float = 2.0

    # Trading mode
    MODE: str = Field(default="paper", pattern="^(paper|live)$")

    # Simulated USD cash for VTS (paper) overseas trading.
    # KIS VTS overseas balance API returns errors for most accounts.
    # This value is used as a fallback when the balance API returns 0 in paper mode.
    PAPER_OVERSEAS_CASH: float = Field(default=50000.0, ge=0.0)
    USD_BUFFER_MIN: float = Field(default=1000.0, ge=0.0)
    US_MIN_PRICE: float = Field(default=5.0, ge=0.0)
    OVERNIGHT_EXCEPTION_ENABLED: bool = True

    # Trading frequency mode (daily = batch API calls, realtime = per-stock calls)
    TRADE_MODE: str = Field(default="daily", pattern="^(daily|realtime)$")
    DAILY_SESSIONS: int = Field(default=4, ge=1, le=10)
    SESSION_INTERVAL_HOURS: int = Field(default=6, ge=1, le=24)
    ORDER_BLACKOUT_ENABLED: bool = True
    ORDER_BLACKOUT_WINDOWS_KST: str = "23:30-00:10"
    ORDER_BLACKOUT_QUEUE_MAX: int = Field(default=500, ge=10, le=5000)

    # Pre-Market Planner
    PRE_MARKET_MINUTES: int = Field(default=30, ge=10, le=120)
    MAX_SCENARIOS_PER_STOCK: int = Field(default=5, ge=1, le=10)
    PLANNER_TIMEOUT_SECONDS: int = Field(default=60, ge=10, le=300)
    DEFENSIVE_PLAYBOOK_ON_FAILURE: bool = True
    RESCAN_INTERVAL_SECONDS: int = Field(default=300, ge=60, le=900)

    # Market selection (comma-separated market codes)
    ENABLED_MARKETS: str = "KR,US"

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

    # Telegram Commands (optional)
    TELEGRAM_COMMANDS_ENABLED: bool = True
    TELEGRAM_POLLING_INTERVAL: float = 1.0  # seconds

    # Telegram notification type filters (granular control)
    # circuit_breaker is always sent regardless — safety-critical
    TELEGRAM_NOTIFY_TRADES: bool = True           # BUY/SELL execution alerts
    TELEGRAM_NOTIFY_MARKET_OPEN_CLOSE: bool = True  # Market open/close alerts
    TELEGRAM_NOTIFY_FAT_FINGER: bool = True       # Fat-finger rejection alerts
    TELEGRAM_NOTIFY_SYSTEM_EVENTS: bool = True    # System start/shutdown alerts
    TELEGRAM_NOTIFY_PLAYBOOK: bool = True         # Playbook generated/failed alerts
    TELEGRAM_NOTIFY_SCENARIO_MATCH: bool = True   # Scenario matched alerts (most frequent)
    TELEGRAM_NOTIFY_ERRORS: bool = True           # Error alerts

    # Overseas ranking API (KIS endpoint/TR_ID may vary by account/product)
    # Override these from .env if your account uses different specs.
    OVERSEAS_RANKING_ENABLED: bool = True
    OVERSEAS_RANKING_FLUCT_TR_ID: str = "HHDFS76290000"
    OVERSEAS_RANKING_VOLUME_TR_ID: str = "HHDFS76270000"
    OVERSEAS_RANKING_FLUCT_PATH: str = (
        "/uapi/overseas-stock/v1/ranking/updown-rate"
    )
    OVERSEAS_RANKING_VOLUME_PATH: str = (
        "/uapi/overseas-stock/v1/ranking/volume-surge"
    )

    # Dashboard (optional)
    DASHBOARD_ENABLED: bool = False
    DASHBOARD_HOST: str = "127.0.0.1"
    DASHBOARD_PORT: int = Field(default=8080, ge=1, le=65535)

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
        from src.markets.schedule import expand_market_codes

        raw = [m.strip() for m in self.ENABLED_MARKETS.split(",") if m.strip()]
        return expand_market_codes(raw)
