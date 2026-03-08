# Issue 448 Run Daily Session Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Refactor `run_daily_session()` into smaller helpers without changing runtime behavior.

**Architecture:** Keep `run_daily_session()` as the public orchestration entrypoint and extract market-scoped helpers for scanner/playbook preparation, data collection, and per-stock execution. Preserve existing call order, logging, and side effects so current behavior and CI expectations remain unchanged.

**Tech Stack:** Python 3.11, asyncio, pytest, ruff

---

### Task 1: Lock helper extraction behavior with tests

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Add a test for a new helper that prepares scanner candidates for one market and asserts:
- overseas markets call `build_overseas_symbol_universe()`
- domestic markets do not
- scanner fallback stocks flow into `smart_scanner.scan()`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k daily_session_market_candidates -v`
Expected: FAIL because the helper does not exist yet.

**Step 3: Write minimal implementation**

Add the helper to `src/main.py` and wire `run_daily_session()` to use it.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k daily_session_market_candidates -v`
Expected: PASS

### Task 2: Extract market preparation helpers

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Extend the helper-focused tests if needed to cover playbook generation fallback and market-level skips.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k run_daily_session -v`

**Step 3: Write minimal implementation**

Extract helper functions for:
- pending order preparation
- scanner/watchlist loading
- playbook loading/generation
- stock/balance snapshot assembly

Keep return values explicit instead of introducing shared mutable state.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k run_daily_session -v`

### Task 3: Extract per-stock execution helper and verify CI-critical paths

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Reuse existing daily session tests and add coverage only if extraction reveals an unguarded path.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "run_daily_session or daily_session" -v`

**Step 3: Write minimal implementation**

Extract the per-stock decision/log/order path into a helper while preserving:
- session id propagation
- staged exit overrides
- cooldown and risk guards
- trade logging behavior

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "run_daily_session or daily_session" -v`

### Task 4: Full verification and PR preparation

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Run focused verification**

Run:
- `pytest tests/test_main.py -k "run_daily_session or daily_session" -v`
- `ruff check src/main.py tests/test_main.py`

**Step 2: Run CI-equivalent verification**

Run:
- `python3 scripts/session_handover_check.py --strict --ci`
- `python3 scripts/validate_governance_assets.py`
- `python3 scripts/validate_ouroboros_docs.py`
- `python3 scripts/validate_docs_sync.py`
- `pytest -v --cov=src --cov-report=term-missing --cov-fail-under=80`

**Step 3: Prepare integration**

Commit only refactor/test changes for issue `#448`, push branch, and open a PR that references `#445` and notes that any newly discovered behavioral bug was split into a separate issue.
