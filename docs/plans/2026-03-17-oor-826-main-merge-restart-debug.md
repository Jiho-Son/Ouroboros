# OOR-826 Main Merge Restart Debug Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the canonical restart hook survive non-root worktree execution context and persist enough diagnostics to explain why post-merge restart did or did not happen.

**Architecture:** Tighten the `WORKFLOW.md` `before_remove` hook so it resolves the repo root before invoking the restart script, then extend `scripts/symphony_before_remove_canonical_restart.sh` to persist invocation/decision logs before merge detection exits. Cover both gaps with targeted regression tests in `tests/test_runtime_overnight_scripts.py` and update operator docs for the new debug surface.

**Tech Stack:** Bash hook scripts, pytest subprocess integration tests, YAML front matter in `WORKFLOW.md`, repo operator docs.

---

### Task 1: Lock down the failing behavior in tests

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Read: `WORKFLOW.md`
- Read: `scripts/symphony_before_remove_canonical_restart.sh`

**Step 1: Write the failing test for hook path resolution**

Add a test that reads the real `WORKFLOW.md` `hooks.before_remove` command, executes it from a nested directory inside the fake worktree, and expects the canonical restart script to run successfully.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime_overnight_scripts.py -k 'workflow_before_remove' -v`
Expected: FAIL because `bash scripts/symphony_before_remove_canonical_restart.sh` cannot be found outside the repo root.

**Step 3: Write the failing test for debug log persistence**

Add a second test that drives the unmerged-worktree path and expects `canonical_restart.log` to contain invocation context plus the skip reason.

**Step 4: Run test to verify it fails**

Run: `pytest tests/test_runtime_overnight_scripts.py -k 'workflow_before_remove or invocation_log' -v`
Expected: FAIL because the script currently exits before writing any persistent log for that path.

### Task 2: Implement the minimal hook/script fix

**Files:**
- Modify: `WORKFLOW.md`
- Modify: `scripts/symphony_before_remove_canonical_restart.sh`

**Step 1: Make the hook repo-root safe**

Change the `before_remove` command to resolve the repo top-level with `git rev-parse --show-toplevel` and invoke the script via that absolute path.

**Step 2: Add early diagnostic logging**

Create the canonical restart log as soon as the canonical root is known, write an invocation line with `cwd`, `workspace_branch`, and `workspace_sha`, and persist skip/error decisions before early exits.

**Step 3: Run targeted tests to verify green**

Run: `pytest tests/test_runtime_overnight_scripts.py -k 'workflow_before_remove or invocation_log or before_remove_canonical_restart' -v`
Expected: PASS.

### Task 3: Sync docs and final validation

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/live-trading-checklist.md`
- Test: `tests/test_runtime_overnight_scripts.py`

**Step 1: Update operator docs**

Document the repo-root-safe hook invocation and clarify that `canonical_restart.log` now records invocation/skip/failure decisions for debugging.

**Step 2: Run scope checks**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS.

**Step 3: Run final targeted regression**

Run: `pytest tests/test_runtime_overnight_scripts.py -k 'workflow_before_remove or invocation_log or before_remove_canonical_restart' -v`
Expected: PASS.
