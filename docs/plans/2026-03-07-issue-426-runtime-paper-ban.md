# Issue 426 Runtime Paper Ban Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reject `paper` mode at runtime entrypoints while leaving non-runtime docs and historical data semantics untouched.

**Architecture:** Add a small runtime validation helper in `src/main.py` and call it from both `main()` and `run(settings)` so CLI invocation and direct async entry both reject `paper`. Keep the scope limited to runtime behavior only; do not rewrite DB defaults, broker internals, or broad documentation in this task.

**Tech Stack:** Python, pytest, argparse, asyncio

---

### Task 1: Add Red Tests For Runtime Rejection

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Add:
- a test that `main()` raises on `--mode=paper` and does not call `asyncio.run`
- a test that `run(settings)` raises on `Settings(MODE="paper")` before initializing brokers

**Step 2: Run test to verify it fails**

Run:
- `pytest -q tests/test_main.py -k 'main_rejects_paper_mode or run_rejects_paper_mode'`

Expected: FAIL because runtime currently still allows `paper`.

**Step 3: Write minimal implementation**

Add a helper in `src/main.py` that raises `ValueError` when runtime mode is `paper`, call it from `main()` right after parsing args and from `run(settings)` before runtime initialization.

**Step 4: Run test to verify it passes**

Run:
- `pytest -q tests/test_main.py -k 'main_rejects_paper_mode or run_rejects_paper_mode'`

Expected: PASS.

**Step 5: Commit**

```bash
git add docs/plans/2026-03-07-issue-426-runtime-paper-ban.md src/main.py tests/test_main.py
git commit -m "feat: ban runtime paper mode"
```
