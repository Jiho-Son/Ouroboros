"""Strictly typed configuration loaded from environment variables."""

from __future__ import annotations

import json

from pydantic import Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings — loaded from .env or environment variables."""

    _executable_quote_gap_caps_by_market_cache: dict[str, float] = PrivateAttr(default_factory=dict)

    # KIS Open API
    KIS_APP_KEY: str
    KIS_APP_SECRET: str
    KIS_ACCOUNT_NO: str  # format: "XXXXXXXX-XX"
    KIS_BASE_URL: str = "https://openapivts.koreainvestment.com:29443"
    KIS_WS_URL: str | None = None
    KIS_WS_PATH: str = "/tryitout"

    # LLM provider
    LLM_PROVIDER: str = Field(default="gemini", pattern="^(gemini|ollama)$")

    # Google Gemini
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # Ollama
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "llama3.2"
    OLLAMA_REQUEST_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0.0, le=600.0)

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
    BUY_CHASE_MIN_INTRADAY_GAIN_PCT: float = Field(default=4.0, ge=0.0, le=100.0)
    BUY_CHASE_MAX_PULLBACK_FROM_HIGH_PCT: float = Field(default=0.5, ge=0.0, le=20.0)
    SELL_REENTRY_PRICE_GUARD_SECONDS: int = Field(default=120, ge=1, le=3600)
    EXECUTABLE_QUOTE_MAX_GAP_PCT: float = Field(default=2.0, ge=0.0, le=100.0)
    EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON: str = "{}"

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
    US_MIN_PRICE: float = Field(default=5.0, ge=1.0)
    STAGED_EXIT_BE_ARM_PCT: float = Field(default=1.2, gt=0.0, le=30.0)
    STAGED_EXIT_ARM_PCT: float = Field(default=3.0, gt=0.0, le=100.0)
    STOPLOSS_REENTRY_COOLDOWN_MINUTES: int = Field(default=120, ge=1, le=1440)
    KR_ATR_STOP_MULTIPLIER_K: float = Field(default=2.0, ge=0.1, le=10.0)
    KR_ATR_STOP_MIN_PCT: float = Field(default=-2.0, le=0.0)
    KR_ATR_STOP_MAX_PCT: float = Field(default=-7.0, le=0.0)
    OVERNIGHT_EXCEPTION_ENABLED: bool = True
    SESSION_RISK_RELOAD_ENABLED: bool = True
    SESSION_RISK_PROFILES_JSON: str = "{}"

    # Trading frequency mode (daily = batch API calls, realtime = per-stock calls)
    TRADE_MODE: str = Field(default="daily", pattern="^(daily|realtime)$")
    DAILY_SESSIONS: int = Field(default=4, ge=1, le=10)
    SESSION_INTERVAL_HOURS: int = Field(default=6, ge=1, le=24)
    ORDER_BLACKOUT_ENABLED: bool = True
    ORDER_BLACKOUT_WINDOWS_KST: str = "23:30-00:10"
    ORDER_BLACKOUT_QUEUE_MAX: int = Field(default=500, ge=10, le=5000)
    BLACKOUT_RECOVERY_PRICE_REVALIDATION_ENABLED: bool = True
    BLACKOUT_RECOVERY_MAX_PRICE_DRIFT_PCT: float = Field(default=5.0, ge=0.0, le=100.0)
    REALTIME_HARD_STOP_ENABLED: bool = True
    REALTIME_HARD_STOP_RETRY_DELAY_SECONDS: float = Field(default=1.0, ge=0.0, le=30.0)
    REALTIME_HARD_STOP_MAX_RETRIES: int = Field(default=1000, ge=1, le=100000)

    # Pre-Market Planner
    PRE_MARKET_MINUTES: int = Field(default=30, ge=10, le=120)
    MAX_SCENARIOS_PER_STOCK: int = Field(default=5, ge=1, le=10)
    PLANNER_TIMEOUT_SECONDS: int = Field(default=60, ge=10, le=300)
    DEFENSIVE_PLAYBOOK_ON_FAILURE: bool = True
    SCORECARD_BUY_GUARD_LOOKBACK_DAYS: int = Field(default=0, ge=0, le=30)
    SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL: float | None = Field(default=None, le=0.0)
    SCORECARD_BUY_GUARD_MIN_WIN_RATE: float | None = Field(default=None, ge=0.0, le=100.0)
    SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS: int | None = Field(
        default=None,
        ge=1,
        le=30,
    )
    SCORECARD_BUY_GUARD_ACTION: str = Field(
        default="block_buy",
        pattern="^(block_buy|defensive)$",
    )
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
    TELEGRAM_NOTIFY_TRADES: bool = True  # BUY/SELL execution alerts
    TELEGRAM_NOTIFY_MARKET_OPEN_CLOSE: bool = True  # Market open/close alerts
    TELEGRAM_NOTIFY_FAT_FINGER: bool = True  # Fat-finger rejection alerts
    TELEGRAM_NOTIFY_SYSTEM_EVENTS: bool = True  # System start/shutdown alerts
    TELEGRAM_NOTIFY_PLAYBOOK: bool = True  # Playbook generated/failed alerts
    TELEGRAM_NOTIFY_SCENARIO_MATCH: bool = True  # Scenario matched alerts (most frequent)
    TELEGRAM_NOTIFY_ERRORS: bool = True  # Error alerts

    # Overseas ranking API (KIS endpoint/TR_ID may vary by account/product)
    # Override these from .env if your account uses different specs.
    OVERSEAS_RANKING_ENABLED: bool = True
    OVERSEAS_RANKING_FLUCT_TR_ID: str = "HHDFS76290000"
    OVERSEAS_RANKING_VOLUME_TR_ID: str = "HHDFS76270000"
    OVERSEAS_RANKING_FLUCT_PATH: str = "/uapi/overseas-stock/v1/ranking/updown-rate"
    OVERSEAS_RANKING_VOLUME_PATH: str = "/uapi/overseas-stock/v1/ranking/volume-surge"

    # Dashboard (optional)
    DASHBOARD_ENABLED: bool = False
    DASHBOARD_HOST: str = "127.0.0.1"
    DASHBOARD_PORT: int = Field(default=8080, ge=1, le=65535)
    LIVE_RUNTIME_LOCK_PATH: str = "data/overnight/live_runtime.lock"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def _validate_selected_llm_provider(self) -> Settings:
        if self.LLM_PROVIDER == "gemini" and not self.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        return self

    @model_validator(mode="after")
    def _validate_executable_quote_gap_caps_by_market(self) -> Settings:
        self._executable_quote_gap_caps_by_market_cache = (
            self._parse_executable_quote_gap_caps_by_market()
        )
        return self

    def _parse_executable_quote_gap_caps_by_market(self) -> dict[str, float]:
        try:
            parsed = json.loads(self.EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(
                "EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON must be valid JSON object"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError("EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON must be a JSON object")
        normalized: dict[str, float] = {}
        for raw_market, raw_value in parsed.items():
            market_code = str(raw_market).strip().upper()
            if not market_code:
                continue
            try:
                cap_pct = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON values must be numeric"
                ) from exc
            if cap_pct < 0.0 or cap_pct > 100.0:
                raise ValueError(
                    "EXECUTABLE_QUOTE_MAX_GAP_PCT_BY_MARKET_JSON values must be within [0, 100]"
                )
            normalized[market_code] = cap_pct
        return normalized

    @property
    def account_number(self) -> str:
        return self.KIS_ACCOUNT_NO.split("-")[0]

    @property
    def account_product_code(self) -> str:
        return self.KIS_ACCOUNT_NO.split("-")[1]

    @property
    def kis_ws_url(self) -> str:
        if self.KIS_WS_URL:
            return self.KIS_WS_URL
        if "openapivts" in self.KIS_BASE_URL:
            return "ws://ops.koreainvestment.com:31000"
        return "ws://ops.koreainvestment.com:21000"

    @property
    def enabled_market_list(self) -> list[str]:
        """Parse ENABLED_MARKETS into list of market codes."""
        from src.markets.schedule import expand_market_codes

        raw = [m.strip() for m in self.ENABLED_MARKETS.split(",") if m.strip()]
        return expand_market_codes(raw)

    @property
    def executable_quote_gap_caps_by_market(self) -> dict[str, float]:
        """Market-specific executable quote gap caps (percent)."""
        return self._executable_quote_gap_caps_by_market_cache

    @property
    def llm_model(self) -> str:
        """Return the active model name for the configured LLM provider."""
        if self.LLM_PROVIDER == "ollama":
            return self.OLLAMA_MODEL
        return self.GEMINI_MODEL
