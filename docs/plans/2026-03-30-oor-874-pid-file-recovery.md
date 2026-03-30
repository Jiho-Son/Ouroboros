# OOR-874 PID File Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `scripts/runtime_verify_monitor.sh` recreate a missing/stale `app.pid` from the latest run log when the logged app PID is still alive, so harness process checks stop failing on missing PID files.

**Architecture:** Keep branch-scoped runtime isolation unchanged. Use the latest `run_*.log` entry `app pid=<pid>` as the recovery source, restore `app.pid` only when that PID is still alive, and leave the existing `pgrep`-based liveness fallback as a secondary signal only.

**Tech Stack:** Bash runtime scripts, pytest subprocess integration tests, runtime operator docs.

---

### Task 1: Reproduce the missing-PID gap with a failing test

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Read: `scripts/runtime_verify_monitor.sh`

**Step 1: Write the failing test**

Add a subprocess test that:
- starts a disposable long-lived process,
- writes a synthetic `run_*.log` containing `app pid=<pid>`,
- runs `scripts/runtime_verify_monitor.sh` once with no `app.pid`,
- expects `app.pid` to be recreated with the logged PID.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime_overnight_scripts.py -k 'restores_missing_app_pid' -v`

Expected: FAIL because the current monitor logs liveness but leaves `app.pid` absent.

### Task 2: Implement log-based PID recovery in the runtime monitor

**Files:**
- Modify: `scripts/runtime_verify_monitor.sh`
- Test: `tests/test_runtime_overnight_scripts.py`

**Step 1: Write minimal implementation**

Add a small helper in `scripts/runtime_verify_monitor.sh` that:
- finds the latest `app pid=<pid>` line in the current run log,
- validates the PID with `kill -0`,
- rewrites `"$LOG_DIR/app.pid"` when the file is missing or stale,
- logs a short recovery note.

**Step 2: Run targeted test to verify it passes**

Run: `pytest tests/test_runtime_overnight_scripts.py -k 'restores_missing_app_pid' -v`

Expected: PASS with `app.pid` recreated from the logged live PID.

### Task 3: Guard behavior and docs

**Files:**
- Modify: `docs/commands.md`
- Test: `tests/test_runtime_overnight_scripts.py`

**Step 1: Add one doc update**

Document that `scripts/runtime_verify_monitor.sh` can recover a missing `app.pid` from the latest run log when the underlying process is still alive.

**Step 2: Run scoped regression checks**

Run:
- `pytest tests/test_runtime_overnight_scripts.py -k 'runtime_verify_monitor' -v`
- `ruff check scripts tests/test_runtime_overnight_scripts.py`
- `python3 scripts/validate_docs_sync.py`

Expected: All commands succeed.
