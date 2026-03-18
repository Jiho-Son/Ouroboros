# Sell-to-Rebuy Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent immediate higher-price rebuys right after a SELL while preserving normal lower-price or expired-window re-entry behavior.

**Architecture:** Keep the fix in the execution layer. Add one DB helper for the latest SELL, one pure guard helper for the recent-SELL rule, and call that helper from both realtime and daily BUY paths before order submission.

**Tech Stack:** Python, pytest, sqlite helpers, Pydantic settings, existing `src/main.py` trade orchestration

---

### Task 1: Lock the current failure in tests

**Files:**
- Modify: `tests/test_main.py`
- Modify: `tests/test_db.py`

**Step 1: Write the failing realtime-path regression test**

Add a `trading_cycle()` test that:
- seeds the DB with `BUY` then `SELL`,
- uses a current price above the last SELL price but below the generic session-high chase threshold,
- expects the final decision to be `HOLD` and no BUY order to be sent.

**Step 2: Run the realtime regression to verify it fails**

Run: `pytest tests/test_main.py -k "recent_sell and trading_cycle" -v`
Expected: FAIL because the current code still sends the BUY.

**Step 3: Write the failing daily-path regression test**

Add a `run_daily_session()` test with the same recent-SELL setup and a domestic quote snapshot that is not blocked by the session-high chase guard.

**Step 4: Run the daily regression to verify it fails**

Run: `pytest tests/test_main.py -k "recent_sell and daily_session" -v`
Expected: FAIL because the daily BUY path still sends the BUY.

**Step 5: Add a DB helper test**

Add `tests/test_db.py` coverage for the latest-SELL lookup helper so the integration tests are not the only proof of correctness.

### Task 2: Implement the narrow recent-SELL guard

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Modify: `src/db.py`
- Modify: `src/core/order_helpers.py`
- Modify: `src/main.py`

**Step 1: Add the setting**

Introduce `SELL_REENTRY_PRICE_GUARD_SECONDS` in `Settings` and `.env.example` with a default of `120`.

**Step 2: Add the DB helper**

Implement a helper in `src/db.py` that returns the most recent SELL trade for a stock/market, including timestamp and price.

**Step 3: Add the pure guard helper**

Implement a helper in `src/core/order_helpers.py` that:
- accepts `market`, `action`, `current_price`, latest SELL timestamp/price, and `settings`,
- resolves the guard window via `_resolve_market_setting`,
- returns whether BUY should be blocked plus the derived evidence needed for rationale/logging.

**Step 4: Wire the helper into realtime BUY suppression**

Call the helper from `_evaluate_trading_cycle_decision()` after duplicate/min-price/stop-loss checks and before the session-high chase guard.

**Step 5: Wire the helper into daily BUY suppression**

Call the same helper from the daily-session BUY suppression path with the matching stock snapshot data.

### Task 3: Verify boundaries and document behavior

**Files:**
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`

**Step 1: Add helper boundary coverage**

Cover:
- higher-price re-entry blocked inside the window,
- lower-price re-entry allowed inside the window,
- higher-price re-entry allowed after expiry,
- no SELL history leaves BUY unchanged.

**Step 2: Update architecture notes**

Document that BUY execution now checks recent SELL price/time before sending a new order.

**Step 3: Run focused verification**

Run: `pytest tests/test_db.py -k "latest_sell" -v`
Expected: PASS.

Run: `pytest tests/test_main.py -k "recent_sell or rebuy_after_sell" -v`
Expected: PASS.

**Step 4: Run touched-surface checks**

Run: `ruff check src/config.py src/db.py src/core/order_helpers.py src/main.py tests/test_db.py tests/test_main.py`
Expected: PASS.

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS.
