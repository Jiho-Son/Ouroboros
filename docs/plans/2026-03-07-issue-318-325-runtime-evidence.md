# Issue 318/325 Runtime Evidence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist staged-exit runtime inputs and resolved thresholds so `#318` and `#325` can be proven from live artifacts.

**Architecture:** Keep the existing `decision_logs` table and enrich its JSON payloads in `src/main.py` at both decision logging sites. Add regression tests around staged-exit decisions to prove the new fields are recorded.

**Tech Stack:** Python, pytest, sqlite-backed decision logger, existing main-loop tests

---

### Task 1: Reproduce the evidence gap

**Files:**
- Modify: `tests/test_main.py`

**Step 1: Write the failing test**

Add a test that drives a HOLD decision through staged-exit override logic and then asserts the resulting `decision_logs.input_data` or `context_snapshot` includes `atr_value`, `pred_down_prob`, `stop_loss_threshold`, `be_arm_pct`, `arm_pct`, and staged-exit reason fields.

**Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_main.py -k staged_exit_decision_logs_runtime_evidence`

Expected: FAIL because current decision logs omit those fields.

### Task 2: Add runtime evidence fields

**Files:**
- Modify: `src/main.py`

**Step 1: Write minimal implementation**

Thread the staged-exit values already computed in `main.py` into both decision logging payloads.

**Step 2: Run test to verify it passes**

Run: `pytest -q tests/test_main.py -k staged_exit_decision_logs_runtime_evidence`

Expected: PASS

### Task 3: Verify focused regressions

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Run focused verification**

Run: `ruff check src/main.py tests/test_main.py`

Run: `pytest -q tests/test_main.py -k 'staged_exit_decision_logs_runtime_evidence or inject_staged_exit_features or apply_staged_exit'`

Expected: all pass

**Step 2: Commit**

```bash
git add src/main.py tests/test_main.py docs/plans/2026-03-07-issue-318-325-runtime-evidence-design.md docs/plans/2026-03-07-issue-318-325-runtime-evidence.md
git commit -m "feat: log staged exit runtime evidence"
```
