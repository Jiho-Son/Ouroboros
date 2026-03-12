# OOR-811 Canonical Main Restart Design

**Ticket:** `OOR-811`

**Problem:** The repo now encodes branch-scoped overnight runtime defaults, but it still has no automated path that detects a merge to `main` and restarts the canonical long-running process exactly once in the canonical `main` checkout.

**Approval source:** This is an unattended orchestration session, so the Linear ticket body is treated as the design input and approval boundary.

## Constraints

- The integration point must fire from a merge-to-`main` event, not from an operator remembering a manual checklist.
- Only the canonical `main` checkout may be restarted automatically.
- Non-`main` worktrees must keep their own `LOG_DIR`, `LIVE_RUNTIME_LOCK_PATH`, `DASHBOARD_PORT`, and tmux namespace untouched.
- Retry or workflow re-run scenarios must not restart the same merged commit more than once.
- The repo can define workflow + host-side scripts and documentation, but it cannot assume host secrets are present inside this sandbox.

## Options

### Option 1: Keep the restart policy manual

Leave the current docs/checklist language in place and add more operational notes.

- Pros: no code or workflow changes.
- Cons: does not close the actual gap; merge detection and exactly-once restart are still manual.

### Option 2: Host-local git hook only

Add a host script and tell operators to wire it to `post-merge` or a local deployment wrapper.

- Pros: no CI secrets required.
- Cons: detection happens only when somebody already performs a host-side merge/pull, so the repo still does not own a reliable merge-to-`main` trigger.

### Option 3: GitHub push-to-main workflow plus host-side restart script

Add a GitHub Actions workflow that triggers on `push` to `main`, then SSHes into the canonical host checkout, pulls `origin/main`, and runs a repo-owned restart script. The restart script enforces `main`-only execution, serializes via a restart lock, and deduplicates by merged commit SHA.

- Pros: direct merge-to-`main` integration point, restart intent lives in versioned repo code, retries remain safe via per-SHA dedupe, and the canonical/non-canonical boundary stays inside the existing runtime defaults.
- Cons: requires host SSH secrets and a documented host checkout path.

## Decision

Choose **Option 3**.

The repo will add:

1. A new host-side script that:
   - resolves runtime defaults through `scripts/runtime_instance_env.sh`,
   - refuses to run outside the `main` checkout,
   - records the last processed merge SHA under the canonical runtime state,
   - skips duplicate restart requests for the same SHA,
   - runs `scripts/stop_overnight.sh` then `scripts/run_overnight.sh`, and
   - supports `--dry-run` for unattended validation.
2. A GitHub Actions workflow triggered by `push` to `main` (plus `workflow_dispatch` for dry-run/manual recovery) that SSHes into the canonical host and runs the restart flow with the pushed SHA.
3. Targeted regression tests for the restart script’s branch gate and exactly-once behavior.
4. Operator docs covering required secrets, host expectations, and dry-run verification.

## Implementation Shape

1. Add failing tests for:
   - rejecting auto-restart outside `main`,
   - recording the processed SHA and skipping duplicates,
   - dry-run output that shows canonical paths without touching the live runtime.
2. Implement `scripts/restart_canonical_main_runtime.sh` with argument parsing, logging, a restart marker file, and overridable stop/start commands for tests.
3. Add `.github/workflows/canonical-runtime-restart.yml` that:
   - triggers on `push` to `main`,
   - serializes runs with workflow concurrency,
   - supports `workflow_dispatch` with `dry_run`,
   - uses repo secrets for host/user/checkout path/SSH key,
   - SSHes to the canonical checkout, pulls `origin/main`, and invokes the restart script with the target SHA.
4. Document the automation contract and validation path in operator-facing docs.

## Verification

- Reproduce the current gap by showing that no existing GitHub workflow references the overnight runtime scripts while docs still describe a manual post-merge restart.
- Run targeted regression tests for the restart script.
- Run the restart script locally in `--dry-run` mode with a temporary runtime state root.
- Run docs sync and the strict handover gate before completion.
