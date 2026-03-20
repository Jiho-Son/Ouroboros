# Recent SELL Guard Market Setting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the direct `session_risk` dependency from the recent-SELL guard while keeping the setting interpretation in one shared place.

**Architecture:** Keep a small dedicated helper for resolving `SELL_REENTRY_PRICE_GUARD_SECONDS`, then pass the resolved integer into `_should_block_buy_above_recent_sell()` so the guard remains pure. Update the two BUY call paths in `src/main.py`, add focused regression coverage, and leave a short doc note explaining why the lazy import was removed for this path.

**Tech Stack:** Python, pytest, markdown docs

---

### Task 1: Lock the new boundary in tests

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

Add focused tests that express the new API boundary:
- `_should_block_buy_above_recent_sell()` takes `window_seconds` directly,
- the shared recent-SELL setting helper resolves session overrides and clamps the minimum window.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "recent_sell_guard or recent_sell_guard_window" -v`
Expected: FAIL because the new helper/signature does not exist yet.

**Step 3: Write minimal implementation**

Implement only the helper/signature changes needed to satisfy the new tests.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "recent_sell_guard or recent_sell_guard_window" -v`
Expected: PASS.

### Task 2: Move the setting responsibility to one helper

**Files:**
- Modify: `src/core/order_helpers.py`
- Modify: `src/main.py`

**Step 1: Add the shared resolver**

Create a dedicated helper that resolves `SELL_REENTRY_PRICE_GUARD_SECONDS` once and normalizes it with `max(1, int(...))`.

**Step 2: Purify the guard**

Change `_should_block_buy_above_recent_sell()` so it receives `window_seconds: int` and only compares explicit inputs.

**Step 3: Update both BUY paths**

Use the shared resolver in both recent-SELL guard call sites in `src/main.py` and pass the resolved integer into the pure guard helper.

### Task 3: Document the dependency decision

**Files:**
- Modify: `docs/architecture.md`
- Modify: `src/core/order_helpers.py`
- Create: `docs/plans/2026-03-20-issue-830-recent-sell-market-setting-design.md`

**Step 1: Update runtime-facing docs**

State that the recent-SELL guard now receives a resolved window from a shared helper, while session-aware setting lookup remains centralized outside the pure comparison function.

**Step 2: Record why the lazy import changed**

Add a short code comment/docstring so reviewers can see why this path no longer uses the in-function import.

**Step 3: Keep the design rationale**

Use the design doc as the source of truth for the injection-vs-helper comparison.

### Task 4: Re-verify the touched surfaces

**Files:**
- Modify: `src/core/order_helpers.py`
- Modify: `src/main.py`
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`

**Step 1: Run focused regression**

Run: `pytest tests/test_main.py -k "recent_sell_guard or suppresses_buy_above_recent_sell_price or recent_sell_guard_window" -v`
Expected: PASS.

**Step 2: Run touched-surface lint/docs checks**

Run: `ruff check src/core/order_helpers.py src/main.py tests/test_main.py docs/architecture.md docs/plans/2026-03-20-issue-830-recent-sell-market-setting-design.md docs/plans/2026-03-20-issue-830-recent-sell-market-setting.md`
Expected: PASS.

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS.
