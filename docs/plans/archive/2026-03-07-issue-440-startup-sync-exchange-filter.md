# Issue 440 Startup Sync Exchange Filter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure startup sync applies the same overseas exchange filter as the other `#440` code paths.

**Architecture:** Add one regression test around `sync_positions_from_broker()` using a mixed-exchange overseas balance payload, then pass `market.exchange_code` to `_extract_held_codes_from_balance()` in the startup sync path.

**Tech Stack:** Python, pytest, existing broker balance helpers in `src/main.py`

---

### Task 1: Reproduce the startup sync gap

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

Add a startup sync test that feeds a `US_NASDAQ` balance containing both `NASD` and `NYSE` holdings and asserts only the `NASD` symbol is recorded under `US_NASDAQ`.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_main.py -k startup_sync_filters_overseas_holdings_by_exchange_code`

Expected: FAIL because startup sync currently records both symbols.

### Task 2: Apply the production fix

**Files:**
- Modify: `src/main.py`

**Step 1: Write minimal implementation**

Pass `exchange_code=None if market.is_domestic else market.exchange_code` into the startup sync `_extract_held_codes_from_balance()` call.

**Step 2: Run test to verify it passes**

Run: `pytest -q tests/test_main.py -k startup_sync_filters_overseas_holdings_by_exchange_code`

Expected: PASS

### Task 3: Verify no local regression

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Run focused verification**

Run: `ruff check src/main.py tests/test_main.py`

Run: `pytest -q tests/test_main.py -k 'startup_sync_filters_overseas_holdings_by_exchange_code or syncs_overseas_position_not_in_db or overseas_filters_holdings_by_exchange_code_when_present'`

Expected: all pass

**Step 2: Commit**

```bash
git add src/main.py tests/test_main.py docs/plans/archive/2026-03-07-issue-440-startup-sync-exchange-filter-design.md docs/plans/archive/2026-03-07-issue-440-startup-sync-exchange-filter.md
git commit -m "fix: filter startup sync holdings by exchange"
```
