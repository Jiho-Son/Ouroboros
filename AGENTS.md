# Agent Entry Point

Start here for Claude, Codex, and other repo-writing agents.

This file is intentionally short. Treat it as a table of contents plus a fast
bootstrap path, not the full policy manual.

## First Read

1. [Workflow Guide](docs/workflow.md)
2. [Command Reference](docs/commands.md)
3. [Agent Constraints](docs/agent-constraints.md)
4. [Agent Persona Rules](docs/agents.md)
5. [Documentation Hub](docs/README.md)

## Fresh Worktree Bootstrap

Use the repo bootstrap script after checking out a new branch or worktree:

```bash
bash .codex/worktree_init.sh
```

What it does:

- creates `.venv` with `python3 -m venv --system-site-packages`
- installs the repo in editable mode with dev dependencies via `pip install --no-build-isolation -e ".[dev]"`
- copies `.env.example` to `.env` when a local env file is missing
- prints the next validation commands to run

Dry-run the bootstrap plan without changing the tree:

```bash
bash .codex/worktree_init.sh --dry-run
```

## Session Start Gate

Before implementation or verification work:

1. Append a same-day entry to `workflow/session-handover.md`
2. Run `python3 scripts/session_handover_check.py --strict`

## Repo Hotspots

- `src/main.py`: runtime entrypoint
- `src/config.py`: environment and settings loading
- `src/dashboard/`: FastAPI monitoring surface
- `scripts/`: validation and operations helpers
- `tests/`: repo regression coverage
- `docs/`: workflow, commands, testing, and architecture references

## Standard Verification

```bash
pytest -v --cov=src --cov-report=term-missing
ruff check src/ tests/
python3 scripts/validate_docs_sync.py
```

Prefer a narrow failing test first for the specific change, then run the broader
repo checks required by the touched surface.

## Non-Negotiable Safety Rules

- Do not weaken `src/core/risk_manager.py`
- Do not merge untested code
- Do not commit directly to `main`
- Keep docs aligned when the workflow or agent harness changes
