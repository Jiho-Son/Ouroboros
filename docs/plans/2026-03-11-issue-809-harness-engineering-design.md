# OOR-809 Harness Engineering Design

## Goal

Make the repo bootable and legible for coding agents using the harness
engineering guidance from OpenAI's article, without changing the trading
system's runtime behavior.

## Current Gaps

- The repo has no top-level `AGENTS.md`, so agent entry depends on ad hoc tool
  context instead of an in-repo TOC.
- `.codex/worktree_init.sh` is a copied template for a different stack and fails
  against this Python repository.
- The existing docs sync validator does not protect the agent entrypoint or
  unattended GitHub publish path from drift.
- Core repo workflow docs and agent skills still describe the retired
  Gitea/`tea` path or old `elixir` validation commands, which misroutes agents
  during unattended PR delivery.

## Options Considered

### 1. Docs-only fix

Add `AGENTS.md` and leave bootstrap as-is.

- Pros: smallest change
- Cons: still leaves fresh worktree setup broken, so the harness is incomplete

### 2. Bootstrap-only fix

Repair `.codex/worktree_init.sh` and leave agent entrypoints fragmented.

- Pros: fixes the most concrete failure
- Cons: agents still lack a canonical repo TOC, so discovery remains weak

### 3. Recommended: minimal harness bundle

Add a concise `AGENTS.md`, repair `.codex/worktree_init.sh`, and extend docs
validation to cover both.

- Pros: fixes the broken path and makes the entrypoint discoverable
- Cons: leaves publish-path drift unguarded

### 4. Recommended rework: full unattended harness slice

Ship the minimal harness bundle and align the active workflow/skill surface with
the current GitHub PR path used by unattended sessions.

- Pros: fixes the broken bootstrap path and the stale delivery guidance together
- Cons: touches more docs and agent-harness files in one ticket

## Chosen Design

Implement option 4.

- Add a short `AGENTS.md` that points agents to workflow, commands,
  constraints, bootstrap, and the main code hotspots.
- Replace the broken worktree init template with a Python-focused bootstrap
  flow that supports deterministic `--dry-run` output for validation.
- Keep `CLAUDE.md` and `agents.md` as thin redirects so existing entrypoints do
  not break.
- Extend `scripts/validate_docs_sync.py` and tests so both `AGENTS.md` and the
  GitHub publish path remain part of the maintained harness surface.
- Update the active workflow docs and `.codex/skills/push/SKILL.md` so
  unattended agents use `python3 scripts/github_pr.py`,
  `python3 scripts/validate_pr_body.py`, and repo-accurate validation commands
  instead of stale Gitea or `elixir` instructions.
