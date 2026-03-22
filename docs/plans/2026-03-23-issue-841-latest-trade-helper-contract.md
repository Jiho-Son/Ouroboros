# Latest Trade Helper Contract Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the BUY/SELL latest trade helper contract differences explicit without changing runtime behavior unless the investigation proves a real bug.

**Architecture:** Preserve the current SQL behavior, add regression tests that lock the decision-linked BUY vs timestamp-driven SELL semantics, and document the differing return shapes at the helper definitions.

**Tech Stack:** Python, sqlite3 helper queries in `src/db.py`, pytest regressions in `tests/test_db.py`

---

### Task 1: Lock the current asymmetry with focused regressions

**Files:**
- Modify: `tests/test_db.py`
- Reference: `src/db.py`

**Step 1: Add a BUY regression**

Write a test that inserts:
- an older BUY with `decision_id`,
- a newer BUY without `decision_id`,
- then asserts `get_latest_buy_trade(..., exchange_code=None)` returns the older linked BUY.

**Step 2: Add a SELL regression**

Write a test that inserts:
- an older SELL with `decision_id`,
- a newer SELL without `decision_id`,
- then asserts `get_latest_sell_trade(..., exchange_code=None)` returns the newer SELL.

**Step 3: Add contract-key assertions**

Extend the focused tests so they also assert:
- BUY helper exposes `selection_context`,
- SELL helper exposes `timestamp`.

**Step 4: Run the targeted tests**

Run: `pytest tests/test_db.py -k 'decision_id or latest_trade' -v`
Expected: PASS, confirming the tests capture the current helper contract.

### Task 2: Document the contract where the helpers are defined

**Files:**
- Modify: `src/db.py`

**Step 1: Clarify BUY helper intent**

Update the docstring/comment to explain that BUY helper intentionally ignores decision-less rows because restore/audit consumers require `decision_id` and `selection_context`.

**Step 2: Clarify SELL helper intent**

Update the docstring/comment to explain that SELL helper intentionally keeps decision-less rows because recent-sell guard only needs the freshest SELL `price`/`timestamp`.

**Step 3: Document return-shape asymmetry**

Make the differing returned keys visible in the helper docstrings.

### Task 3: Verify touched surface and refresh the workpad

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/db.py`
- Modify: `workflow/session-handover.md`

**Step 1: Run targeted verification**

Run: `pytest tests/test_db.py -k 'decision_id or latest_trade' -v`
Expected: PASS.

Run: `ruff check src/db.py tests/test_db.py`
Expected: PASS.

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS.

**Step 2: Refresh the Linear workpad**

Record:
- reproduction evidence,
- pull skill evidence,
- decision on SELL `decision_id` filtering,
- final validation results.
