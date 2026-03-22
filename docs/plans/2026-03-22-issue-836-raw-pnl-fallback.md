# OOR-836 Raw PnL Unit Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop `PreMarketPlanner` from reusing unsupported market codes as raw PnL units in prompts.

**Architecture:** Keep the existing explicit market-to-unit mapping, but change the unsupported branch to emit a prompt-safe fallback label and a warning signal. Lock the contract with helper and prompt regression tests, then document the fallback in architecture notes.

**Tech Stack:** Python, pytest, ruff, Markdown docs

---

### Task 1: Reproduce and pin the unsupported fallback behavior

**Files:**
- Modify: `workflow/session-handover.md`
- Test: `tests/test_pre_market_planner.py`

**Step 1: Capture the current unsupported behavior**

Run: `python3 - <<'PY'\nfrom src.strategy.pre_market_planner import _raw_pnl_unit_for_market\nprint(_raw_pnl_unit_for_market('JP'))\nPY`

Expected: `JP`

**Step 2: Record the signal in the Linear workpad**

Expected: workpad `Notes` contains the exact command/result and the branch sync evidence.

### Task 2: Add the failing regression tests first

**Files:**
- Modify: `tests/test_pre_market_planner.py`

**Step 1: Add a helper-level failing test**

Assert `_raw_pnl_unit_for_market("JP") == "UNKNOWN_CURRENCY"`.

**Step 2: Add a prompt-level failing test**

Build a prompt for market `JP` with a self-market scorecard and assert:

- `Realized PnL (UNKNOWN_CURRENCY, raw): ...` is present
- `Realized PnL (JP, raw): ...` is absent

**Step 3: Run the targeted red test**

Run: `pytest -q tests/test_pre_market_planner.py -k "unsupported_market"`

Expected: FAIL because the implementation still returns `JP`.

### Task 3: Implement the minimal fallback change

**Files:**
- Modify: `src/strategy/pre_market_planner.py`

**Step 1: Update `_raw_pnl_unit_for_market()`**

- Keep the existing mapping for supported markets.
- For unsupported markets, log a warning and return `UNKNOWN_CURRENCY`.

**Step 2: Keep prompt call sites unchanged unless required**

The existing prompt sections should inherit the new fallback via the helper.

**Step 3: Re-run the targeted tests**

Run: `pytest -q tests/test_pre_market_planner.py -k "unsupported_market"`

Expected: PASS

### Task 4: Document the contract

**Files:**
- Modify: `docs/architecture.md`

**Step 1: Add a short architecture note**

Document that planner scorecard prompt rendering uses explicit market-to-unit
mapping and falls back to `UNKNOWN_CURRENCY` for unsupported markets until the
mapping is expanded.

### Task 5: Run verification and prepare review artifacts

**Files:**
- Modify: `src/strategy/pre_market_planner.py`
- Modify: `tests/test_pre_market_planner.py`
- Modify: `docs/architecture.md`
- Modify: `docs/plans/2026-03-22-issue-836-raw-pnl-fallback-design.md`
- Modify: `docs/plans/2026-03-22-issue-836-raw-pnl-fallback.md`
- Modify: `workflow/session-handover.md`

**Step 1: Run targeted planner tests**

Run: `pytest -q tests/test_pre_market_planner.py`

Expected: PASS

**Step 2: Run scoped static checks**

Run: `ruff check src/strategy/pre_market_planner.py tests/test_pre_market_planner.py docs/architecture.md docs/plans/2026-03-22-issue-836-raw-pnl-fallback-design.md docs/plans/2026-03-22-issue-836-raw-pnl-fallback.md`

Expected: PASS

**Step 3: Run docs sync validation**

Run: `python3 scripts/validate_docs_sync.py`

Expected: PASS

**Step 4: Run repo verification gate**

Run: `pytest -v --cov=src --cov-report=term-missing`

Expected: PASS

**Step 5: Commit and publish**

Run:

```bash
git add workflow/session-handover.md \
  src/strategy/pre_market_planner.py \
  tests/test_pre_market_planner.py \
  docs/architecture.md \
  docs/plans/2026-03-22-issue-836-raw-pnl-fallback-design.md \
  docs/plans/2026-03-22-issue-836-raw-pnl-fallback.md
git commit -m "fix(planner): make raw pnl unit fallback explicit"
```

Expected: clean commit ready for PR creation and Linear linkage
