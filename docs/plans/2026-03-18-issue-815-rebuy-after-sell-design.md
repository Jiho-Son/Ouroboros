# OOR-815 Sell-to-Rebuy Guard Design

## Context

- Ticket `OOR-815` reports frequent rebuys one minute after a SELL, often at a higher price than the last exit.
- Current BUY suppression only covers duplicate holdings, insufficient-balance cooldowns, stop-loss cooldowns, the US minimum-price filter, and the session-high chase guard.
- The stop-loss cooldown only applies when `trade_pnl < 0`, so profitable or flat exits leave no state that can stop an immediate higher-price re-entry.
- The session-high chase guard from `OOR-816` only catches names pinned near the session high. It does not cover a higher re-entry that is still below the session-high threshold.

## Approaches Considered

### 1. Extend stop-loss cooldown to every SELL

- Reuse the existing cooldown map and set it for all completed SELL trades.
- Pros: smallest implementation delta.
- Cons: over-broad. It blocks even lower-price or same-price re-entries and changes the meaning of a stop-loss-specific setting that existing docs/tests already rely on.

### 2. Add a recent-SELL higher-price guard at the execution gate

- Query the most recent SELL trade for the same market/symbol.
- Block BUY only when:
  - the SELL happened within a short guard window, and
  - the current price is above the last SELL price.
- Pros: matches the ticket language directly, preserves lower-price re-entry, and stays inside the deterministic execution layer.
- Cons: needs a small DB helper and one more BUY suppression check in both BUY paths.

### 3. Move the rule into planner/scenario generation

- Teach the playbook/scenario engine about recent exits and higher-price re-entry avoidance.
- Pros: AI-visible and expressive.
- Cons: too broad for the issue, depends on planner quality, and still needs an execution-time safety net.

## Recommendation

Choose approach 2.

It addresses the exact failure mode without redefining the existing stop-loss cooldown semantics. The order gate is also the only place that reliably covers both AI-generated and fallback BUY decisions.

## Proposed Behavior

- Add `SELL_REENTRY_PRICE_GUARD_SECONDS` as a tunable execution-time setting.
- Define a BUY as blocked by the new guard when all of the following are true:
  - the latest SELL trade for the same market/symbol exists,
  - that SELL happened within `SELL_REENTRY_PRICE_GUARD_SECONDS`,
  - `current_price > latest_sell_price`.
- When blocked, convert the decision to `HOLD` with rationale that includes:
  - elapsed seconds since the SELL,
  - the latest SELL price,
  - the current price,
  - the guard window.
- When the window has expired, or the current price is at or below the last SELL price, preserve the normal BUY path.

## Default Threshold

- `SELL_REENTRY_PRICE_GUARD_SECONDS = 120`

Two minutes is narrow enough to avoid broad behavior changes, while still covering the immediate next one-minute evaluation cycle plus minor timing skew.

## Integration Points

- `src/db.py`
  - add a helper that returns the latest SELL trade metadata needed by the guard.
- `src/core/order_helpers.py`
  - add a pure helper that evaluates the recent-SELL price/time rule.
- `src/main.py`
  - call the helper from both BUY suppression paths after duplicate/min-price/stop-loss checks and before the generic session-high chase guard.
- `src/config.py` and `.env.example`
  - expose the new setting.
- `docs/architecture.md`
  - document the new BUY suppression rule.

## Verification Plan

- Reproduce the current bug with an integration test that records `BUY -> SELL`, then confirms `trading_cycle()` still submits a higher-price BUY inside the new guard window.
- Add a second regression in `run_daily_session()` so both BUY execution paths enforce the same rule.
- Add helper coverage for:
  - blocked higher-price re-entry inside the window,
  - allowed lower-price re-entry inside the window,
  - allowed higher-price re-entry after expiry,
  - allowed BUY when no SELL history exists.
