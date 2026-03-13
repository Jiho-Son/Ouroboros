# Buy Entry Timing Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent BUY orders from firing when a symbol is already sharply extended and still trading at or near the session high.

**Architecture:** Keep the fix at the order-decision gate instead of changing the planner contract. Add one pure high-chase helper, reuse session-risk-resolved thresholds, and call the helper from both realtime and daily BUY flows so the behavior is consistent across trade modes.

**Tech Stack:** Python, pytest, Pydantic settings, existing `src/main.py` order orchestration

---

### Task 1: Lock the failure in tests

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing realtime-path regression test**

Add a `trading_cycle()` test that:
- returns a BUY scenario,
- injects `current_price`, `price_change_pct`, and `session_high_price` values representing a late-session chase,
- expects the decision to be logged as HOLD and no order to be sent.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "chasing_session_high and trading_cycle" -v`
Expected: FAIL because the current code still sends the BUY order.

**Step 3: Write the failing daily-path regression test**

Add a `run_daily_session()` regression using a domestic stock where:
- the quote response includes a session high,
- the scenario engine returns BUY,
- the test expects no order because the symbol is still pinned near the high after a large intraday move.

**Step 4: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "chasing_session_high and daily_session" -v`
Expected: FAIL because the daily path does not apply the guard yet.

### Task 2: Implement the narrow chase guard

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Modify: `src/core/order_helpers.py`
- Modify: `src/main.py`

**Step 1: Add configuration defaults**

Introduce:
- `BUY_CHASE_MIN_INTRADAY_GAIN_PCT`
- `BUY_CHASE_MAX_PULLBACK_FROM_HIGH_PCT`

Expose them in both `Settings` and `.env.example`.

**Step 2: Add the pure helper**

Create a helper in `src/core/order_helpers.py` that:
- accepts `market`, `current_price`, `session_high_price`, `price_change_pct`, and `settings`,
- resolves thresholds via `_resolve_market_setting`,
- returns a structured result or tuple containing:
  - whether BUY should be blocked,
  - pullback-from-high percentage,
  - the resolved thresholds.

**Step 3: Wire the helper into realtime BUY suppression**

Call the helper in `_evaluate_trading_cycle_decision()` after the existing duplicate/min-price/cooldown checks and before order execution.

**Step 4: Wire the helper into daily BUY suppression**

Call the same helper in `_process_daily_session_stock()` and ensure the domestic daily quote enrichment also records `session_high_price`.

### Task 3: Verify boundaries and document behavior

**Files:**
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`

**Step 1: Add helper boundary tests**

Cover:
- blocked at high with large gain,
- allowed after sufficient pullback,
- allowed when gain threshold is not met,
- allowed when quote high is missing.

**Step 2: Update architecture notes**

Document that BUY execution now applies an extended-high chase guard before sending orders.

**Step 3: Run focused verification**

Run: `pytest tests/test_main.py -k "chasing_session_high or fx_buffer_guard" -v`
Expected: PASS with the new guard coverage.

**Step 4: Run touched-surface lint**

Run: `ruff check src/config.py src/core/order_helpers.py src/main.py tests/test_main.py`
Expected: PASS.
