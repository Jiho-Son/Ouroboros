# Context Tree: Multi-Layered Memory Management

The context tree implements **Pillar 2** of The Ouroboros: hierarchical memory management across 7 time horizons, from real-time market data to generational trading wisdom.

## Overview

Instead of a flat memory structure, The Ouroboros maintains a **7-tier context tree** where each layer represents a different time horizon and level of abstraction:

```
L1 (Legacy)      ←  Cumulative wisdom across generations
  ↑
L2 (Annual)      ←  Yearly performance metrics
  ↑
L3 (Quarterly)   ←  Quarterly strategy adjustments
  ↑
L4 (Monthly)     ←  Monthly portfolio rebalancing
  ↑
L5 (Weekly)      ←  Weekly stock selection
  ↑
L6 (Daily)       ←  Daily trade logs
  ↑
L7 (Real-time)   ←  Live market data
```

Data flows **bottom-up**: real-time trades aggregate into daily summaries, which roll up to weekly, then monthly, quarterly, annual, and finally into permanent legacy knowledge.

## The 7 Layers

### L7: Real-time
**Retention**: 7 days
**Timeframe format**: `YYYY-MM-DD` (same-day)
**Content**: Current positions, live quotes, orderbook snapshots, tick-by-tick volatility

**Use cases**:
- Immediate execution decisions
- Stop-loss triggers
- Real-time P&L tracking

**Example keys**:
- `current_position_{stock_code}`: Current holdings
- `live_price_{stock_code}`: Latest quote
- `volatility_5m_{stock_code}`: 5-minute rolling volatility

### L6: Daily
**Retention**: 90 days
**Timeframe format**: `YYYY-MM-DD`
**Content**: Daily trade logs, end-of-day P&L, market summaries, decision accuracy

**Use cases**:
- Daily performance review
- Identify patterns in recent trading
- Backtest strategy adjustments

**Example keys**:
- `total_pnl`: Daily profit/loss
- `trade_count`: Number of trades
- `win_rate`: Percentage of profitable trades
- `avg_confidence`: Average Gemini confidence

### L5: Weekly
**Retention**: 1 year
**Timeframe format**: `YYYY-Www` (ISO week, e.g., `2026-W06`)
**Content**: Weekly stock selection, sector rotation, volatility regime classification

**Use cases**:
- Weekly strategy adjustment
- Sector momentum tracking
- Identify hot/cold markets

**Example keys**:
- `weekly_pnl`: Week's total P&L
- `top_performers`: Best-performing stocks
- `sector_focus`: Dominant sectors
- `avg_confidence`: Weekly average confidence

### L4: Monthly
**Retention**: 2 years
**Timeframe format**: `YYYY-MM`
**Content**: Monthly portfolio rebalancing, risk exposure analysis, drawdown recovery

**Use cases**:
- Monthly performance reporting
- Risk exposure adjustment
- Correlation analysis

**Example keys**:
- `monthly_pnl`: Month's total P&L
- `sharpe_ratio`: Risk-adjusted return
- `max_drawdown`: Largest peak-to-trough decline
- `rebalancing_notes`: Manual insights

### L3: Quarterly
**Retention**: 3 years
**Timeframe format**: `YYYY-Qn` (e.g., `2026-Q1`)
**Content**: Quarterly strategy pivots, market phase detection (bull/bear/sideways), macro regime changes

**Use cases**:
- Strategic pivots (e.g., growth → value)
- Macro regime classification
- Long-term pattern recognition

**Example keys**:
- `quarterly_pnl`: Quarter's total P&L
- `market_phase`: Bull/Bear/Sideways
- `strategy_adjustments`: Major changes made
- `lessons_learned`: Key insights

### L2: Annual
**Retention**: 10 years
**Timeframe format**: `YYYY`
**Content**: Yearly returns, Sharpe ratio, max drawdown, win rate, strategy effectiveness

**Use cases**:
- Annual performance review
- Multi-year trend analysis
- Strategy benchmarking

**Example keys**:
- `annual_pnl`: Year's total P&L
- `sharpe_ratio`: Annual risk-adjusted return
- `win_rate`: Yearly win percentage
- `best_strategy`: Most successful strategy
- `worst_mistake`: Biggest lesson learned

### L1: Legacy
**Retention**: Forever
**Timeframe format**: `LEGACY` (single timeframe)
**Content**: Cumulative trading history, core principles, generational wisdom

**Use cases**:
- Long-term philosophy
- Foundational rules
- Lessons that transcend market cycles

**Example keys**:
- `total_pnl`: All-time profit/loss
- `years_traded`: Trading longevity
- `avg_annual_pnl`: Long-term average return
- `core_principles`: Immutable trading rules
- `greatest_trades`: Hall of fame
- `never_again`: Permanent warnings

## Usage

### Setting Context

```python
from src.context import ContextLayer, ContextStore
from src.db import init_db

conn = init_db("data/ouroboros.db")
store = ContextStore(conn)

# Store daily P&L
store.set_context(
    layer=ContextLayer.L6_DAILY,
    timeframe="2026-02-04",
    key="total_pnl",
    value=1234.56
)

# Store weekly insight
store.set_context(
    layer=ContextLayer.L5_WEEKLY,
    timeframe="2026-W06",
    key="top_performers",
    value=["005930", "000660", "035720"]  # JSON-serializable
)

# Store legacy wisdom
store.set_context(
    layer=ContextLayer.L1_LEGACY,
    timeframe="LEGACY",
    key="core_principles",
    value=[
        "Cut losses fast",
        "Let winners run",
        "Never average down on losing positions"
    ]
)
```

### Retrieving Context

```python
# Get a specific value
pnl = store.get_context(ContextLayer.L6_DAILY, "2026-02-04", "total_pnl")
# Returns: 1234.56

# Get all keys for a timeframe
daily_summary = store.get_all_contexts(ContextLayer.L6_DAILY, "2026-02-04")
# Returns: {"total_pnl": 1234.56, "trade_count": 10, "win_rate": 60.0, ...}

# Get all data for a layer (any timeframe)
all_daily = store.get_all_contexts(ContextLayer.L6_DAILY)
# Returns: {"total_pnl": 1234.56, "trade_count": 10, ...} (latest timeframes first)

# Get the latest timeframe
latest = store.get_latest_timeframe(ContextLayer.L6_DAILY)
# Returns: "2026-02-04"
```

### Automatic Aggregation

The `ContextAggregator` rolls up data from lower to higher layers:

```python
from src.context.aggregator import ContextAggregator

aggregator = ContextAggregator(conn)

# Aggregate daily metrics from trades
aggregator.aggregate_daily_from_trades("2026-02-04")

# Roll up weekly from daily
aggregator.aggregate_weekly_from_daily("2026-W06")

# Roll up all layers at once (bottom-up)
aggregator.run_all_aggregations()
```

**Aggregation schedule** (recommended):
- **L7 → L6**: Every midnight (daily rollup)
- **L6 → L5**: Every Sunday (weekly rollup)
- **L5 → L4**: First day of each month (monthly rollup)
- **L4 → L3**: First day of quarter (quarterly rollup)
- **L3 → L2**: January 1st (annual rollup)
- **L2 → L1**: On demand (major milestones)

### Context Cleanup

Expired contexts are automatically deleted based on retention policies:

```python
# Manual cleanup
deleted = store.cleanup_expired_contexts()
# Returns: {ContextLayer.L7_REALTIME: 42, ContextLayer.L6_DAILY: 15, ...}
```

**Retention policies** (defined in `src/context/layer.py`):
- L1: Forever
- L2: 10 years
- L3: 3 years
- L4: 2 years
- L5: 1 year
- L6: 90 days
- L7: 7 days

## Integration with Gemini Brain

The context tree provides hierarchical memory for decision-making:

```python
from src.brain.gemini_client import GeminiClient

# Build prompt with multi-layer context
def build_enhanced_prompt(stock_code: str, store: ContextStore) -> str:
    # L7: Real-time data
    current_price = store.get_context(ContextLayer.L7_REALTIME, "2026-02-04", f"live_price_{stock_code}")

    # L6: Recent daily performance
    yesterday_pnl = store.get_context(ContextLayer.L6_DAILY, "2026-02-03", "total_pnl")

    # L5: Weekly trend
    weekly_data = store.get_all_contexts(ContextLayer.L5_WEEKLY, "2026-W06")

    # L1: Core principles
    principles = store.get_context(ContextLayer.L1_LEGACY, "LEGACY", "core_principles")

    return f"""
    Analyze {stock_code} for trading decision.

    Current price: {current_price}
    Yesterday's P&L: {yesterday_pnl}
    This week: {weekly_data}

    Core principles:
    {chr(10).join(f'- {p}' for p in principles)}

    Decision (BUY/SELL/HOLD):
    """
```

## Database Schema

```sql
-- Context storage
CREATE TABLE contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL,          -- L1_LEGACY, L2_ANNUAL, ..., L7_REALTIME
    timeframe TEXT NOT NULL,      -- "LEGACY", "2026", "2026-Q1", "2026-02", "2026-W06", "2026-02-04"
    key TEXT NOT NULL,            -- "total_pnl", "win_rate", "core_principles", etc.
    value TEXT NOT NULL,          -- JSON-serialized value
    created_at TEXT NOT NULL,     -- ISO 8601 timestamp
    updated_at TEXT NOT NULL,     -- ISO 8601 timestamp
    UNIQUE(layer, timeframe, key)
);

-- Layer metadata
CREATE TABLE context_metadata (
    layer TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    retention_days INTEGER,       -- NULL = keep forever
    aggregation_source TEXT       -- Parent layer for rollup
);

-- Indices for fast queries
CREATE INDEX idx_contexts_layer ON contexts(layer);
CREATE INDEX idx_contexts_timeframe ON contexts(timeframe);
CREATE INDEX idx_contexts_updated ON contexts(updated_at);
```

## Best Practices

1. **Write to leaf layers only** — Never manually write to L1-L5; let aggregation populate them
2. **Aggregate regularly** — Schedule aggregation jobs to keep higher layers fresh
3. **Query specific timeframes** — Use `get_context(layer, timeframe, key)` for precise retrieval
4. **Clean up periodically** — Run `cleanup_expired_contexts()` weekly to free space
5. **Preserve L1 forever** — Legacy wisdom should never expire
6. **Use JSON-serializable values** — Store dicts, lists, strings, numbers (not custom objects)

## Testing

See `tests/test_context.py` for comprehensive test coverage (18 tests, 100% coverage on context modules).

```bash
pytest tests/test_context.py -v
```

## References

- **Implementation**: `src/context/`
  - `layer.py`: Layer definitions and metadata
  - `store.py`: CRUD operations
  - `aggregator.py`: Bottom-up aggregation logic
- **Database**: `src/db.py` (table initialization)
- **Tests**: `tests/test_context.py`
- **Related**: Pillar 2 (Multi-layered Context Management)
