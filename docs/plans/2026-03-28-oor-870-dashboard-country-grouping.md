# Dashboard Country Grouping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the dashboard overview treat the three U.S. exchange markets as a single `US` country bucket while preserving existing `KR` behavior.

**Architecture:** Add dashboard-local market grouping helpers in `src/dashboard/app.py`, then route overview-facing endpoints (`/api/status`, `/api/decisions`, `/api/pnl/history`, `/api/positions`) through the grouped key so the overview card, filters, chart, and positions view stay consistent.

**Tech Stack:** FastAPI, SQLite, pytest, static HTML/JS dashboard

---

### Task 1: Lock the grouped overview contract with failing tests

**Files:**
- Modify: `tests/test_dashboard.py`
- Test: `tests/test_dashboard.py`

**Step 1: Write the failing test**

- Add a regression test that seeds `US_NASDAQ`, `US_NYSE`, and `US_AMEX`
  activity on the same day and expects `/api/status` to expose one `US` entry
  with combined counts.
- Add focused assertions for grouped `US` filtering on `/api/decisions`,
  `/api/pnl/history`, and `/api/positions`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard.py -k "group_us_markets" -v`

Expected: FAIL because the dashboard still exposes raw exchange-level markets.

### Task 2: Implement dashboard-local market grouping

**Files:**
- Modify: `src/dashboard/app.py`
- Test: `tests/test_dashboard.py`

**Step 1: Write minimal implementation**

- Add a helper that maps overview-visible market codes to grouped country keys.
- Aggregate `/api/status` rows by grouped key.
- Expand grouped `US` filters into the concrete U.S. exchange market codes for
  `/api/decisions` and `/api/pnl/history`.
- Return grouped `market` values from `/api/positions`.

**Step 2: Run targeted tests to verify they pass**

Run: `pytest tests/test_dashboard.py -k "group_us_markets" -v`

Expected: PASS

### Task 3: Verify no dashboard regressions slipped in

**Files:**
- Modify: `src/dashboard/app.py`
- Modify: `tests/test_dashboard.py`

**Step 1: Run broader dashboard coverage**

Run: `pytest tests/test_dashboard.py -v`

Expected: PASS

**Step 2: Run lint on touched files**

Run: `ruff check src/dashboard/app.py tests/test_dashboard.py`

Expected: PASS
