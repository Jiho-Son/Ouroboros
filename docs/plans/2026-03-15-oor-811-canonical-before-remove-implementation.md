# OOR-811 Canonical Before-Remove Restart Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automate exactly-once canonical `main` runtime restart on merge-driven worktree removal using Symphony `before_remove` hook while preserving branch-scoped runtime boundaries.

**Architecture:** Wire `WORKFLOW.md` to a repo-owned `before_remove` hook script that inspects merge status and restarts only canonical `main`. Use lock + marker safeguards and deterministic logging. Back behavior with shell-script integration tests using fake `git`/`gh` shims and update operator/governance docs.

**Tech Stack:** Bash hook script, Python/pytest script-integration tests, Markdown docs/governance validators.

---

### Task 1: Add failing tests for canonical restart hook behavior

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`

**Step 1: Write failing tests**
- Add constants and helpers for `scripts/symphony_before_remove_canonical_restart.sh`.
- Add failing tests for:
  - unmerged skip,
  - squash-merge fallback,
  - main-worktree skip,
  - dedupe restart-once behavior,
  - dry-run no mutation,
  - non-`flock` lock fallback.

**Step 2: Run tests to verify RED**
- Run: `pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v`
- Expected: FAIL because hook script/workflow wiring do not exist yet.

**Step 3: Commit checkpoint (tests-only, optional if red state kept local)**
- Keep failing tests staged or committed as an explicit RED checkpoint if needed.

### Task 2: Implement hook script and workflow wiring

**Files:**
- Create: `scripts/symphony_before_remove_canonical_restart.sh`
- Modify: `WORKFLOW.md`

**Step 1: Implement minimal GREEN behavior**
- Add script with:
  - canonical worktree discovery,
  - merge detection (git + GitHub fallback),
  - canonical-only restart,
  - marker dedupe,
  - dry-run outputs.
- Wire `hooks.before_remove` to execute the script.

**Step 2: Implement rework hardening requirements**
- Add `flock` fallback lock (`mkdir` lockdir + trap + warning log).
- Replace any `bash -lc` invocation with deterministic `bash -c`.
- Wrap `pull --ff-only` to log diagnostics before exit.
- Use atomic marker write (`mktemp` + `mv`).
- Silence/noise-control fetch output.
- Document squash-fallback SHA limitation in inline comment/docs.

**Step 3: Run targeted tests to verify GREEN**
- Run: `pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v`
- Expected: PASS for new hook subset.

### Task 3: Update docs and governance mappings

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/live-trading-checklist.md`
- Modify: `docs/ouroboros/01_requirements_registry.md`
- Modify: `docs/ouroboros/30_code_level_work_orders.md`
- Modify: `docs/ouroboros/40_acceptance_and_test_plan.md`

**Step 1: Document operational flow**
- Describe `before_remove` automation trigger and canonical-only restart semantics.
- Add dry-run / verification guidance.

**Step 2: Update traceability rows**
- Keep REQ/TASK/TEST mapping for OOR-811 behavior and validation coverage.

### Task 4: Run full validation and publish

**Files:**
- Modify: `workflow/session-handover.md` (session entry only)

**Step 1: Validation commands**
- Run:
  - `pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v`
  - `pytest -v --cov=src --cov-report=term-missing`
  - `ruff check src/ tests/`
  - `python3 scripts/validate_docs_sync.py`
  - `python3 scripts/validate_ouroboros_docs.py`
  - `python3 scripts/validate_governance_assets.py docs/ouroboros/01_requirements_registry.md docs/ouroboros/30_code_level_work_orders.md docs/ouroboros/40_acceptance_and_test_plan.md WORKFLOW.md docs/commands.md docs/live-trading-checklist.md`
  - `git diff --check`

**Step 2: Commit and push**
- Commit with clear summary/rationale/tests.
- Push branch and open PR linked to OOR-811.

**Step 3: Final metadata sync**
- Ensure Linear workpad checklists match executed evidence.
- Ensure issue has attached PR and PR has `symphony` label.
- Run PR feedback sweep before any state transition.
