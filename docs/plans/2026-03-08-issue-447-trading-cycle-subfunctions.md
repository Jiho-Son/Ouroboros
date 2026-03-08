# trading_cycle Sub-Functions Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split `src/main.py` `trading_cycle()` into focused helper functions for data collection, scenario evaluation, order execution, and logging without changing runtime behavior.

**Architecture:** Keep `trading_cycle()` in `src/main.py` as the orchestration entrypoint. Extract private helpers in the same file first so call sites, imports, and test surfaces remain stable. Use existing `tests/test_main.py` trading-cycle coverage as the regression safety net, and add one small structure-preserving test only if an extraction boundary is otherwise unprotected.

**Tech Stack:** Python 3.12, pytest, asyncio, existing main.py collaborators

---

## Constraints

- Preserve `trading_cycle()` signature and side effects.
- Keep helpers private in `src/main.py`; no module move in this ticket.
- No logic changes beyond dependency plumbing needed for extraction.
- Use `python3`, not `python`.
- Do not touch unrelated untracked plan files from the parent worktree.

### Task 1: Lock in regression coverage

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

Add one focused test that exercises a full actionable path currently crossing all four stages:
- market data fetch
- scenario evaluation to `BUY` or `SELL`
- decision logging
- successful order execution / trade logging

Reuse existing fixtures/mocks from `tests/test_main.py`. The test should assert externally visible behavior only, not helper internals.

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py -q -k "<new_test_name>"`

Expected: FAIL for the intended missing assertion or setup mismatch.

**Step 3: Adjust only the test until it fails for the right reason**

Do not change production code in this task.

**Step 4: Run baseline trading-cycle subset**

Run: `python3 -m pytest tests/test_main.py -q -k trading_cycle`

Expected: PASS.

### Task 2: Extract market-data collection helper

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Add helper**

Create a private async helper in `src/main.py` that returns the values `trading_cycle()` currently derives during the fetch/enrichment stage:
- raw broker payloads needed later (`balance_data`, `balance_info`, `price_output`)
- computed pricing/account fields (`current_price`, `price_change_pct`, `foreigner_net`, `total_eval`, `total_cash`, `purchase_total`, `pnl_pct`)
- scanner lookup results (`market_candidates`, `candidate`)
- enriched `market_data`

Keep context-store writes and DB `system_metrics` write in this helper if they naturally belong to the collection phase.

**Step 2: Run targeted tests**

Run: `python3 -m pytest tests/test_main.py -q -k "trading_cycle and (context or min_price or limit_price)"`

Expected: PASS.

### Task 3: Extract scenario-evaluation helper

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Add helper**

Create a private async helper that owns:
- `scenario_engine.evaluate(...)`
- BUY suppression rules
- HOLD staged-exit overrides
- scenario notification
- decision log payload construction and `decision_logger.log_decision(...)`

Return the finalized `decision`, `match`, `decision_id`, and any data needed by later execution/logging.

**Step 2: Run targeted tests**

Run: `python3 -m pytest tests/test_main.py -q -k "trading_cycle and (cooldown or staged_exit or session_boundary or l7_context)"`

Expected: PASS.

### Task 4: Extract order-execution and trade-logging helpers

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Add execution helper**

Create a private async helper for actionable decisions only. It should own:
- kill-switch short-circuit
- quantity calculation
- FX guard
- insufficient-balance cooldown
- risk validation / kill-switch escalation
- domestic/overseas order submission
- success/failure side effects
- SELL outcome bookkeeping

Return a structured result containing all values needed for final trade logging.

**Step 2: Add final logging helper**

Create a small synchronous helper that converts the execution result into the existing `log_trade(...)` call, including selection context and FX/strategy PnL.

**Step 3: Run targeted tests**

Run: `python3 -m pytest tests/test_main.py -q -k "trading_cycle and (limit_price or fat_finger or ghost or cooldown or overnight)"`

Expected: PASS.

### Task 5: Flatten `trading_cycle()` into orchestration and verify

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Replace inline blocks with helper calls**

Reduce `trading_cycle()` to:
- start timing / session setup
- data collection call
- scenario evaluation call
- execution call
- final trade logging call
- latency monitoring

**Step 2: Run verification**

Run:
- `python3 -m pytest tests/test_main.py -q -k trading_cycle`
- `python3 -m pytest tests/test_main.py -q -k "session_boundary_reloads_us_min_price_override_in_trading_cycle or trading_cycle_sets_l7_context_keys"`
- `python3 -m ruff check src/main.py tests/test_main.py`

Expected: all PASS.

**Step 3: Commit**

```bash
git add src/main.py tests/test_main.py docs/plans/2026-03-08-issue-447-trading-cycle-subfunctions.md workflow/session-handover.md
git commit -m "refactor: extract trading_cycle sub-functions"
```
