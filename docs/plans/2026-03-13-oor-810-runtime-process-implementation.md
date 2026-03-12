# OOR-810 Runtime Process Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Encode the overnight runtime policy so only `main` keeps the canonical operational process while non-`main` worktrees automatically isolate runtime state and live lock paths.

**Architecture:** Add one shared shell helper for runtime-instance defaults, wire every overnight script through it, and make `src.main` read the live lock path from settings. Drive the change with regression tests that first fail on clean `origin/main`, then pass after the helper/config wiring is in place.

**Tech Stack:** Bash, Python, pytest, ruff, Linear workpad workflow

---

### Task 1: Freeze the failing proof

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Modify: `tests/test_main.py`

**Step 1: Write the failing tests**

- Add tests that call a shared runtime-instance helper and assert:
  - `main` keeps `LOG_DIR=data/overnight`, port `8080`, and the canonical lock path.
  - `feature/...` branches resolve to `data/overnight/<branch-slug>`, a non-`8080` port, and a branch-specific lock path.
- Add a `test_live_runtime_lock_uses_configured_path` case in `tests/test_main.py`.

**Step 2: Run the tests to verify they fail**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py::test_runtime_instance_defaults_keep_main_canonical \
  tests/test_runtime_overnight_scripts.py::test_runtime_instance_defaults_isolate_non_main_branch \
  tests/test_runtime_overnight_scripts.py::test_runtime_verify_monitor_uses_branch_scoped_defaults_when_unset \
  tests/test_runtime_overnight_scripts.py::test_run_overnight_uses_branch_scoped_defaults_when_log_dir_unset \
  tests/test_main.py::test_live_runtime_lock_uses_configured_path -v
```

Expected: FAIL because the shared helper does not exist, the scripts still hardcode singleton paths, and `Settings` rejects `LIVE_RUNTIME_LOCK_PATH`.

### Task 2: Implement shared runtime defaults

**Files:**
- Create: `scripts/runtime_instance_env.sh`
- Modify: `scripts/run_overnight.sh`
- Modify: `scripts/runtime_verify_monitor.sh`
- Modify: `scripts/stop_overnight.sh`
- Modify: `scripts/morning_report.sh`
- Modify: `scripts/watchdog.sh`

**Step 1: Write minimal implementation**

- Create a shared resolver that:
  - detects `main` vs non-`main`,
  - uses `OVERNIGHT_STATE_ROOT` when provided,
  - computes a stable branch slug,
  - assigns `LOG_DIR`, `DASHBOARD_PORT`, `TMUX_SESSION_PREFIX`, and `LIVE_RUNTIME_LOCK_PATH`.
- Source that helper from each overnight script before any path/port logic.

**Step 2: Run the overnight-script subset**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py::test_runtime_instance_defaults_keep_main_canonical \
  tests/test_runtime_overnight_scripts.py::test_runtime_instance_defaults_isolate_non_main_branch \
  tests/test_runtime_overnight_scripts.py::test_runtime_verify_monitor_uses_branch_scoped_defaults_when_unset \
  tests/test_runtime_overnight_scripts.py::test_run_overnight_uses_branch_scoped_defaults_when_log_dir_unset -v
```

Expected: PASS.

### Task 3: Wire live lock configuration through Python runtime

**Files:**
- Modify: `src/config.py`
- Modify: `src/main.py`
- Modify: `.env.example`

**Step 1: Write minimal implementation**

- Add `LIVE_RUNTIME_LOCK_PATH` to `Settings`.
- Use that setting inside `_acquire_live_runtime_lock`.
- Surface the new env knob in `.env.example`.

**Step 2: Run the live-lock subset**

Run:

```bash
pytest tests/test_main.py::test_live_runtime_lock_can_be_reacquired_after_release \
  tests/test_main.py::test_live_runtime_lock_uses_configured_path -v
```

Expected: PASS.

### Task 4: Document the operational policy

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/architecture.md`
- Modify: `docs/live-trading-checklist.md`

**Step 1: Update docs**

- Document that only the `main` checkout keeps the canonical continuously running process.
- Document that canonical restart happens only after a PR merges to `main`.
- Document that non-`main` worktrees auto-scope runtime state unless explicitly overridden.

**Step 2: Run docs validation**

Run:

```bash
python3 scripts/validate_docs_sync.py
```

Expected: PASS.

### Task 5: Final validation and handoff evidence

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-13-oor-810-runtime-process-design.md`
- Modify: `docs/plans/2026-03-13-oor-810-runtime-process-implementation.md`

**Step 1: Run the scoped validation suite**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py::test_runtime_instance_defaults_keep_main_canonical \
  tests/test_runtime_overnight_scripts.py::test_runtime_instance_defaults_isolate_non_main_branch \
  tests/test_runtime_overnight_scripts.py::test_runtime_verify_monitor_uses_branch_scoped_defaults_when_unset \
  tests/test_runtime_overnight_scripts.py::test_run_overnight_uses_branch_scoped_defaults_when_log_dir_unset \
  tests/test_main.py::test_live_runtime_lock_can_be_reacquired_after_release \
  tests/test_main.py::test_live_runtime_lock_uses_configured_path -v
ruff check src/main.py src/config.py tests/test_runtime_overnight_scripts.py tests/test_main.py
python3 scripts/validate_docs_sync.py
git diff --check
python3 scripts/session_handover_check.py --strict
```

Expected: PASS across all commands.

**Step 2: Update the Linear workpad**

- Check off completed plan, acceptance, and validation items.
- Record reproduction evidence, pull evidence, GitHub preflight evidence, and final validation results.
