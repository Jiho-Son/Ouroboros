# OOR-811 Canonical Restart Rework Design

**Ticket:** `OOR-811`

**Problem:** `OOR-810` isolated runtime defaults per branch, but the canonical `main` runtime still restarts only by operator memory. The rejected first attempt used a GitHub workflow trigger, but reviewer feedback requires the restart to happen from the host-side Symphony lifecycle instead.

**Design input:** This is an unattended orchestration session. The Linear issue body, reviewer comments on PR #811, and the current `WORKFLOW.md`/Symphony `before_remove` spec are treated as the approval boundary for the redesign.

## Constraints

- The integration point must live in the host-side Symphony workflow, not in GitHub Actions.
- Only the canonical `main` checkout may be restarted.
- Non-`main` worktrees must keep their branch-scoped logs, locks, ports, and tmux session prefixes untouched.
- The hook runs in the feature worktree that is about to be removed, so it must discover the canonical checkout itself.
- `before_remove` failures are best-effort according to the Symphony spec, so the script must log clearly and exit safely when it cannot prove a restart is required.

## Options Considered

### Option 1: Keep the GitHub workflow and SSH into the host

Rejected. This was the first implementation and the review explicitly pushed back on CI controlling a local long-running runtime.

### Option 2: Use Symphony `hooks.before_remove` in `WORKFLOW.md`

Recommended. `WORKFLOW.md` already owns the repository's Symphony configuration and currently sets `hooks.before_remove: true`. Replacing that no-op with a repo-side script keeps the orchestration point versioned with the repo while running on the host that owns the canonical runtime.

The hook runs inside the feature worktree right before deletion. From there it can:

1. Read the current worktree branch and HEAD.
2. Discover the canonical `main` checkout via `git worktree list --porcelain`.
3. Fetch `origin/main` from the canonical checkout.
4. Verify that the worktree being removed is actually merged into `origin/main`.
5. Pull the canonical checkout forward and restart only that runtime.
6. Record the processed `origin/main` SHA so repeated cleanup events do not restart twice.

### Option 3: Run a periodic local poller

Rejected. It adds another host daemon and delays restart detection. The repository already has a lifecycle event slot in `WORKFLOW.md`, so a poller would be extra moving parts without better guarantees.

## Chosen Design

Use a repo-owned `before_remove` hook script wired from `WORKFLOW.md`.

### Hook entrypoint

- Add `scripts/symphony_before_remove_canonical_restart.sh`.
- Update `WORKFLOW.md` so `hooks.before_remove` executes that script from the workspace root.

### Merge detection

The script runs in the worktree being removed and uses only git state:

- `workspace_branch=$(git branch --show-current)`
- `workspace_sha=$(git rev-parse HEAD)`
- Discover the canonical `main` checkout from `git worktree list --porcelain`.
- `git -C "$canonical_root" fetch origin`
- `target_sha=$(git -C "$canonical_root" rev-parse origin/main)`
- Restart only if `workspace_sha` is an ancestor of `target_sha`.

This means:

- merged feature worktrees trigger the restart path,
- abandoned/unmerged worktrees skip,
- deleting a stale already-up-to-date worktree is harmless because the dedupe marker already matches `target_sha`.

### Canonical restart behavior

The hook script operates on the canonical checkout path only:

- pull `origin/main` with `--ff-only`,
- resolve runtime defaults as `branch=main`,
- stop the canonical runtime,
- start it again with `TMUX_ATTACH=false` so the unattended hook does not block on interactive attach,
- record `target_sha` in a marker under the canonical runtime state root.

### Safety boundaries

- Canonical path discovery must require `branch refs/heads/main`.
- Restart commands run with `RUNTIME_REPO_ROOT=<canonical-root>` and `RUNTIME_BRANCH_NAME=main`.
- Marker/log files live in canonical state under `data/overnight`, not under the feature worktree being removed.
- If canonical `main` cannot be found, the hook logs and exits non-zero, but cleanup continues per Symphony semantics.

## Test Strategy

Add shell-script regression tests that prove:

1. the hook skips unmerged worktrees,
2. the hook discovers the canonical main worktree and restarts it exactly once for a merged target SHA,
3. dry-run mode reports the canonical root/target marker without mutating state.

The script will expose lightweight env overrides for `git`, `run`, and `stop` commands so tests can fake host orchestration without touching a real long-running runtime.

## Documentation Impact

- `WORKFLOW.md`: versioned `before_remove` hook contract.
- `docs/commands.md`: replace the manual “restart after main merge” wording with the hook-based flow and dry-run verification command.
- `docs/live-trading-checklist.md`: update the operator checklist to verify the hook-managed canonical restart path.
- `docs/ouroboros/*`: add REQ/TASK/TEST mapping for the new host-side automation requirement.
