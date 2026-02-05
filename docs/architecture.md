# System Architecture

## Overview

Self-evolving AI trading agent for global stock markets via KIS (Korea Investment & Securities) API. The main loop in `src/main.py` orchestrates four components across multiple markets with two trading modes: daily (batch API calls) or realtime (per-stock decisions).

## Trading Modes

The system supports two trading frequency modes controlled by the `TRADE_MODE` environment variable:

### Daily Mode (default)

Optimized for Gemini Free tier API limits (20 calls/day):

- **Batch decisions**: 1 API call per market per session
- **Fixed schedule**: 4 sessions per day at 6-hour intervals (configurable)
- **API efficiency**: Processes all stocks in a market simultaneously
- **Use case**: Free tier users, cost-conscious deployments
- **Configuration**:
  ```bash
  TRADE_MODE=daily
  DAILY_SESSIONS=4        # Sessions per day (1-10)
  SESSION_INTERVAL_HOURS=6  # Hours between sessions (1-24)
  ```

**Example**: With 2 markets (US, KR) and 4 sessions/day = 8 API calls/day (within 20 call limit)

### Realtime Mode

High-frequency trading with individual stock analysis:

- **Per-stock decisions**: 1 API call per stock per cycle
- **60-second interval**: Continuous monitoring
- **Use case**: Production deployments with Gemini paid tier
- **Configuration**:
  ```bash
  TRADE_MODE=realtime
  ```

**Note**: Realtime mode requires Gemini API subscription due to high call volume.

## Core Components

### 1. Broker (`src/broker/`)

**KISBroker** (`kis_api.py`) — Async KIS API client for domestic Korean market

- Automatic OAuth token refresh (valid for 24 hours)
- Leaky-bucket rate limiter (10 requests per second)
- POST body hash-key signing for order authentication
- Custom SSL context with disabled hostname verification for VTS (virtual trading) endpoint due to known certificate mismatch

**OverseasBroker** (`overseas.py`) — KIS overseas stock API wrapper

- Reuses KISBroker infrastructure (session, token, rate limiter) via composition
- Supports 9 global markets: US (NASDAQ/NYSE/AMEX), Japan, Hong Kong, China (Shanghai/Shenzhen), Vietnam (Hanoi/HCM)
- Different API endpoints for overseas price/balance/order operations

**Market Schedule** (`src/markets/schedule.py`) — Timezone-aware market management

- `MarketInfo` dataclass with timezone, trading hours, lunch breaks
- Automatic DST handling via `zoneinfo.ZoneInfo`
- `is_market_open()` checks weekends, trading hours, lunch breaks
- `get_open_markets()` returns currently active markets
- `get_next_market_open()` finds next market to open and when

**New API Methods** (added in v0.9.0):
- `fetch_market_rankings()` — Fetch volume surge rankings from KIS API
- `get_daily_prices()` — Fetch OHLCV history for technical analysis

### 2. Analysis (`src/analysis/`)

**VolatilityAnalyzer** (`volatility.py`) — Technical indicator calculations

- ATR (Average True Range) for volatility measurement
- RSI (Relative Strength Index) using Wilder's smoothing method
- Price change percentages across multiple timeframes
- Volume surge ratios and price-volume divergence
- Momentum scoring (0-100 scale)
- Breakout/breakdown pattern detection

**SmartVolatilityScanner** (`smart_scanner.py`) — Python-first filtering pipeline

- **Step 1**: Fetch volume rankings from KIS API (top 30 stocks)
- **Step 2**: Calculate RSI and volume ratio for each stock
- **Step 3**: Apply filters:
  - Volume ratio >= `VOL_MULTIPLIER` (default 2.0x previous day)
  - RSI < `RSI_OVERSOLD_THRESHOLD` (30) OR RSI > `RSI_MOMENTUM_THRESHOLD` (70)
- **Step 4**: Score candidates by RSI extremity (60%) + volume surge (40%)
- **Step 5**: Return top N candidates (default 3) for AI analysis
- **Fallback**: Uses static watchlist if ranking API unavailable
- **Realtime mode only**: Daily mode uses batch processing for API efficiency

**Benefits:**
- Reduces Gemini API calls from 20-30 stocks to 1-3 qualified candidates
- Fast Python-based filtering before expensive AI judgment
- Logs selection context (RSI, volume_ratio, signal, score) for Evolution system

### 3. Brain (`src/brain/gemini_client.py`)

**GeminiClient** — AI decision engine powered by Google Gemini

- Constructs structured prompts from market data
- Parses JSON responses into `TradeDecision` objects (`action`, `confidence`, `rationale`)
- Forces HOLD when confidence < threshold (default 80)
- Falls back to safe HOLD on any parse/API error
- Handles markdown-wrapped JSON, malformed responses, invalid actions

### 4. Risk Manager (`src/core/risk_manager.py`)

**RiskManager** — Safety circuit breaker and order validation

⚠️ **READ-ONLY by policy** (see [`docs/agents.md`](./agents.md))

- **Circuit Breaker**: Halts all trading via `SystemExit` when daily P&L drops below -3.0%
  - Threshold may only be made stricter, never relaxed
  - Calculated as `(total_eval - purchase_total) / purchase_total * 100`
- **Fat-Finger Protection**: Rejects orders exceeding 30% of available cash
  - Must always be enforced, cannot be disabled

### 5. Notifications (`src/notifications/telegram_client.py`)

**TelegramClient** — Real-time event notifications via Telegram Bot API

- Sends alerts for trades, circuit breakers, fat-finger rejections, system events
- Non-blocking: failures are logged but never crash trading
- Rate-limited: 1 message/second default to respect Telegram API limits
- Auto-disabled when credentials missing
- Gracefully handles API errors, network timeouts, invalid tokens

**Notification Types:**
- Trade execution (BUY/SELL with confidence)
- Circuit breaker trips (critical alert)
- Fat-finger protection triggers (order rejection)
- Market open/close events
- System startup/shutdown status

**Setup:** See [src/notifications/README.md](../src/notifications/README.md) for bot creation and configuration.

### 6. Evolution (`src/evolution/optimizer.py`)

**StrategyOptimizer** — Self-improvement loop

- Analyzes high-confidence losing trades from SQLite
- Asks Gemini to generate new `BaseStrategy` subclasses
- Validates generated strategies by running full pytest suite
- Simulates PR creation for human review
- Only activates strategies that pass all tests

## Data Flow

### Realtime Mode (with Smart Scanner)

```
┌─────────────────────────────────────────────────────────────┐
│ Main Loop (60s cycle per market)                           │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Market Schedule Check             │
        │ - Get open markets                │
        │ - Filter by enabled markets       │
        │ - Wait if all closed              │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Smart Scanner (Python-first)      │
        │ - Fetch volume rankings (KIS)    │
        │ - Get 20d price history per stock│
        │ - Calculate RSI(14) + vol ratio  │
        │ - Filter: vol>2x AND RSI extreme │
        │ - Return top 3 qualified stocks  │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ For Each Qualified Candidate      │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Broker: Fetch Market Data        │
        │ - Domestic: orderbook + balance  │
        │ - Overseas: price + balance      │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Calculate P&L                     │
        │ pnl_pct = (eval - cost) / cost   │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Brain: Get Decision (AI)          │
        │ - Build prompt with market data   │
        │ - Call Gemini API                 │
        │ - Parse JSON response             │
        │ - Return TradeDecision            │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Risk Manager: Validate Order      │
        │ - Check circuit breaker           │
        │ - Check fat-finger limit          │
        │ - Raise if validation fails       │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Broker: Execute Order             │
        │ - Domestic: send_order()          │
        │ - Overseas: send_overseas_order() │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Notifications: Send Alert         │
        │ - Trade execution notification    │
        │ - Non-blocking (errors logged)    │
        │ - Rate-limited to 1/sec           │
        └──────────────────┬────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Database: Log Trade               │
        │ - SQLite (data/trades.db)         │
        │ - Track: action, confidence,      │
        │   rationale, market, exchange     │
        │ - NEW: selection_context (JSON)   │
        │   - RSI, volume_ratio, signal     │
        │   - For Evolution optimization    │
        └───────────────────────────────────┘
```

## Database Schema

**SQLite** (`src/db.py`)

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    action TEXT NOT NULL,          -- BUY | SELL | HOLD
    confidence INTEGER NOT NULL,   -- 0-100
    rationale TEXT,
    quantity INTEGER,
    price REAL,
    pnl REAL DEFAULT 0.0,
    market TEXT DEFAULT 'KR',       -- KR | US_NASDAQ | JP | etc.
    exchange_code TEXT DEFAULT 'KRX', -- KRX | NASD | NYSE | etc.
    selection_context TEXT          -- JSON: {rsi, volume_ratio, signal, score}
);
```

**Selection Context** (new in v0.9.0): Stores scanner selection criteria as JSON:
```json
{
  "rsi": 28.5,
  "volume_ratio": 2.7,
  "signal": "oversold",
  "score": 85.2
}
```

Enables Evolution system to analyze correlation between selection criteria and trade outcomes.

Auto-migration: Adds `market`, `exchange_code`, and `selection_context` columns if missing for backward compatibility.

## Configuration

**Pydantic Settings** (`src/config.py`)

Loaded from `.env` file:

```bash
# Required
KIS_APP_KEY=your_app_key
KIS_APP_SECRET=your_app_secret
KIS_ACCOUNT_NO=XXXXXXXX-XX
GEMINI_API_KEY=your_gemini_key

# Optional
MODE=paper                    # paper | live
DB_PATH=data/trades.db
CONFIDENCE_THRESHOLD=80
MAX_LOSS_PCT=3.0
MAX_ORDER_PCT=30.0
ENABLED_MARKETS=KR,US_NASDAQ  # Comma-separated market codes

# Trading Mode (API efficiency)
TRADE_MODE=daily              # daily | realtime
DAILY_SESSIONS=4              # Sessions per day (daily mode only)
SESSION_INTERVAL_HOURS=6      # Hours between sessions (daily mode only)

# Telegram Notifications (optional)
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ENABLED=true

# Smart Scanner (optional, realtime mode only)
RSI_OVERSOLD_THRESHOLD=30    # 0-50, oversold threshold
RSI_MOMENTUM_THRESHOLD=70    # 50-100, momentum threshold
VOL_MULTIPLIER=2.0           # Minimum volume ratio (2.0 = 200%)
SCANNER_TOP_N=3              # Max qualified candidates per scan
```

Tests use in-memory SQLite (`DB_PATH=":memory:"`) and dummy credentials via `tests/conftest.py`.

## Error Handling

### Connection Errors (Broker API)
- Retry with exponential backoff (2^attempt seconds)
- Max 3 retries per stock
- After exhaustion, skip stock and continue with next

### API Quota Errors (Gemini)
- Return safe HOLD decision with confidence=0
- Log error but don't crash
- Agent continues trading on next cycle

### Circuit Breaker Tripped
- Immediately halt via `SystemExit`
- Log critical message
- Requires manual intervention to restart

### Market Closed
- Wait until next market opens
- Use `get_next_market_open()` to calculate wait time
- Sleep until market open time

### Telegram API Errors
- Log warning but continue trading
- Missing credentials → auto-disable notifications
- Network timeout → skip notification, no retry
- Invalid token → log error, trading unaffected
- Rate limit exceeded → queued via rate limiter

**Guarantee**: Notification failures never interrupt trading operations.
