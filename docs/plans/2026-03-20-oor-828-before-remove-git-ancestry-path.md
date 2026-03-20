# OOR-828 Before Remove Git Ancestry Path Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the workflow `before_remove` regression test explicitly prove the git ancestry merge-detection path without relying on the GitHub merge fallback signal.

**Architecture:** Keep production behavior unchanged and narrow the change surface to `tests/test_runtime_overnight_scripts.py`. Reproduce the current mixed-signal test, then rename and tighten its fixture inputs so the test shows `merged_by_git=True` with `github_merged=False` is sufficient for the workflow hook to resolve and run the restart script from a nested worktree directory.

**Tech Stack:** pytest subprocess integration tests, fake git/GitHub helper shims, Linear workpad-driven unattended workflow.

---

### Task 1: Capture the current signal

**Files:**
- Read: `tests/test_runtime_overnight_scripts.py`
- Read: `WORKFLOW.md`

**Step 1: Inspect the existing regression test**

Locate `test_workflow_before_remove_hook_resolves_script_from_nested_worktree_dir` and confirm which helper inputs make it pass today.

**Step 2: Run the focused test as a baseline**

Run: `pytest tests/test_runtime_overnight_scripts.py -k workflow_before_remove_hook_resolves_script_from_nested_worktree_dir -v`
Expected: PASS with the current mixed `merged_by_git=True` / `github_merged=True` fixture.

### Task 2: Separate the git ancestry path explicitly

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`

**Step 1: Tighten the test inputs**

Change the target test so `github_merged=False` while `merged_by_git=True` remains enabled, and rename or comment the test to state that it is proving the git ancestry path.

**Step 2: Run the focused test to verify green**

Run: `pytest tests/test_runtime_overnight_scripts.py -k workflow_before_remove_hook_resolves_script_from_nested_worktree_dir -v`
Expected: PASS, demonstrating the workflow hook does not need the GitHub merge fallback for this scenario.

### Task 3: Run scope validation and prepare review artifacts

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Read: `workflow/session-handover.md`

**Step 1: Run the focused overnight script suite**

Run: `pytest tests/test_runtime_overnight_scripts.py -v`
Expected: PASS.

**Step 2: Run lint for the touched test file**

Run: `ruff check tests/test_runtime_overnight_scripts.py`
Expected: PASS.

**Step 3: Update workpad with evidence**

Record the pull result, reproduction command, final validation commands, and the exact test-intent change in the Linear workpad before commit/push/PR work.
