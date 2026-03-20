# Recent SELL Fee Buffer Implementation Plan

**Goal:** Record why `OOR-829` keeps the recent-SELL guard strict and add regression coverage for the no-buffer boundary.

**Design source of truth:** See [`2026-03-20-issue-829-recent-sell-fee-buffer-design.md`](./2026-03-20-issue-829-recent-sell-fee-buffer-design.md) for the fee-buffer alternatives, recommendation, and final decision. This plan only tracks the execution steps required to apply that decision.

**Architecture:** Do not change the runtime threshold model. Keep the existing helper behavior, add one boundary test for equal-price re-entry, and update docs so reviewers can see that the strict guard is intentional.

**Tech Stack:** Python, pytest, markdown docs

---

### Task 1: Lock the no-buffer boundary in tests

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Add the boundary regression**

Add a helper-level test showing that a BUY at exactly the latest SELL price is still allowed inside the guard window.

**Step 2: Run the focused test**

Run: `pytest tests/test_main.py -k "recent_sell_guard" -v`
Expected: PASS, including the new equal-price boundary.

### Task 2: Document the decision

**Files:**
- Modify: `docs/architecture.md`
- Modify: `src/core/order_helpers.py`
- Create: `docs/plans/2026-03-20-issue-829-recent-sell-fee-buffer-design.md`

**Step 1: Update runtime-facing docs**

State that `SELL_REENTRY_PRICE_GUARD_SECONDS` keeps a strict `current_price > last_sell_price` comparison and intentionally excludes fee/slippage buffering.

**Step 2: Update helper intent**

Tighten the helper docstring/comment so the code itself reflects the strict comparison contract.

**Step 3: Record the design rationale**

Keep the rationale in the design doc and reference it from review/PR discussion instead of duplicating the same decision text here.

### Task 3: Re-verify touched surfaces

**Files:**
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`
- Modify: `src/core/order_helpers.py`

**Step 1: Run focused regression**

Run: `pytest tests/test_main.py -k "suppresses_buy_above_recent_sell_price or recent_sell_guard" -v`
Expected: PASS.

**Step 2: Run touched-surface lint/docs checks**

Run: `ruff check src/core/order_helpers.py tests/test_main.py docs/architecture.md docs/plans/2026-03-20-issue-829-recent-sell-fee-buffer-design.md docs/plans/2026-03-20-issue-829-recent-sell-fee-buffer.md`
Expected: PASS.

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS.
