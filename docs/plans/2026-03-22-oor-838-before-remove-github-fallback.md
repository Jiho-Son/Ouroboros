# OOR-838 Before Remove Nested GitHub Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a regression test that explicitly proves the workflow `before_remove` hook still restarts the canonical main worktree from a nested directory when git ancestry does not confirm the merge but the GitHub fallback does.

**Architecture:** Keep production behavior unchanged and limit edits to `tests/test_runtime_overnight_scripts.py` plus work tracking artifacts. Reproduce the current coverage split left by OOR-828, then add one focused nested-directory workflow test whose inputs clearly show `merged_by_git=False` and `github_merged=True` are the deciding signal.

**Tech Stack:** pytest subprocess integration tests, fake git/GitHub CLI shims, Linear workpad-driven unattended workflow.

---

### Task 1: Capture the current coverage gap

**Files:**
- Read: `tests/test_runtime_overnight_scripts.py`
- Read: `scripts/symphony_before_remove_canonical_restart.sh`

**Step 1: Inspect the existing split tests**

Locate the nested workflow test and the standalone GitHub fallback test. Confirm the suite currently proves each signal separately but not their combined nested-directory path.

**Step 2: Run the closest existing tests as a baseline**

Run: `pytest tests/test_runtime_overnight_scripts.py::test_workflow_before_remove_hook_uses_git_ancestry_signal_from_nested_dir tests/test_runtime_overnight_scripts.py::test_before_remove_canonical_restart_uses_github_merge_signal_for_squash_merges -v`
Expected: PASS, while still leaving the nested-directory GitHub fallback combination uncovered.

### Task 2: Add the nested-directory GitHub fallback regression test

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`

**Step 1: Write the failing test first**

Add a new workflow-hook test that uses `invocation_mode="workflow"` and `cwd_relative="nested/context"` with `merged_by_git=False` and `github_merged=True`. Make the test name and inline comment explicitly state that it is proving the GitHub fallback path from a nested directory.

**Step 2: Run the focused test to verify green**

Run: `pytest tests/test_runtime_overnight_scripts.py -k "workflow_before_remove_hook and github_fallback" -v`
Expected: PASS with stop/start hook execution and the marker file containing the target SHA.

### Task 3: Validate the touched surface and update tracking

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Modify: `workflow/session-handover.md`

**Step 1: Run scope validation**

Run: `pytest tests/test_runtime_overnight_scripts.py -k "github_merge_signal or workflow_before_remove_hook_uses_github_fallback_signal_from_nested_dir" -v`
Expected: PASS.

**Step 2: Run lint for the touched test file**

Run: `ruff check tests/test_runtime_overnight_scripts.py`
Expected: PASS.

**Step 3: Update the Linear workpad with evidence**

Record the handover gate result, pull evidence, reproduction notes, and final validation commands before commit/push/PR work.
