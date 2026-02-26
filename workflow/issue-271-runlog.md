# Issue #271 Workflow Run Log

## 2026-02-26

### Step 1: Gitea issue creation
- Attempt 1: Succeeded, but formatting degraded
  - Command style: `tea issues create -t ... -d "...\n..."`
  - Symptom: Issue body rendered literal `\n` text in web UI instead of line breaks
- Root cause
  - `tea` does not provide `--description-file`
  - Shell-escaped `\n` inside double quotes is passed as backslash+n text
- Resolution
  - Build body with heredoc and pass as variable (`-d "$ISSUE_BODY"`)

### Step 2: PR description creation
- Attempt 1: Succeeded, but same newline rendering risk detected
- Resolution
  - Same heredoc variable pattern applied for PR body (`--description "$PR_BODY"`)

### Preventive Action
- `docs/workflow.md` updated with "Gitea CLI Formatting Troubleshooting" section
- Standard command templates added for issues and PRs

### Reusable Safe Template
```bash
ISSUE_BODY=$(cat <<'EOF'
## Summary
- item A
- item B

## Scope
- docs only
EOF
)

tea issues create -t "title" -d "$ISSUE_BODY"
```
