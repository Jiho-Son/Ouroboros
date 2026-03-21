# Sell Trade Exchange Priority Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure `get_latest_sell_trade()` uses the freshest eligible SELL for recent-sell guard calculations, and lock that behavior with regression tests.

**Architecture:** Reproduce the current timestamp inversion first, then update the SELL helper so `timestamp DESC` leads the ordering while `exchange_code` only breaks ties among eligible rows. Add one DB-level regression and one recent-sell guard regression that exercises the real DB helper from `src/main.py`.

**Tech Stack:** Python, sqlite3 helpers, pytest, existing recent-sell guard path in `src/main.py`

---

### Task 1: Reproduce the current SELL selection bug in tests

**Files:**
- Modify: `tests/test_db.py`
- Modify: `tests/test_main.py`

**Step 1: Write the failing DB helper regression**

Add a test in `tests/test_db.py` that inserts:
- an older SELL with matching `exchange_code`,
- a newer SELL with blank legacy `exchange_code`,
- then asserts `get_latest_sell_trade(..., exchange_code="NASD")` returns the newer SELL.

**Step 2: Run the DB regression to verify it fails**

Run: `pytest tests/test_db.py -k "latest_sell_trade and timestamp" -v`
Expected: FAIL because the current SQL returns the older exchange-matched SELL.

**Step 3: Write the failing recent-sell guard regression**

Add a test in `tests/test_main.py` that:
- uses a real in-memory DB,
- seeds an older matching SELL and a newer blank legacy SELL,
- sets the newer SELL inside the guard window and the older SELL outside it,
- calls `_apply_recent_sell_guard()` and expects `HOLD`.

**Step 4: Run the guard regression to verify it fails**

Run: `pytest tests/test_main.py -k "recent_sell_guard_uses_latest_sell_timestamp" -v`
Expected: FAIL because the current helper reads the older SELL and lets the BUY pass through.

### Task 2: Make SELL selection semantics explicit in `src/db.py`

**Files:**
- Modify: `src/db.py`

**Step 1: Update the SELL ordering**

Change `get_latest_sell_trade()` so the ORDER BY clause sorts by `timestamp DESC` first and uses the `exchange_code` match only as a tie-breaker among already eligible rows.

**Step 2: Document the policy inline**

Add a short SQL or function comment that explains why the freshest SELL must win for guard-window calculations even when legacy blank rows are in the candidate set.

**Step 3: Run the focused regressions**

Run: `pytest tests/test_db.py -k "latest_sell_trade" -v`
Expected: PASS.

Run: `pytest tests/test_main.py -k "recent_sell_guard" -v`
Expected: PASS for the touched guard tests, including the new regression.

### Task 3: Verify touched surface and keep notes aligned

**Files:**
- Modify: `tests/test_db.py`
- Modify: `tests/test_main.py`
- Modify: `workflow/session-handover.md`

**Step 1: Run touched-surface lint**

Run: `ruff check src/db.py tests/test_db.py tests/test_main.py`
Expected: PASS.

**Step 2: Refresh the Linear workpad**

Record:
- reproduction command/output,
- pull skill evidence,
- validation commands/results,
- final SQL policy summary.

**Step 3: Prepare commit**

Run:
```bash
git add src/db.py tests/test_db.py tests/test_main.py workflow/session-handover.md docs/plans/2026-03-21-issue-832-sell-trade-exchange-priority.md
git commit -m "fix: use latest sell timestamp for guard lookups"
```
Expected: clean commit with tests and policy note together.
