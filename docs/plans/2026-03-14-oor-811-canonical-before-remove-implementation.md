# OOR-811 Canonical Before-Remove Restart Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the rejected GitHub workflow approach with a Symphony `before_remove` hook that restarts only the canonical `main` runtime after a merged worktree is removed.

**Architecture:** `WORKFLOW.md` becomes the host-side integration point by invoking a repo-owned shell script before workspace deletion. That script discovers the canonical `main` checkout with git worktree metadata, verifies the worktree being removed is merged into `origin/main`, then pulls and restarts only the canonical runtime with SHA dedupe.

**Tech Stack:** Bash, git worktree metadata, pytest, repo runtime scripts, Linear/GitHub workflow docs.

---

### Task 1: Lock the failing behavior with tests

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Create: `scripts/symphony_before_remove_canonical_restart.sh`

**Step 1: Write the failing test**

Add tests for:

- unmerged worktree -> skip without restart,
- merged worktree -> canonical main checkout stop/start + marker write,
- dry-run -> no marker and no restart commands.

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v
```

Expected: FAIL because `scripts/symphony_before_remove_canonical_restart.sh` does not exist yet.

**Step 3: Write minimal implementation**

Implement the hook script with:

- `git worktree list --porcelain` parsing,
- canonical `main` checkout discovery,
- `origin/main` fetch + ancestor check,
- SHA marker/dry-run support,
- env seams for git/stop/run commands,
- `TMUX_ATTACH=false` on unattended restart.

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_runtime_overnight_scripts.py scripts/symphony_before_remove_canonical_restart.sh
git commit -m "feat(ops): add canonical restart before-remove hook"
```

### Task 2: Wire the host-side integration point and docs

**Files:**
- Modify: `WORKFLOW.md`
- Modify: `docs/commands.md`
- Modify: `docs/live-trading-checklist.md`
- Modify: `docs/ouroboros/01_requirements_registry.md`
- Modify: `docs/ouroboros/30_code_level_work_orders.md`
- Modify: `docs/ouroboros/40_acceptance_and_test_plan.md`

**Step 1: Write the failing doc expectation**

Update the test plan mentally from the issue/review:

- `WORKFLOW.md` must stop using `true` for `before_remove`,
- docs must describe Symphony hook ownership instead of GitHub Actions.

**Step 2: Run a narrow validation to verify the gap**

Run:

```bash
rg -n 'before_remove: \\||canonical 운영 프로세스 재실행|Restart that canonical process only after a PR has been merged into `main`' WORKFLOW.md docs/commands.md docs/live-trading-checklist.md
```

Expected: `WORKFLOW.md` still shows `true`; docs still describe a manual restart policy.

**Step 3: Write minimal implementation**

Update `WORKFLOW.md` to call the new script and adjust docs/governance text to the hook-based flow.

**Step 4: Run doc validators**

Run:

```bash
python3 scripts/validate_docs_sync.py
python3 scripts/validate_ouroboros_docs.py
python3 scripts/validate_governance_assets.py docs/ouroboros/01_requirements_registry.md docs/ouroboros/30_code_level_work_orders.md docs/ouroboros/40_acceptance_and_test_plan.md WORKFLOW.md docs/commands.md docs/live-trading-checklist.md
```

Expected: PASS.

**Step 5: Commit**

```bash
git add WORKFLOW.md docs/commands.md docs/live-trading-checklist.md docs/ouroboros/01_requirements_registry.md docs/ouroboros/30_code_level_work_orders.md docs/ouroboros/40_acceptance_and_test_plan.md
git commit -m "docs(ops): wire symphony before-remove restart flow"
```

### Task 3: Prove the automation and run repo gates

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: Linear workpad comment

**Step 1: Run targeted proof**

Run:

```bash
pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v
OVERNIGHT_STATE_ROOT=/tmp/oor-811-runtime bash scripts/symphony_before_remove_canonical_restart.sh --canonical-root "$PWD" --workspace-sha "$(git rev-parse HEAD)" --dry-run
```

Expected: targeted tests pass; dry-run prints canonical root, target sha, and marker path without mutation.

**Step 2: Run repo validation**

Run:

```bash
pytest -v --cov=src --cov-report=term-missing
ruff check src/ tests/
git diff --check
```

Expected: PASS.

**Step 3: Publish and reconcile**

Run the GitHub/Linear publish flow, attach the fresh PR, sweep review comments, and update the single workpad comment with commit + validation evidence.
