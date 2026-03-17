# OOR-811 Canonical Before-Remove Restart Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automate exactly-once canonical `main` runtime restart on merged worktree cleanup using Symphony `before_remove` while preserving branch-scoped runtime isolation.

**Architecture:** Add a repo-owned before_remove hook script that gates restart on merge-to-main detection, restarts only canonical `main`, and enforces lock/marker/log safeguards. Cover behavior with hook-focused pytest integration tests and synchronize operator/governance docs.

**Tech Stack:** Bash script automation, pytest integration tests with fake CLI shims, Markdown docs/governance validators.

---

### Task 1: Establish RED tests for hook behavior and rework hardening

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`

**Step 1: Add failing tests**
- Add helper shims for fake `git`/`gh` plus hook runner harness.
- Add tests for:
  - merged/unmerged path gating,
  - canonical-only restart,
  - dedupe marker behavior,
  - dry-run no mutation,
  - lock fallback timeout,
  - start failure critical logging,
  - bare-worktree parse safety.

**Step 2: Verify RED**
- Run: `pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v`
- Expected: fail while hook script/wiring is missing.

### Task 2: Implement hook and workflow integration

**Files:**
- Create: `scripts/symphony_before_remove_canonical_restart.sh`
- Modify: `WORKFLOW.md`

**Step 1: Implement core path**
- canonical root discovery,
- merge inclusion checks (`merge-base` + GitHub fallback),
- canonical-only stop/start,
- marker dedupe and dry-run output.

**Step 2: Apply rework hardening**
- `mkdir` lock fallback timeout,
- explicit CRITICAL log when start fails after successful stop,
- bare-entry-safe parser reset,
- atomic marker write,
- pull failure diagnostics,
- fetch noise suppression.

**Step 3: Verify GREEN**
- Run: `pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v`

### Task 3: Update docs and governance mapping

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/live-trading-checklist.md`
- Modify: `docs/ouroboros/01_requirements_registry.md`
- Modify: `docs/ouroboros/30_code_level_work_orders.md`
- Modify: `docs/ouroboros/40_acceptance_and_test_plan.md`

**Step 1: Operator docs**
- Document before_remove trigger, canonical-only restart, and dry-run/targeted verification command.

**Step 2: Governance traceability**
- Add/refresh REQ/TASK/TEST mapping rows for canonical restart automation invariants.

### Task 4: Validate and publish

**Files:**
- Modify: `workflow/session-handover.md` (session entry only)

**Step 1: Run validation set**
- `pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v`
- `pytest -v --cov=src --cov-report=term-missing`
- `ruff check src/ tests/`
- `python3 scripts/validate_docs_sync.py`
- `python3 scripts/validate_ouroboros_docs.py`
- `python3 scripts/validate_governance_assets.py docs/ouroboros/01_requirements_registry.md docs/ouroboros/30_code_level_work_orders.md docs/ouroboros/40_acceptance_and_test_plan.md WORKFLOW.md docs/commands.md docs/live-trading-checklist.md`
- `git diff --check`

**Step 2: Publish artifacts**
- Commit, push branch, open PR, ensure `symphony` label exists.

**Step 3: Review sweep**
- Sweep PR top-level + inline + review summaries until zero actionable feedback remains.
