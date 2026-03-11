---
name: push
description:
  Push current branch changes to origin and create or update the corresponding
  pull request; use when asked to push, publish updates, or create pull request.
---

# Push

## Prerequisites

- `GH_TOKEN` is exported for GitHub operations in this repo.
- `python3 scripts/github_pr.py current` succeeds once a PR exists for the branch.

## Goals

- Push current branch changes to `origin` safely.
- Create a PR if none exists for the branch, otherwise update the existing PR.
- Keep branch history clean when remote has moved.

## Related Skills

- `pull`: use this when push is rejected or sync is not clean (non-fast-forward,
  merge conflict risk, or stale branch).

## Steps

1. Identify current branch and confirm remote state.
2. Run local validation for the touched surface before pushing.
3. Push branch to `origin` with upstream tracking if needed, using whatever
   remote URL is already configured.
4. If push is not clean/rejected:
   - If the failure is a non-fast-forward or sync problem, run the `pull`
     skill to merge `origin/main`, resolve conflicts, and rerun validation.
   - Push again; use `--force-with-lease` only when history was rewritten.
   - If the failure is due to auth, permissions, or workflow restrictions on
     the configured remote, stop and surface the exact error instead of
     rewriting remotes or switching protocols as a workaround.

5. Ensure a PR exists for the branch:
   - If no PR exists, create one.
   - If a PR exists and is open, update it.
   - If branch is tied to a closed/merged PR, create a new branch + PR.
   - Write a proper PR title that clearly describes the change outcome
   - For branch updates, explicitly reconsider whether current PR title still
     matches the latest scope; update it if it no longer does.
6. Write/update PR body explicitly in `/tmp/pr_body.md`:
   - Include concrete `## Summary`, `## Why`, and `## Validation` sections for this change.
   - Keep REQ/TASK/TEST traceability IDs in narrative text so `scripts/validate_pr_body.py` can see them.
   - If PR already exists, refresh body content so it reflects the total PR
     scope (all intended work on the branch), not just the newest commits,
     including newly added work, removed work, or changed approach.
   - Do not reuse stale description text from earlier iterations.
7. Validate PR body with `python3 scripts/validate_pr_body.py --body-file /tmp/pr_body.md` and fix all reported issues.
8. Reply with the PR URL from `python3 scripts/github_pr.py field --field html_url`.

## Commands

```sh
# Identify branch
branch=$(git branch --show-current)

# Minimal validation gate
pytest -v --cov=src --cov-report=term-missing
ruff check src/ tests/

# Initial push: respect the current origin remote.
git push -u origin HEAD

# If that failed because the remote moved, use the pull skill. After
# pull-skill resolution and re-validation, retry the normal push:
git push -u origin HEAD

# If the configured remote rejects the push for auth, permissions, or workflow
# restrictions, stop and surface the exact error.

# Only if history was rewritten locally:
git push --force-with-lease origin HEAD

# Ensure a PR exists (create only if missing)
pr_state=$(python3 scripts/github_pr.py field --field state 2>/dev/null || true)
if [ "$pr_state" = "closed" ]; then
  echo "Current branch is tied to a closed PR; create a new branch + PR." >&2
  exit 1
fi

# Write a clear, human-friendly title that summarizes the shipped change.
pr_title="<clear PR title written for this change>"
if [ -z "$pr_state" ]; then
  python3 scripts/github_pr.py create --title "$pr_title" --body-file /tmp/pr_body.md --base main
else
  # Reconsider title on every branch update; edit if scope shifted.
  pr_number=$(python3 scripts/github_pr.py field --field number)
  python3 scripts/github_pr.py edit --pr "$pr_number" --title "$pr_title" --body-file /tmp/pr_body.md
fi

# Write/edit PR body in /tmp/pr_body.md before validation.
# Example workflow:
# 1) draft body content for this PR in /tmp/pr_body.md
# 2) python3 scripts/validate_pr_body.py --body-file /tmp/pr_body.md
# 3) python3 scripts/github_pr.py edit --pr <number> --body-file /tmp/pr_body.md
# 4) python3 scripts/validate_pr_body.py --pr <number>

# Show PR URL for the reply
python3 scripts/github_pr.py field --field html_url
```

## Notes

- Do not use `--force`; only use `--force-with-lease` as the last resort.
- Distinguish sync problems from remote auth/permission problems:
  - Use the `pull` skill for non-fast-forward or stale-branch issues.
  - Surface auth, permissions, or workflow restrictions directly instead of
    changing remotes or protocols.
