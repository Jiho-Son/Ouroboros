# Open Issue Triage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Audit open issues against current repo state, fix remaining gaps, collect acceptable evidence, and update issue states consistently.

**Architecture:** Use a mixed evidence policy. Structural bugs and policy guards close from code plus regression tests; runtime-sensitive tickets remain open unless runtime evidence exists. Keep code changes minimal and add issue comments that cite concrete verification commands or runtime artifacts.

**Tech Stack:** Python, pytest, tea CLI, repository docs/workflow validators

---

### Task 1: Establish Triage Baseline

**Files:**
- Modify: `workflow/session-handover.md`
- Create: `docs/plans/archive/2026-03-06-open-issue-triage-design.md`
- Create: `docs/plans/archive/2026-03-06-open-issue-triage.md`

**Step 1: Record the same-day handover entry**

Update `workflow/session-handover.md` with branch `feature/issue-438-open-issue-triage` and the evidence policy note.

**Step 2: Run the handover gate**

Run: `python3 scripts/session_handover_check.py --strict`
Expected: PASS for the active branch and same-day UTC entry.

**Step 3: Save the design and plan docs**

Write the evidence policy and task sequence to the plan files above.

### Task 2: Audit Each Open Issue Against Repo State

**Files:**
- Read: `src/main.py`
- Read: `src/db.py`
- Read: `tests/test_main.py`
- Read: `tests/test_playbook_store.py`
- Read: `workflow/session-handover.md`
- Read: `docs/plans/archive/2026-03-07-issue-426-runtime-paper-ban.md`
- Read: `docs/plans/archive/2026-03-06-issue-428-nested-test-cleanup.md`
- Read: `docs/plans/archive/2026-03-07-issue-436-mid-session-refresh-rollback.md`

**Step 1: Inspect close candidates**

Confirm whether `#426`, `#428`, `#435`, and `#436` already have code and tests in place.

**Step 2: Inspect runtime-sensitive issues**

Confirm whether `#318`, `#325`, and `#429` have only test evidence or also runtime evidence.

**Step 3: Decide action per issue**

Produce a local checklist of `close now`, `fix first`, or `comment only`.

### Task 3: Fix Remaining Gaps with TDD

**Files:**
- Modify: exact production/test files only if audit finds a real gap

**Step 1: Write or confirm a failing test for each unresolved gap**

Run the narrow pytest target first and verify RED.

**Step 2: Write the minimal implementation**

Patch only the unresolved behavior.

**Step 3: Re-run the narrow tests and then the relevant regression set**

Expected: PASS with no new failures in touched paths.

### Task 4: Collect Verification Evidence

**Files:**
- No repository file changes required unless audit reveals missing documentation

**Step 1: Run fresh verification**

Run only the relevant commands for the issues being closed, such as:
- `python3 scripts/session_handover_check.py --strict`
- `pytest tests/test_main.py -k "paper mode or refresh or extract_avg_price" -v`
- `pytest tests/test_playbook_store.py -k "slot or UNIQUE" -v`

**Step 2: Capture runtime evidence status**

If runtime artifacts for `#318`, `#325`, `#429` are absent, record that explicitly and keep those issues open.

### Task 5: Comment and Update Issue States

**Files:**
- No repository file changes required

**Step 1: Post per-issue comments**

Use `scripts/tea_comment.sh` with file-based bodies to summarize code locations and verification evidence.

**Step 2: Close only eligible issues**

Close issues with acceptable evidence, and leave runtime-sensitive issues open when runtime evidence is missing.

**Step 3: Summarize final triage result**

Report which issues were closed, which remain open, what was fixed, and which verification commands were run.
