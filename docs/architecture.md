# System Architecture

## Overview

Self-evolving AI trading agent for global stock markets via KIS (Korea Investment & Securities) API. The main loop in `src/main.py` orchestrates components across multiple markets with two trading modes: daily (batch API calls) or realtime (per-stock decisions).

**v2 Proactive Playbook Architecture**: The system uses a "plan once, execute locally" approach. Pre-market, the AI generates a playbook of scenarios (one Gemini API call per market per day). During trading hours, a local scenario engine matches live market data against these pre-computed scenarios — no additional AI calls needed. This dramatically reduces API costs and latency.

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
- Leaky-bucket rate limiter (configurable RPS, default 2.0)
- POST body hash-key signing for order authentication
- Custom SSL context with disabled hostname verification for VTS (virtual trading) endpoint due to known certificate mismatch
- `fetch_market_rankings()` — Fetch volume surge rankings from KIS API
- `get_daily_prices()` — Fetch OHLCV history for technical analysis

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
- 10 global markets defined (KR, US_NASDAQ, US_NYSE, US_AMEX, JP, HK, CN_SHA, CN_SZA, VN_HNX, VN_HSX)

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

### 3. Brain (`src/brain/`)

**GeminiClient** (`gemini_client.py`) — AI decision engine powered by Google Gemini

- Constructs structured prompts from market data
- Parses JSON responses into `TradeDecision` objects (`action`, `confidence`, `rationale`)
- Forces HOLD when confidence < threshold (default 80)
- Falls back to safe HOLD on any parse/API error
- Handles markdown-wrapped JSON, malformed responses, invalid actions

**PromptOptimizer** (`prompt_optimizer.py`) — Token efficiency optimization

- Reduces prompt size while preserving decision quality
- Caches optimized prompts

**ContextSelector** (`context_selector.py`) — Relevant context selection for prompts

- Selects appropriate context layers for current market conditions

### 4. Risk Manager (`src/core/risk_manager.py`)

**RiskManager** — Safety circuit breaker and order validation

> **READ-ONLY by policy** (see [`docs/agents.md`](./agents.md))

- **Circuit Breaker**: Halts all trading via `SystemExit` when daily P&L drops below -3.0%
  - Threshold may only be made stricter, never relaxed
  - Calculated as `(total_eval - purchase_total) / purchase_total * 100`
- **Fat-Finger Protection**: Rejects orders exceeding 30% of available cash
  - Must always be enforced, cannot be disabled

### 5. Strategy (`src/strategy/`)

**Pre-Market Planner** (`pre_market_planner.py`) — AI playbook generation

- Runs before market open (configurable `PRE_MARKET_MINUTES`, default 30)
- Generates scenario-based playbooks via single Gemini API call per market
- Handles timeout (`PLANNER_TIMEOUT_SECONDS`, default 60) with defensive playbook fallback
- Persists playbooks to database for audit trail

**Scenario Engine** (`scenario_engine.py`) — Local scenario matching

- Matches live market data against pre-computed playbook scenarios
- No AI calls during trading hours — pure Python matching logic
- Returns matched scenarios with confidence scores
- Configurable `MAX_SCENARIOS_PER_STOCK` (default 5)
- Periodic rescan at `RESCAN_INTERVAL_SECONDS` (default 300)

**Playbook Store** (`playbook_store.py`) — Playbook persistence

- SQLite-backed storage for daily playbooks
- Date and market-based retrieval
- Status tracking (generated, active, expired)

**Models** (`models.py`) — Pydantic data models

- Scenario, Playbook, MatchResult, and related type definitions

### 6. Context System (`src/context/`)

**Context Store** (`store.py`) — L1-L7 hierarchical memory

- 7-layer context system (see [docs/context-tree.md](./context-tree.md)):
  - L1: Tick-level (real-time price)
  - L2: Intraday (session summary)
  - L3: Daily (end-of-day)
  - L4: Weekly (trend analysis)
  - L5: Monthly (strategy review)
  - L6: Daily Review (scorecard)
  - L7: Evolution (long-term learning)
- Key-value storage with timeframe tagging
- SQLite persistence in `contexts` table

**Context Scheduler** (`scheduler.py`) — Periodic aggregation

- Scheduled summarization from lower to higher layers
- Configurable aggregation intervals

**Context Summarizer** (`summarizer.py`) — Layer summarization

- Aggregates lower-layer data into higher-layer summaries

### 7. Dashboard (`src/dashboard/`)

**FastAPI App** (`app.py`) — Read-only monitoring dashboard

- Runs as daemon thread when enabled (`--dashboard` CLI flag or `DASHBOARD_ENABLED=true`)
- Configurable host/port (`DASHBOARD_HOST`, `DASHBOARD_PORT`, default `127.0.0.1:8080`)
- Serves static HTML frontend

**8 API Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Static HTML dashboard |
| `/api/status` | GET | Daily trading status by market |
| `/api/playbook/{date}` | GET | Playbook for specific date and market |
| `/api/scorecard/{date}` | GET | Daily scorecard from L6_DAILY context |
| `/api/performance` | GET | Trading performance metrics (by market + combined) |
| `/api/context/{layer}` | GET | Query context by layer (L1-L7) |
| `/api/decisions` | GET | Decision log entries with outcomes |
| `/api/scenarios/active` | GET | Today's matched scenarios |

### 8. Notifications (`src/notifications/telegram_client.py`)

**TelegramClient** — Real-time event notifications via Telegram Bot API

- Sends alerts for trades, circuit breakers, fat-finger rejections, system events
- Non-blocking: failures are logged but never crash trading
- Rate-limited: 1 message/second default to respect Telegram API limits
- Auto-disabled when credentials missing

**TelegramCommandHandler** — Bidirectional command interface

- Long polling from Telegram API (configurable `TELEGRAM_POLLING_INTERVAL`)
- 9 interactive commands: `/help`, `/status`, `/positions`, `/report`, `/scenarios`, `/review`, `/dashboard`, `/stop`, `/resume`
- Authorization filtering by `TELEGRAM_CHAT_ID`
- Enable/disable via `TELEGRAM_COMMANDS_ENABLED` (default: true)

**Notification Types:**
- Trade execution (BUY/SELL with confidence)
- Circuit breaker trips (critical alert)
- Fat-finger protection triggers (order rejection)
- Market open/close events
- System startup/shutdown status
- Playbook generation results
- Stop-loss monitoring alerts

### 9. Evolution (`src/evolution/`)

**StrategyOptimizer** (`optimizer.py`) — Self-improvement loop

- Analyzes high-confidence losing trades from SQLite
- Asks Gemini to generate new `BaseStrategy` subclasses
- Validates generated strategies by running full pytest suite
- Simulates PR creation for human review
- Only activates strategies that pass all tests

**DailyReview** (`daily_review.py`) — End-of-day review

- Generates comprehensive trade performance summary
- Stores results in L6_DAILY context layer
- Tracks win rate, P&L, confidence accuracy

**DailyScorecard** (`scorecard.py`) — Performance scoring

- Calculates daily metrics (trades, P&L, win rate, avg confidence)
- Enables trend tracking across days

**Stop-Loss Monitoring** — Real-time position protection

- Monitors positions against stop-loss levels from playbook scenarios
- Sends Telegram alerts when thresholds approached or breached

### 10. Decision Logger (`src/logging/decision_logger.py`)

**DecisionLogger** — Comprehensive audit trail

- Logs every trading decision with full context snapshot
- Captures input data, rationale, confidence, and outcomes
- Supports outcome tracking (P&L, accuracy) for post-analysis
- Stored in `decision_logs` table with indexed queries
- Review workflow support (reviewed flag, review notes)

### 11. Data Integration (`src/data/`)

**External Data Sources** (optional):

- `news_api.py` — News sentiment data
- `market_data.py` — Extended market data
- `economic_calendar.py` — Economic event calendar

### 12. Backup (`src/backup/`)

**Disaster Recovery** (see [docs/disaster_recovery.md](./disaster_recovery.md)):

- `scheduler.py` — Automated backup scheduling
- `exporter.py` — Data export to various formats
- `cloud_storage.py` — S3-compatible cloud backup
- `health_monitor.py` — Backup integrity verification

## Data Flow

### Playbook Mode (Daily — Primary v2 Flow)

```
┌─────────────────────────────────────────────────────────────┐
│ Pre-Market Phase (before market open)                       │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Pre-Market Planner               │
        │ - 1 Gemini API call per market   │
        │ - Generate scenario playbook     │
        │ - Store in playbooks table       │
        └──────────────────┬───────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Trading Hours (market open → close)                         │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Market Schedule Check            │
        │ - Get open markets               │
        │ - Filter by enabled markets      │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Scenario Engine (local)          │
        │ - Match live data vs playbook    │
        │ - No AI calls needed             │
        │ - Return matched scenarios       │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Risk Manager: Validate Order     │
        │ - Check circuit breaker          │
        │ - Check fat-finger limit         │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Broker: Execute Order            │
        │ - Domestic: send_order()         │
        │ - Overseas: send_overseas_order()│
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Decision Logger + DB             │
        │ - Full audit trail               │
        │ - Context snapshot               │
        │ - Telegram notification          │
        └──────────────────┬───────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Post-Market Phase                                           │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Daily Review + Scorecard         │
        │ - Performance summary            │
        │ - Store in L6_DAILY context      │
        │ - Evolution learning             │
        └──────────────────────────────────┘
```

### Realtime Mode (with Smart Scanner)

```
┌─────────────────────────────────────────────────────────────┐
│ Main Loop (60s cycle per market)                            │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Market Schedule Check            │
        │ - Get open markets               │
        │ - Filter by enabled markets      │
        │ - Wait if all closed             │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Smart Scanner (Python-first)     │
        │ - Fetch volume rankings (KIS)    │
        │ - Get 20d price history per stock│
        │ - Calculate RSI(14) + vol ratio  │
        │ - Filter: vol>2x AND RSI extreme │
        │ - Return top 3 qualified stocks  │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ For Each Qualified Candidate     │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Broker: Fetch Market Data        │
        │ - Domestic: orderbook + balance  │
        │ - Overseas: price + balance      │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Brain: Get Decision (AI)         │
        │ - Build prompt with market data  │
        │ - Call Gemini API                │
        │ - Parse JSON response            │
        │ - Return TradeDecision           │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Risk Manager: Validate Order     │
        │ - Check circuit breaker          │
        │ - Check fat-finger limit         │
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Broker: Execute Order            │
        │ - Domestic: send_order()         │
        │ - Overseas: send_overseas_order()│
        └──────────────────┬───────────────┘
                           │
                           ▼
        ┌──────────────────────────────────┐
        │ Decision Logger + Notifications  │
        │ - Log trade to SQLite            │
        │ - selection_context (JSON)       │
        │ - Telegram notification          │
        └──────────────────────────────────┘
```

## Database Schema

**SQLite** (`src/db.py`) — Database: `data/trades.db`

### trades
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
    market TEXT DEFAULT 'KR',
    exchange_code TEXT DEFAULT 'KRX',
    selection_context TEXT,        -- JSON: {rsi, volume_ratio, signal, score}
    decision_id TEXT              -- Links to decision_logs
);
```

### contexts
```sql
CREATE TABLE contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL,           -- L1 through L7
    timeframe TEXT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,           -- JSON data
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Indices: idx_contexts_layer, idx_contexts_timeframe, idx_contexts_updated
```

### decision_logs
```sql
CREATE TABLE decision_logs (
    decision_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    stock_code TEXT,
    market TEXT,
    exchange_code TEXT,
    action TEXT,
    confidence INTEGER,
    rationale TEXT,
    context_snapshot TEXT,         -- JSON: full context at decision time
    input_data TEXT,              -- JSON: market data used
    outcome_pnl REAL,
    outcome_accuracy REAL,
    reviewed INTEGER DEFAULT 0,
    review_notes TEXT
);
-- Indices: idx_decision_logs_timestamp, idx_decision_logs_reviewed, idx_decision_logs_confidence
```

### playbooks
```sql
CREATE TABLE playbooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    market TEXT NOT NULL,
    status TEXT DEFAULT 'generated',
    playbook_json TEXT NOT NULL,   -- Full playbook with scenarios
    generated_at TEXT NOT NULL,
    token_count INTEGER,
    scenario_count INTEGER,
    match_count INTEGER DEFAULT 0
);
-- Indices: idx_playbooks_date, idx_playbooks_market
```

### context_metadata
```sql
CREATE TABLE context_metadata (
    layer TEXT PRIMARY KEY,
    description TEXT,
    retention_days INTEGER,
    aggregation_source TEXT
);
```

## Configuration

**Pydantic Settings** (`src/config.py`)

Loaded from `.env` file:

```bash
# Required
KIS_APP_KEY=your_app_key
KIS_APP_SECRET=your_app_secret
KIS_ACCOUNT_NO=XXXXXXXX-XX
GEMINI_API_KEY=your_gemini_key

# Optional — Trading Mode
MODE=paper                    # paper | live
TRADE_MODE=daily              # daily | realtime
DAILY_SESSIONS=4              # Sessions per day (daily mode only)
SESSION_INTERVAL_HOURS=6      # Hours between sessions (daily mode only)

# Optional — Database
DB_PATH=data/trades.db

# Optional — Risk
CONFIDENCE_THRESHOLD=80
MAX_LOSS_PCT=3.0
MAX_ORDER_PCT=30.0

# Optional — Markets
ENABLED_MARKETS=KR,US         # Comma-separated market codes
RATE_LIMIT_RPS=2.0            # KIS API requests per second

# Optional — Pre-Market Planner (v2)
PRE_MARKET_MINUTES=30         # Minutes before market open to generate playbook
MAX_SCENARIOS_PER_STOCK=5     # Max scenarios per stock in playbook
PLANNER_TIMEOUT_SECONDS=60    # Timeout for playbook generation
DEFENSIVE_PLAYBOOK_ON_FAILURE=true  # Fallback on AI failure
RESCAN_INTERVAL_SECONDS=300   # Scenario rescan interval during trading

# Optional — Smart Scanner (realtime mode only)
RSI_OVERSOLD_THRESHOLD=30     # 0-50, oversold threshold
RSI_MOMENTUM_THRESHOLD=70     # 50-100, momentum threshold
VOL_MULTIPLIER=2.0            # Minimum volume ratio (2.0 = 200%)
SCANNER_TOP_N=3               # Max qualified candidates per scan

# Optional — Dashboard
DASHBOARD_ENABLED=false       # Enable FastAPI dashboard
DASHBOARD_HOST=127.0.0.1      # Dashboard bind address
DASHBOARD_PORT=8080           # Dashboard port (1-65535)

# Optional — Telegram
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ENABLED=true
TELEGRAM_COMMANDS_ENABLED=true   # Enable bidirectional commands
TELEGRAM_POLLING_INTERVAL=1.0    # Command polling interval (seconds)

# Optional — Backup
BACKUP_ENABLED=false
BACKUP_DIR=data/backups
S3_ENDPOINT_URL=...
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET_NAME=...
S3_REGION=...

# Optional — External Data
NEWS_API_KEY=...
NEWS_API_PROVIDER=...
MARKET_DATA_API_KEY=...
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

### Playbook Generation Failure
- Timeout → fall back to defensive playbook (`DEFENSIVE_PLAYBOOK_ON_FAILURE`)
- API error → use previous day's playbook if available
- No playbook → skip pre-market phase, fall back to direct AI calls

**Guarantee**: Notification and dashboard failures never interrupt trading operations.
