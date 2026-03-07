# Issue 436 Mid-Session Refresh Rollback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a regression test that proves mid-session playbook refresh restores the pre-refresh playbook when regeneration fails.

**Architecture:** Reuse the existing `tests/test_main.py` loop-style test patterns and drive only the mid-session refresh failure path. The implementation should avoid broad refactors and prefer observable-state assertions over new production hooks.

**Tech Stack:** Python, pytest, asyncio, unittest.mock

---

### Task 1: Add Red Test For Refresh Rollback

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py` only if the test cannot observe the existing rollback path without a tiny seam

**Step 1: Write the failing test**

Add a test that:
- seeds an in-memory existing playbook for a market
- forces `_should_mid_session_refresh()` conditions
- makes `pre_market_planner.generate_playbook()` raise
- verifies the original playbook remains the active one after the failure path

**Step 2: Run test to verify it fails**

Run:
- `pytest -q tests/test_main.py -k mid_session_refresh`

Expected: FAIL because the rollback path is not yet covered by an assertion that the test can satisfy.

**Step 3: Write minimal implementation**

Implement only the smallest change needed to make the test observe the existing rollback behavior.

**Step 4: Run test to verify it passes**

Run:
- `pytest -q tests/test_main.py -k 'mid_session_refresh or should_mid_session_refresh'`

Expected: PASS.

**Step 5: Commit**

```bash
git add docs/plans/archive/2026-03-07-issue-436-mid-session-refresh-rollback-design.md docs/plans/archive/2026-03-07-issue-436-mid-session-refresh-rollback.md tests/test_main.py src/main.py
git commit -m "test: cover mid-session refresh rollback"
```
