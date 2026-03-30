# OOR-874 PID File Recovery Design

**Ticket:** `OOR-874`

**Related implementation plan:** [`2026-03-30-oor-874-pid-file-recovery.md`](2026-03-30-oor-874-pid-file-recovery.md)

**Problem:** The harness reported `PID 파일 없음: data/overnight/app.pid`. The runtime monitor already treats a live process as healthy even when `app.pid` is missing, but it does not recreate the missing PID file. That leaves external process checks with a false negative even while the app process is still alive.

**Approval source:** This is an unattended orchestration session, so the Linear ticket body is treated as the design input and approval boundary.

## Reproduction

- On the current branch, `scripts/runtime_instance_env.sh` resolves the default `LOG_DIR` to `data/overnight/<branch-slug>`, which confirms the repo already expects branch-scoped runtime state outside `main`.
- A direct repro with a fake live process and `scripts/runtime_verify_monitor.sh` shows:
  - the monitor exits `0`,
  - logs `[COVERAGE] LIVE_MODE=PASS source=process_liveness`,
  - but still leaves `app.pid` absent.

## Constraints

- Keep canonical-runtime isolation intact: do not write compatibility PID files into the shared canonical path from non-`main` worktrees.
- Avoid PID restoration based on ambiguous process scans alone.
- Keep the existing monitor liveness fallback so missing PID files do not immediately mark the runtime dead.

## Options

### Option 1: Always mirror `app.pid` into `data/overnight/app.pid`

- Pros: directly satisfies the harness path.
- Cons: breaks branch-scoped runtime isolation and risks cross-worktree collisions.

### Option 2: Restore missing `app.pid` from the latest run log

- Pros: uses the PID that `scripts/run_overnight.sh` already wrote to `run_*.log`, preserves branch scoping, and only heals when the logged PID is still alive.
- Cons: depends on a recent run log existing.

### Option 3: Restore `app.pid` from `pgrep` discovery

- Pros: works even without a run log.
- Cons: process discovery is noisy in CI/sandbox contexts and can match unrelated command lines.

## Decision

Choose **Option 2**.

Teach `scripts/runtime_verify_monitor.sh` to treat the latest `app pid=<pid>` entry in `run_*.log` as the authoritative recovery source when `app.pid` is missing or stale. Only rewrite `app.pid` when that logged PID is still alive. Keep the existing process-liveness fallback for coverage logging, but do not use it as the primary restoration mechanism.

## Implementation Shape

1. Add a failing regression test proving that a live PID present in the latest run log is not currently restored into `app.pid`.
2. Update `scripts/runtime_verify_monitor.sh` to recover `app.pid` from the latest run log before its heartbeat/liveness evaluation.
3. Document the self-heal behavior in operator-facing runtime docs so the process contract is explicit.

## Verification

- `pytest tests/test_runtime_overnight_scripts.py -k 'runtime_verify_monitor and app_pid' -v`
- `ruff check scripts tests/test_runtime_overnight_scripts.py`
- `python3 scripts/validate_docs_sync.py`
