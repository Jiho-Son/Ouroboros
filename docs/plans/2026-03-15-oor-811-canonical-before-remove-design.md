# OOR-811 Canonical Before-Remove Restart Design

## Context

- OOR-810 isolated runtime defaults so canonical runtime lives only on `main` while non-`main` worktrees use branch-scoped state/port/lock prefixes.
- Fresh `origin/main` still ships `WORKFLOW.md` with `hooks.before_remove: true`, so there is no host-side automation path to restart canonical runtime after merge.
- Rework feedback on PR #822 requires hardening before merge:
  - `mkdir` lock fallback must have timeout (avoid infinite wait on stale lockdir),
  - stop/start sequence must emit explicit critical signal when start fails after stop,
  - canonical worktree parser must safely handle bare entries in `git worktree list --porcelain`.

## Problem

There is no exact-once canonical restart automation on merge-to-`main`, and the previous attempt needs additional operational safety in lock and failure handling.

## Approach Options

### Option A (recommended): Symphony `before_remove` hook + repo-owned script

- Wire `WORKFLOW.md` `hooks.before_remove` to `scripts/symphony_before_remove_canonical_restart.sh`.
- Script executes during worktree cleanup, verifies merge-to-main for the deleting worktree, and restarts only canonical `main` runtime.

Pros:
- Correct host-side lifecycle event without external daemon.
- Script remains versioned, testable, and reviewable in repo.
- Minimal blast radius to existing runtime flow.

Cons:
- Depends on host tools (`git`, optional `gh`, shell lock primitives).

### Option B: External host watcher (not selected)

- Separate daemon watches merges and restarts runtime.

Cons:
- Out of scope and adds deployment/control-plane surface.

### Option C: CI-triggered restart (rejected)

- CI runs restart post-merge.

Cons:
- Canonical runtime is host-owned; CI cannot safely control local long-running process.

## Detailed Design

### Integration point

- Replace `WORKFLOW.md` `hooks.before_remove` with:
  - `bash scripts/symphony_before_remove_canonical_restart.sh`

### Hook script behavior

1. Resolve deleting worktree branch/SHA (`git branch --show-current`, `git rev-parse HEAD`).
2. Skip when deleting worktree itself is `main`.
3. Discover canonical `main` root from `git worktree list --porcelain`.
4. Validate canonical root branch is exactly `main`.
5. Fetch `origin/main` at canonical root and resolve target SHA.
6. Decide merge inclusion:
  - primary: `git merge-base --is-ancestor <workspace_sha> <origin/main_sha>`
  - fallback: recent closed PR lookup via `gh api` for squash-merge cases.
7. Skip if not merged.
8. Resolve canonical state files for lock/log/marker under canonical overnight state root.
9. Acquire lock:
  - prefer `flock`,
  - fallback `mkdir` lockdir with timeout and explicit timeout error logging.
10. Pull canonical root `git pull --ff-only origin main`.
11. Deduplicate restart by marker SHA.
12. Restart canonical runtime (`stop_overnight.sh` then `run_overnight.sh`) with explicit CRITICAL log on start failure after stop.
13. Persist marker atomically (`tmp + mv`) and log completion.

### Runtime boundary invariants

- Restart target is only canonical `main` checkout.
- Non-`main` worktree runtime paths (`LOG_DIR`, lock files, ports) are untouched.
- Restart dedupe marker and hook logs remain under canonical state root.

### Error handling

- Canonical discovery failure: fail closed with non-zero.
- Lock timeout: fail with deterministic error and no restart side effects.
- Pull/start failure: fail with explicit diagnostics.
- Dry-run: no file/process mutation; emit planned actions only.

## Test Strategy

- Add hook-focused pytest integration tests with fake `git`/`gh` shims to verify:
  - unmerged skip,
  - merged restart on canonical only,
  - squash-merge fallback,
  - dry-run no side effects,
  - dedupe exact-once marker,
  - lock fallback timeout behavior,
  - start-failure critical diagnostics,
  - bare worktree parsing robustness.

## Docs + Governance

- Update operator docs (`docs/commands.md`, `docs/live-trading-checklist.md`) for before_remove flow and verification steps.
- Update governance mapping docs for new REQ/TASK/TEST rows tied to canonical restart automation.

## Risks

- GitHub fallback checks recent closed PR metadata and may miss old/superseded branch SHA in heavy traffic.
- Hook is executed during workspace deletion lifecycle; failures rely on log monitoring for fast remediation.
