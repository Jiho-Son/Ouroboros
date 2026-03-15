# OOR-811 Canonical Before-Remove Restart Design

## Context

- OOR-810 established branch-scoped runtime defaults so only `main` is canonical while non-`main` worktrees are isolated by `LOG_DIR`, `DASHBOARD_PORT`, `LIVE_RUNTIME_LOCK_PATH`, and `TMUX_SESSION_PREFIX`.
- Fresh `origin/main` baseline still has `WORKFLOW.md` `hooks.before_remove: true` (no host-side restart automation).
- Rework review from closed PR #816 requires additional hardening before merge:
  - no silent lock bypass when `flock` is unavailable,
  - deterministic command execution (`bash -c`, not `bash -lc`),
  - explicit diagnostics on `pull --ff-only` failure,
  - atomic restart marker writes,
  - controlled fetch output noise,
  - documented squash-merge GitHub fallback limitation.

## Problem

Canonical runtime restart after merge-to-`main` remains manual in the baseline, and previous automation attempt needs safety hardening to be mergeable.

## Approach Options

### Option A (recommended): `before_remove` hook + repo-owned restart script

- Wire `WORKFLOW.md` `hooks.before_remove` to `scripts/symphony_before_remove_canonical_restart.sh`.
- Script runs in the removed worktree context, checks whether that worktree SHA is merged into `origin/main`, then restarts only canonical `main` checkout runtime.
- Preserve exact-once behavior with marker/lock under canonical state root.

Pros:
- Host-side event timing is correct for Symphony worktree cleanup lifecycle.
- Directly satisfies issue request and reviewer guidance.
- Keeps restart logic versioned and testable in the repo.

Cons:
- Hook remains best-effort and dependent on host runtime tooling (`git`, optional `gh`).

### Option B: external host daemon/watcher (not selected)

- Separate process watches merge events and restarts canonical runtime out-of-band.

Pros:
- Could centralize orchestration.

Cons:
- Out-of-scope for this repo-only ticket; extra deployment surface; weaker local testability.

### Option C: CI-owned restart (rejected)

- Trigger restart from GitHub Actions post-merge.

Cons:
- Rejected in review because canonical runtime is host-owned, not CI-owned.

## Detailed Design

### Integration point

- Replace `WORKFLOW.md`:
  - `hooks.before_remove: true`
  - -> `hooks.before_remove: bash scripts/symphony_before_remove_canonical_restart.sh`

### Script behavior

1. Resolve workspace branch/SHA from the to-be-removed worktree.
2. Skip immediately when invoked from `main` worktree cleanup.
3. Discover canonical checkout from `git worktree list --porcelain` by selecting `refs/heads/main` worktree.
4. Validate canonical root is actually on branch `main`.
5. Fetch `origin/main` in canonical root (quiet output) and detect merge:
   - primary: `git merge-base --is-ancestor <workspace_sha> <origin/main_sha>`
   - fallback: GitHub closed PR lookup for squash merges (`gh api`).
6. If not merged, exit without restart.
7. Resolve canonical state paths (`marker`, `lock`, `log`) under canonical overnight state root.
8. Acquire lock:
   - prefer `flock` when available,
   - fallback to `mkdir`-based lock directory with trap cleanup and warning log.
9. Pull canonical root fast-forward only and log failures with explicit diagnostics.
10. Deduplicate by marker target SHA.
11. Restart sequence:
   - stop canonical runtime,
   - start canonical runtime with `RUNTIME_BRANCH_NAME=main` and `TMUX_ATTACH=false`.
12. Persist processed target SHA via atomic write (`tmp + mv`).
13. Emit deterministic announce/log lines for operators and tests.

### Runtime boundary guarantees

- Only canonical `main` root is restarted.
- Script never mutates non-`main` worktree runtime files.
- Existing OOR-810 branch-scoped defaults continue to isolate concurrent validation runs.

### Error handling

- Fail closed when canonical root cannot be discovered/validated.
- Log and exit non-zero for canonical pull failures.
- Keep dry-run path side-effect free and fully report planned actions.

## Test Strategy

- Add hook-focused tests in `tests/test_runtime_overnight_scripts.py` using fake `git`/`gh` binaries:
  - unmerged branch cleanup skip,
  - squash-merge fallback success,
  - main-worktree skip,
  - restart dedupe by target SHA,
  - dry-run no-mutation proof,
  - non-`flock` lock fallback path,
  - pull failure diagnostics,
  - atomic marker write observable outcome.
- Keep existing runtime defaults tests green to prove boundary preservation.

## Docs + Governance Updates

- Update operator docs (`docs/commands.md`, `docs/live-trading-checklist.md`) with hook automation and validation steps.
- Update governance mappings:
  - `docs/ouroboros/01_requirements_registry.md`
  - `docs/ouroboros/30_code_level_work_orders.md`
  - `docs/ouroboros/40_acceptance_and_test_plan.md`

## Risks

- Hook execution is best-effort during workspace cleanup; failures require log-driven triage.
- GitHub fallback checks recent closed PRs; stale branch/head SHA drift after squash merge is documented as an edge limitation.
