# OOR-809 Harness Engineering Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a working agent entrypoint, bootstrap path, and unattended GitHub PR harness for this repo.

**Architecture:** Keep the harness small and repo-local. Use a top-level `AGENTS.md` for discovery, a shell bootstrap script for fresh worktrees, and lightweight tests/validators to catch drift across both bootstrap and GitHub publish guidance.

**Tech Stack:** Markdown, bash, pytest, Python stdlib

---

### Task 1: Lock the harness expectations with tests

**Files:**
- Modify: `tests/test_validate_docs_sync.py`
- Create: `tests/test_worktree_init.py`
- Create: `tests/test_github_pr.py`

**Step 1: Write the failing test**

- Require `AGENTS.md` in docs sync coverage
- Require `.codex/worktree_init.sh --dry-run` to describe a Python bootstrap flow
- Require active harness docs/skills to point at the GitHub unattended publish path
- Require `scripts/github_pr.py` to cover token and repo-resolution basics

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_validate_docs_sync.py tests/test_worktree_init.py tests/test_github_pr.py -q`
Expected: FAIL because the validator misses the GitHub harness surface, the bootstrap dry-run is nondeterministic, and the helper lacks direct regression coverage

**Step 3: Write minimal implementation**

- Extend docs sync validation for `AGENTS.md`
- Extend docs sync validation for active GitHub harness docs/skills
- Replace the template bootstrap script with a repo-compatible flow
- Add basic `scripts/github_pr.py` regression coverage

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_validate_docs_sync.py tests/test_worktree_init.py tests/test_github_pr.py -q`
Expected: PASS

### Task 2: Add the repo agent entrypoint

**Files:**
- Create: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `agents.md`

**Step 1: Write the smallest entrypoint doc**

- Link workflow, commands, constraints, docs hub, and bootstrap
- Keep content concise and TOC-oriented

**Step 2: Preserve existing entrypoints**

- Redirect `CLAUDE.md` and `agents.md` to `AGENTS.md`

**Step 3: Validate docs**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

### Task 3: Finish validation

**Files:**
- Modify: `scripts/validate_docs_sync.py`
- Modify: `docs/workflow.md`
- Modify: `docs/commands.md`
- Modify: `docs/agent-constraints.md`
- Modify: `.codex/skills/push/SKILL.md`

**Step 1: Run targeted regression checks**

Run: `pytest tests/test_validate_docs_sync.py tests/test_worktree_init.py tests/test_validate_pr_body.py tests/test_github_pr.py -q`
Expected: PASS

**Step 2: Run docs sync validator**

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Run bootstrap dry-run proof**

Run: `bash .codex/worktree_init.sh --dry-run`
Expected: PASS with deterministic Python bootstrap commands in stdout

**Step 4: Record ticket evidence**

- Update the Linear workpad with reproduction, fix summary, and validation output
