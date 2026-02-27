# Agent Constraints

This document records **persistent behavioral constraints** for agents working on this repository.
It is distinct from `docs/requirements-log.md`, which records **project/product requirements**.

## Scope

- Applies to all AI agents and automation that modify this repo.
- Supplements (does not replace) `docs/agents.md` and `docs/workflow.md`.

## Persistent Rules

1. **Workflow enforcement**
   - Follow `docs/workflow.md` for all changes.
   - Before any Gitea issue/PR/comment operation, read `docs/commands.md` and `docs/workflow.md` troubleshooting section.
   - Use `tea` for Gitea operations; do not use GitHub CLI (`gh`) in this repository workflow.
   - Create a Gitea issue before any code or documentation change.
   - Work on a feature branch `feature/issue-{N}-{short-description}` and open a PR.
   - Never commit directly to `main`.

2. **Document-first routing**
   - When performing work, consult relevant `docs/` files *before* making changes.
   - Route decisions to the documented policy whenever applicable.
   - If guidance conflicts, prefer the stricter/safety-first rule and note it in the PR.

3. **Docs with code**
   - Any code change must be accompanied by relevant documentation updates.
   - If no doc update is needed, state the reason explicitly in the PR.

4. **Session-persistent user constraints**
   - If the user requests that a behavior should persist across sessions, record it here
     (or in a dedicated policy doc) and reference it when working.
   - Keep entries short and concrete, with dates.

## Change Control

- Changes to this file follow the same workflow as code changes.
- Keep the history chronological and minimize rewording of existing entries.

## History

### 2026-02-08

- Always enforce Gitea workflow: issue -> feature branch -> PR before changes.
- When work requires guidance, consult the relevant `docs/` policies first.
- Any code change must be accompanied by relevant documentation updates.
- Persist user constraints across sessions by recording them in this document.

### 2026-02-27

- All agents must pre-read `docs/commands.md` and `docs/workflow.md` troubleshooting before running Gitea issue/PR/comment commands.
- `gh` CLI is prohibited for repository ticket/PR operations; use `tea` (or documented Gitea API fallback only).
