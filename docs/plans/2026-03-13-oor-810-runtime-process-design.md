# OOR-810 Runtime Process Design

**Ticket:** `OOR-810`

**Problem:** Overnight runtime scripts still assume a single hardcoded checkout and singleton runtime state. That breaks the operational policy that requires the continuously running canonical process to live only on `main`, restart only after a PR merges to `main`, and allow concurrent validation runs from other worktrees.

**Approval source:** This is an unattended orchestration session, so the Linear ticket body plus Jiho Son's linked comments are treated as the design input and approval boundary.

## Constraints

- Keep the canonical continuously running runtime only on the `main` branch checkout.
- Non-`main` worktrees must be able to run the same scripts concurrently without sharing logs, lock files, dashboard ports, or tmux session names.
- `src.main` live-mode singleton locking must follow the branch-scoped runtime state instead of a hardcoded path.
- The restart policy is procedural: restart the canonical runtime only after a PR merges to `main`.

## Options

### Option 1: Hardcode a different path per script

Patch each shell script independently with branch checks and duplicate path logic.

- Pros: small local edits.
- Cons: drift risk across scripts, duplicated slug/port logic, harder to test.

### Option 2: Shared runtime-instance resolver

Add one shared shell helper that derives runtime defaults from the current branch and an optional state-root override, then source it from all overnight scripts.

- Pros: single source of truth for `LOG_DIR`, `DASHBOARD_PORT`, `TMUX_SESSION_PREFIX`, and `LIVE_RUNTIME_LOCK_PATH`; easiest to test; matches the operational policy cleanly.
- Cons: requires touching several scripts at once.

### Option 3: Force every worktree run to pass explicit env vars

Keep scripts simple and require operators/tests to set `LOG_DIR`, `DASHBOARD_PORT`, and lock paths manually.

- Pros: minimal code changes.
- Cons: too error-prone for unattended operations and does not encode the policy in the default path.

## Decision

Choose **Option 2**.

Create `scripts/runtime_instance_env.sh` as the shared resolver. `main` keeps the canonical defaults: repo root, `data/overnight`, dashboard port `8080`, default tmux prefix, and `data/overnight/live_runtime.lock`. Non-`main` worktrees derive a branch slug and use branch-scoped state under `data/overnight/<branch-slug>`, a deterministic non-`8080` dashboard port, a branch-specific tmux session prefix, and a branch-specific live runtime lock path.

## Implementation Shape

1. Add failing tests that prove `main` stays canonical, non-`main` branches isolate state, and `src.main` respects a configurable `LIVE_RUNTIME_LOCK_PATH`.
2. Add the shared shell helper and source it from `run_overnight`, `runtime_verify_monitor`, `stop_overnight`, `morning_report`, and `watchdog`.
3. Add `LIVE_RUNTIME_LOCK_PATH` to `Settings` and use it inside `_acquire_live_runtime_lock`.
4. Document the canonical-main and post-merge restart policy in operator docs and command references.

## Verification

- Capture the current hardcoded defaults from clean `origin/main` as the reproduction signal before implementation.
- Run the targeted regression suite for the overnight scripts and live lock path.
- Run lint, docs sync, diff hygiene, and the strict handover gate.
