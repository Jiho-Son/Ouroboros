# OOR-811 Canonical Main Restart Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automate the canonical overnight runtime restart after merges to `main` without touching non-`main` worktrees.

**Architecture:** Add a repo-owned host restart script plus a GitHub workflow that triggers on `push` to `main`, SSHes to the canonical host checkout, pulls the merged commit, and invokes the restart script with per-SHA dedupe. Keep runtime isolation anchored on `scripts/runtime_instance_env.sh`.

**Tech Stack:** Bash, GitHub Actions, pytest, Linear workpad workflow

---

### Task 1: Freeze the failing proof

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`

**Step 1: Write the failing tests**

- Add restart-script tests that expect:
  - non-`main` execution fails,
  - a new target SHA triggers stop/start once and records the marker,
  - a repeated target SHA exits cleanly without stop/start side effects,
  - dry-run mode prints the canonical restart plan without mutating marker files.

**Step 2: Run the tests to verify they fail**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_rejects_non_main_branch \
  tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_records_and_skips_duplicate_sha \
  tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_dry_run_does_not_mutate_state -v
```

Expected: FAIL because the restart script does not exist yet.

### Task 2: Implement the host-side restart script

**Files:**
- Create: `scripts/restart_canonical_main_runtime.sh`

**Step 1: Write minimal implementation**

- Add argument parsing for `--target-sha` and `--dry-run`.
- Source `scripts/runtime_instance_env.sh` and reject non-`main` branches.
- Create canonical restart log + marker paths under the resolved canonical `LOG_DIR`.
- Add per-SHA dedupe and serial execution via a restart lock.
- Run overridable stop/start commands so tests can stub them safely.

**Step 2: Run the restart-script subset**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_rejects_non_main_branch \
  tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_records_and_skips_duplicate_sha \
  tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_dry_run_does_not_mutate_state -v
```

Expected: PASS.

### Task 3: Add the merge-to-main workflow integration point

**Files:**
- Create: `.github/workflows/canonical-runtime-restart.yml`

**Step 1: Write minimal implementation**

- Trigger on `push` to `main`.
- Add `workflow_dispatch` inputs for `target_sha` and `dry_run`.
- Serialize with workflow concurrency.
- Use host SSH secrets and a remote shell command that:
  - changes into the canonical checkout,
  - verifies/pulls `origin/main`,
  - runs `bash scripts/restart_canonical_main_runtime.sh --target-sha <sha>` with optional `--dry-run`.

**Step 2: Perform a local dry-run proof**

Run:

```bash
OVERNIGHT_STATE_ROOT=/tmp/oor-811-runtime \
RUNTIME_BRANCH_NAME=main \
bash scripts/restart_canonical_main_runtime.sh --target-sha test-sha --dry-run
```

Expected: PASS with dry-run output showing canonical state paths and no marker mutation.

### Task 4: Document the operational contract

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/live-trading-checklist.md`
- Modify: `docs/disaster_recovery.md`

**Step 1: Update docs**

- Document the new workflow trigger, required host secrets, and recovery/dry-run path.
- Document that the host restart script only operates in the canonical `main` checkout and deduplicates by merged SHA.
- Keep the non-`main` runtime isolation guidance intact.

**Step 2: Run docs validation**

Run:

```bash
python3 scripts/validate_docs_sync.py
```

Expected: PASS.

### Task 5: Final validation and evidence

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-13-oor-811-canonical-main-restart-design.md`
- Modify: `docs/plans/2026-03-13-oor-811-canonical-main-restart-implementation.md`

**Step 1: Run the scoped validation suite**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_rejects_non_main_branch \
  tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_records_and_skips_duplicate_sha \
  tests/test_runtime_overnight_scripts.py::test_restart_canonical_main_runtime_dry_run_does_not_mutate_state -v
OVERNIGHT_STATE_ROOT=/tmp/oor-811-runtime \
RUNTIME_BRANCH_NAME=main \
bash scripts/restart_canonical_main_runtime.sh --target-sha test-sha --dry-run
python3 scripts/validate_docs_sync.py
git diff --check
python3 scripts/session_handover_check.py --strict
```

Expected: PASS across all commands.

**Step 2: Update the Linear workpad**

- Check off completed plan, acceptance, and validation items.
- Record reproduction evidence, pull evidence, dry-run proof, and final validation results.
