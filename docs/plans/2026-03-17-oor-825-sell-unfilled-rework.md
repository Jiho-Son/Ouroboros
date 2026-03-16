# OOR-825 Repeated SELL Unfilled Rework Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop repeated unfilled SELL loops by propagating exhausted pending-order state into runtime SELL execution and escalating the next exit order instead of silently re-looping.

**Architecture:** Keep pending-order reconciliation in `src/broker/pending_orders.py`, but pass retry-exhaustion state into `src/main.py` so `trading_cycle()` can execute the final exit with the normal risk/logging/persistence flow. Cover the behavior with RED-first runtime tests and keep state cleanup explicit.

**Tech Stack:** Python async runtime, pytest, unittest.mock, Markdown workpad/docs.

---

### Task 1: Lock reproduction with failing runtime tests

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing tests**
- Add one domestic test for exhausted pending SELL state causing a terminal-exit order.
- Add one overseas test for exhausted pending SELL state causing a terminal-exit order.
- Add one stale-state cleanup test for a fresh BUY lifecycle.

**Step 2: Verify RED**
- Run: `pytest tests/test_main.py -k 'pending_retry_budget_is_exhausted or stale_sell_retry_budget' -v`
- Expected: FAIL on current `main` because `trading_cycle()` does not yet accept or use `sell_resubmit_counts`.

### Task 2: Implement the runtime fix

**Files:**
- Modify: `src/main.py`

**Step 1: Thread retry state**
- Add a helper for the pending SELL key.
- Thread `sell_resubmit_counts` through `trading_cycle()`, `_execute_trading_cycle_action()`, and the runtime call site.

**Step 2: Escalate exhausted SELL exits**
- Detect exhausted pending SELL state before the normal SELL price selection.
- Use market order pricing when policy allows, otherwise use the strongest policy-compliant fallback.
- Clear stale retry state after successful BUY/SELL execution.

**Step 3: Verify GREEN**
- Run: `pytest tests/test_main.py -k 'pending_retry_budget_is_exhausted or stale_sell_retry_budget' -v`

### Task 3: Run focused regressions

**Files:**
- Modify: `tests/test_main.py` if any nearby expectation must be updated

**Step 1: Pending-order/runtime regression sweep**
- Run: `pytest tests/test_main.py -k 'pending_retry_budget_is_exhausted or stale_sell_retry_budget or sell_pending_is_cancelled_then_resubmitted or sell_already_resubmitted_is_only_cancelled or ghost_position' -v`
- Confirm no regressions in nearby SELL recovery paths.

### Task 4: Finish validation and publish

**Files:**
- Modify: `workflow/session-handover.md`

**Step 1: Run validation**
- `pytest tests/test_main.py -k 'pending_retry_budget_is_exhausted or stale_sell_retry_budget or sell_pending_is_cancelled_then_resubmitted or sell_already_resubmitted_is_only_cancelled or ghost_position' -v`
- `ruff check src/ tests/`
- `pytest -v --cov=src --cov-report=term-missing`

**Step 2: Publish**
- Commit the change set.
- Push `feature/issue-825-sell-unfilled-loop-r2`.
- Create a new PR against `main`, attach it to Linear, and ensure label `symphony`.

**Step 3: Review sweep**
- Check PR top-level comments, inline comments, and review summaries until zero actionable feedback remain.
