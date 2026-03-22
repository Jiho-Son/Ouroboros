# System Architecture

## Overview

Self-evolving AI trading agent for global stock markets via KIS (Korea Investment & Securities) API. The main loop in `src/main.py` orchestrates components across multiple markets with two trading modes: daily (batch API calls) or realtime (per-stock decisions).

**v2 Proactive Playbook Architecture**: The system uses a "plan once, execute locally" approach. Pre-market, the AI generates a playbook of scenarios (one LLM provider call per market per day). During trading hours, a local scenario engine matches live market data against these pre-computed scenarios — no additional AI calls needed. This dramatically reduces API costs and latency.

## Trading Modes

The system supports two trading frequency modes controlled by the `TRADE_MODE` environment variable:

### Daily Mode (default)

Optimized for cost-sensitive/provider-limited deployments:

- **Batch decisions**: 1 API call per market per session
- **Fixed schedule**: 4 sessions per day at 6-hour intervals (configurable)
- **API efficiency**: Processes all stocks in a market simultaneously
- **Use case**: Cost-conscious deployments or providers with tighter rate/cost budgets
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
- **Use case**: Production deployments with higher-throughput LLM capacity
- **Configuration**:
  ```bash
  TRADE_MODE=realtime
  ```

**Note**: Realtime mode requires an LLM provider that can tolerate higher call volume; local Ollama and paid hosted models are the intended options.

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

**Overseas Ranking API Methods** (added in v0.10.x):
- `fetch_overseas_rankings()` — Fetch overseas ranking universe (fluctuation / volume)
- Ranking endpoint paths and TR_IDs are configurable via environment variables

### 2. Analysis (`src/analysis/`)

**VolatilityAnalyzer** (`volatility.py`) — Technical indicator calculations

- ATR (Average True Range) for volatility measurement
- RSI (Relative Strength Index) using Wilder's smoothing method
- Price change percentages across multiple timeframes
- Volume surge ratios and price-volume divergence
- Momentum scoring (0-100 scale)
- Breakout/breakdown pattern detection

**TripleBarrierLabeler** (`triple_barrier.py`) — Financial time-series labeling (v2)

- Triple Barrier method: upper (take-profit), lower (stop-loss), time barrier
- First-touch labeling: labels confirmed by whichever barrier is breached first
- `max_holding_minutes` (calendar-minute) time barrier — session-aware, bar-period independent
- Tie-break mode: `"stop_first"` (conservative) or `"take_first"`
- Feature-label strict separation to prevent look-ahead bias

**BacktestPipeline** (`backtest_pipeline.py`) — End-to-end validation pipeline (v2)

- `run_v2_backtest_pipeline()`: cost guard → triple barrier labeling → walk-forward splits → fold scoring
- `BacktestPipelineResult`: artifact contract for reproducible output
- `fold_has_leakage()`: leakage detection utility

**WalkForwardSplit** (`walk_forward_split.py`) — Time-series validation (v2)

- Fold-based walk-forward splits (no random shuffling)
- Purge/Embargo: excludes N bars before/after fold boundaries to prevent data leakage

**BacktestExecutionModel** (`backtest_execution_model.py`) — Conservative fill simulation (v2/v3)

- Session-aware slippage: KRX_REG 5bps, NXT_AFTER 15bps, US_REG 3bps, US_PRE/DAY 30-50bps
- Order failure rate simulation per session
- Partial fill rate simulation with min/max ratio bounds
- Unfavorable-direction fill assumption (no simple close-price fill)

**BacktestCostGuard** (`backtest_cost_guard.py`) — Cost model validator (v2)

- `validate_backtest_cost_model()`: fail-fast check that session cost assumptions are present
- Enforces realistic cost assumptions before any backtest run proceeds

**SmartVolatilityScanner** (`smart_scanner.py`) — Python-first filtering pipeline

- **Domestic (KR)**:
  - **Step 1**: Fetch domestic fluctuation ranking as primary universe
  - **Step 2**: Fetch domestic volume ranking for liquidity bonus
  - **Step 3**: Compute volatility-first score (max of daily change% and intraday range%)
  - **Step 4**: Apply liquidity bonus and return top N candidates
- **Overseas (US/JP/HK/CN/VN)**:
  - **Step 1**: Fetch overseas ranking universe (fluctuation rank + volume rank bonus)
  - **Step 2**: Compute volatility-first score (max of daily change% and intraday range%)
  - **Step 3**: Apply liquidity bonus from volume ranking
  - **Step 4**: Return top N candidates (default 3)
- **Fallback (overseas only)**: If ranking API is unavailable, uses dynamic universe
  from runtime active symbols + recent traded symbols + current holdings (no static watchlist)
- **Both modes**: Realtime 중심이지만 Daily 경로(`run_daily_session()`)에서도 후보 선별에 사용

**Benefits:**
- Reduces Gemini API calls from 20-30 stocks to 1-3 qualified candidates
- Fast Python-based filtering before expensive AI judgment
- Logs selection context (RSI-compatible proxy, volume_ratio, signal, score) for Evolution system

### 3. Brain (`src/brain/`)

**DecisionEngine** (`decision_engine.py`) — provider-agnostic AI decision engine

- Constructs structured prompts from market data
- Parses JSON responses into `TradeDecision` objects (`action`, `confidence`, `rationale`)
- Forces HOLD when confidence < threshold (default 80)
- Falls back to safe HOLD on any parse/API error
- Handles markdown-wrapped JSON, malformed responses, invalid actions

**Provider Selection** (`llm_client.py`) — low-level provider adapters

- `LLM_PROVIDER=gemini|ollama` selects the raw prompt execution backend
- `DecisionEngine` and `EvolutionOptimizer` share the same provider factory
- `GeminiProvider` and `OllamaProvider` encapsulate provider-specific SDK/HTTP behavior behind the shared abstraction

**PromptOptimizer** (`prompt_optimizer.py`) — Token efficiency optimization

- Reduces prompt size while preserving decision quality
- Caches optimized prompts

**ContextSelector** (`context_selector.py`) — Relevant context selection for prompts

- Selects appropriate context layers for current market conditions

### 4. Risk Manager & Session Policy (`src/core/`)

**RiskManager** (`risk_manager.py`) — Safety circuit breaker and order validation

> **READ-ONLY by policy** (see [`docs/agents.md`](./agents.md))

- **Circuit Breaker**: Halts all trading via `SystemExit` when daily P&L drops below -3.0%
  - Threshold may only be made stricter, never relaxed
  - Calculated as `(total_eval - purchase_total) / purchase_total * 100`
- **Fat-Finger Protection**: Rejects orders exceeding 30% of available cash
  - Must always be enforced, cannot be disabled

**OrderPolicy** (`order_policy.py`) — Session classification and order type enforcement (v3)

- `classify_session_id()`: Classifies current KR/US session from KST clock
  - KR: `NXT_PRE` (08:00-08:50), `KRX_REG` (09:00-15:30), `NXT_AFTER` (15:30-20:00)
  - US: `US_DAY` (10:00-18:00), `US_PRE` (18:00-23:30), `US_REG` (23:30-06:00), `US_AFTER` (06:00-07:00)
- Low-liquidity session detection: `NXT_AFTER`, `US_PRE`, `US_DAY`, `US_AFTER`
- Market order forbidden in low-liquidity sessions (`OrderPolicyRejected` raised)
- Limit/IOC/FOK orders always allowed

**KillSwitch** (`kill_switch.py`) — Emergency trading halt orchestration (v2)

- Fixed 5-step atomic sequence:
  1. Block new orders (`new_orders_blocked = True`)
  2. Cancel all unfilled orders
  3. Refresh order state (query final status)
  4. Reduce risk (force-close or reduce positions)
  5. Snapshot state + send Telegram alert
- Async, injectable step callables — each step individually testable
- Highest priority: overrides overnight exception and all other rules

**BlackoutManager** (`blackout_manager.py`) — KIS maintenance window handling (v3)

- Configurable blackout windows (e.g., `23:30-00:10 KST`)
- `queue_order()`: Queues order intent during blackout, enforces max queue size
- `pop_recovery_batch()`: Returns queued intents after recovery
- Recovery revalidation path (in `src/main.py`):
  - Stale BUY drop (position already exists)
  - Stale SELL drop (position absent)
  - `validate_order_policy()` rechecked
  - Price drift check (>5% → drop, configurable via `BLACKOUT_RECOVERY_MAX_PRICE_DRIFT_PCT`)

### 5. Strategy (`src/strategy/`)

**PositionStateMachine** (`position_state_machine.py`) — 4-state sell state machine (v2)

- States: `HOLDING` → `BE_LOCK` → `ARMED` → `EXITED`
  - `HOLDING`: Normal holding
  - `BE_LOCK`: Profit ≥ `be_arm_pct` — stop-loss elevated to break-even
  - `ARMED`: Profit ≥ `arm_pct` — peak-tracking trailing stop active
  - `EXITED`: Position closed
- `promote_state()`: Immediately elevates to highest admissible state (handles gaps/skips)
- `evaluate_exit_first()`: EXITED conditions checked before state promotion
- Monotonic: states only move up, never down

**ExitRules** (`exit_rules.py`) — 4-layer composite exit logic (v2)

- **Hard Stop**: `unrealized <= hard_stop_pct` (always enforced, ATR-adaptive for KR)
- **Break-Even Lock**: Once in BE_LOCK/ARMED, exit if price falls to entry price
- **ATR Trailing Stop**: `trailing_stop_price = peak_price - (atr_multiplier_k × ATR)`
- **Model Signal**: Exit if `pred_down_prob >= model_prob_threshold AND liquidity_weak`
- Realtime KR websocket highs may raise the cached `peak_price`, but favorable-exit decisions still execute in the regular trading-cycle staged-exit path.
- `evaluate_exit()`: Returns `ExitEvaluation` with next state, exit flag, reason, trailing price
- `ExitRuleConfig`: Frozen dataclass with all tunable parameters

**Pre-Market Planner** (`pre_market_planner.py`) — AI playbook generation

- Runs before market open (configurable `PRE_MARKET_MINUTES`, default 30)
- Generates scenario-based playbooks via single Gemini API call per market
- Handles timeout (`PLANNER_TIMEOUT_SECONDS`, default 60) with defensive playbook fallback
- Persists playbooks to database for audit trail

**Scenario Engine** (`scenario_engine.py`) — Local scenario matching

- Matches live market data against pre-computed playbook scenarios
- No AI calls during trading hours — pure Python matching logic
- Returns matched scenarios with confidence scores
- BUY execution adds a final session-high chase guard: if intraday gain is already stretched and price is still pinned near the session high, the order path suppresses BUY to HOLD until price pulls back further.
- BUY execution also checks the latest SELL inside `SELL_REENTRY_PRICE_GUARD_SECONDS`.
- The session-aware window is resolved by one shared helper before the pure recent-SELL comparison runs, so the guard itself does not import `session_risk`.
- The comparison stays strict (`current_price > last_sell_price`) and does not add a fee/slippage buffer until a market-aware cost model exists.
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
- Live runtime lock path is configurable via `LIVE_RUNTIME_LOCK_PATH`, and
  non-`main` worktrees should use the shared runtime scripts so `LOG_DIR`,
  dashboard port, tmux session prefix, and lock path auto-scope per branch.
- Serves static HTML frontend

**10 API Endpoints:**

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
| `/api/pnl/history` | GET | P&L history time series |
| `/api/positions` | GET | Current open positions |

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
- Asks the configured LLM provider to generate new `BaseStrategy` subclasses
- Validates generated strategies by running full pytest suite
- Simulates PR creation for human review
- Only activates strategies that pass all tests

**DailyReview** (`daily_review.py`) — End-of-day review

- Generates comprehensive trade performance summary
- Stores results in L6_DAILY context layer
- Tracks win rate, raw realized P&L (market quote currency), confidence accuracy

**DailyScorecard** (`scorecard.py`) — Performance scoring

- Calculates daily metrics (trades, P&L, win rate, avg confidence)
- `total_pnl` is stored as raw realized P&L, not a percentage
- Planner prompt rendering maps market -> raw PnL unit explicitly (`KR -> KRW`, `US -> USD`) for both self-market and cross-market scorecard sections, and falls back to `UNKNOWN_CURRENCY` for unsupported or blank market codes until the mapping is extended
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
        │ Smart Scanner (Python-first)      │
        │ - Domestic: fluctuation rank     │
        │   + volume rank bonus            │
        │   + volatility-first scoring     │
        │ - Overseas: ranking universe     │
        │   + volatility-first scoring     │
        │ - Fallback: dynamic universe     │
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
    session_id TEXT DEFAULT 'UNKNOWN',  -- v3: KRX_REG | NXT_AFTER | US_REG | US_PRE | ...
    selection_context TEXT,        -- JSON: {rsi, volume_ratio, signal, score}
    decision_id TEXT,             -- Links to decision_logs
    strategy_pnl REAL,            -- v3: Core strategy P&L (separated from FX)
    fx_pnl REAL DEFAULT 0.0,      -- v3: FX gain/loss for USD trades (schema ready, activation pending)
    mode TEXT                     -- paper | live
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
    session_id TEXT DEFAULT 'UNKNOWN',  -- v3: session when decision was made
    action TEXT,
    confidence INTEGER,
    rationale TEXT,
    context_snapshot TEXT,         -- JSON: full context at decision time
    input_data TEXT,              -- JSON: market data used
    outcome_pnl REAL,
    outcome_accuracy INTEGER,
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
    status TEXT NOT NULL DEFAULT 'pending',  -- pending → generated → active → expired
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
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key   # required when LLM_PROVIDER=gemini

# Optional — LLM Provider
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2
OLLAMA_REQUEST_TIMEOUT_SECONDS=60

# Optional — Trading Mode
MODE=live                     # runtime paper execution banned (#426)
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

# Optional — v2 Exit Rules (State Machine)
STAGED_EXIT_BE_ARM_PCT=1.2    # Break-even lock threshold (%)
STAGED_EXIT_ARM_PCT=3.0       # Armed state threshold (%)
KR_ATR_STOP_MULTIPLIER_K=2.0  # ATR multiplier for KR dynamic hard stop
KR_ATR_STOP_MIN_PCT=-2.0      # KR hard stop floor (must tighten, negative)
KR_ATR_STOP_MAX_PCT=-7.0      # KR hard stop ceiling (loosest, negative)

# Optional — v2 Trade Filters
STOP_LOSS_COOLDOWN_MINUTES=120  # Cooldown after stop-loss before re-entry (same ticker)
US_MIN_PRICE=5.0              # Minimum US stock price for BUY ($)
BUY_CHASE_MIN_INTRADAY_GAIN_PCT=4.0      # Minimum day gain before the chase guard activates
BUY_CHASE_MAX_PULLBACK_FROM_HIGH_PCT=0.5 # Maximum pullback from session high still treated as "buying the top"
SELL_REENTRY_PRICE_GUARD_SECONDS=120     # Block only strictly higher re-buys above the latest SELL price for a short window

# Optional — v3 Session Risk Management
SESSION_RISK_RELOAD_ENABLED=true   # Reload risk params at session boundaries
SESSION_RISK_PROFILES_JSON="{}"    # Per-session overrides JSON: {"KRX_REG": {"be_arm_pct": 1.0}}
OVERNIGHT_EXCEPTION_ENABLED=true   # Allow holding through session close (conditions apply)

# Optional — v3 Blackout (KIS maintenance windows)
ORDER_BLACKOUT_ENABLED=true
ORDER_BLACKOUT_WINDOWS_KST=23:30-00:10   # Comma-separated: "HH:MM-HH:MM"
ORDER_BLACKOUT_QUEUE_MAX=500             # Max queued orders during blackout
BLACKOUT_RECOVERY_PRICE_REVALIDATION_ENABLED=true
BLACKOUT_RECOVERY_MAX_PRICE_DRIFT_PCT=5.0  # Drop recovery order if price drifted >5%

# Optional — Smart Scanner (realtime mode only)
RSI_OVERSOLD_THRESHOLD=30     # 0-50, oversold threshold
RSI_MOMENTUM_THRESHOLD=70     # 50-100, momentum threshold
VOL_MULTIPLIER=2.0            # Minimum volume ratio (2.0 = 200%)
SCANNER_TOP_N=3               # Max qualified candidates per scan

# Optional — Dashboard
DASHBOARD_ENABLED=false       # Enable FastAPI dashboard
DASHBOARD_HOST=127.0.0.1      # Dashboard bind address
DASHBOARD_PORT=8080           # Dashboard port (1-65535)
LIVE_RUNTIME_LOCK_PATH=data/overnight/live_runtime.lock

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

# Position Sizing (optional)
POSITION_SIZING_ENABLED=true
POSITION_BASE_ALLOCATION_PCT=5.0
POSITION_MIN_ALLOCATION_PCT=1.0
POSITION_MAX_ALLOCATION_PCT=10.0
POSITION_VOLATILITY_TARGET_SCORE=50.0

# Legacy/compat scanner thresholds (kept for backward compatibility)
RSI_OVERSOLD_THRESHOLD=30
RSI_MOMENTUM_THRESHOLD=70
VOL_MULTIPLIER=2.0

# Overseas Ranking API (optional override; account-dependent)
OVERSEAS_RANKING_ENABLED=true
OVERSEAS_RANKING_FLUCT_TR_ID=HHDFS76200100
OVERSEAS_RANKING_VOLUME_TR_ID=HHDFS76200200
OVERSEAS_RANKING_FLUCT_PATH=/uapi/overseas-price/v1/quotations/inquire-updown-rank
OVERSEAS_RANKING_VOLUME_PATH=/uapi/overseas-price/v1/quotations/inquire-volume-rank
```

Tests use in-memory SQLite (`DB_PATH=":memory:"`) and dummy credentials via `tests/conftest.py`.

## Error Handling

### Connection Errors (Broker API)
- Retry with exponential backoff (2^attempt seconds)
- Max 3 retries per stock
- After exhaustion, skip stock and continue with next

### API Quota / Provider Errors (LLM)
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
