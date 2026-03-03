#!/usr/bin/env bash
# Safe helper for posting multiline Gitea comments without escaped-newline artifacts.

set -euo pipefail

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] || [ "$#" -lt 2 ]; then
  cat <<'EOF'
Usage:
  scripts/tea_comment.sh <issue_or_pr_index> <body_file|-> [repo]

Examples:
  scripts/tea_comment.sh 374 /tmp/comment.md
  cat /tmp/comment.md | scripts/tea_comment.sh 374 - jihoson/The-Ouroboros

Notes:
  - Use file/stdin input to preserve real newlines.
  - Passing inline strings with "\n" is intentionally avoided by this helper.
EOF
  exit 1
fi

INDEX="$1"
BODY_SOURCE="$2"
REPO="${3:-jihoson/The-Ouroboros}"

if [ "$BODY_SOURCE" = "-" ]; then
  BODY="$(cat)"
else
  if [ ! -f "$BODY_SOURCE" ]; then
    echo "[FAIL] body file not found: $BODY_SOURCE" >&2
    exit 1
  fi
  BODY="$(cat "$BODY_SOURCE")"
fi

if [ -z "$BODY" ]; then
  echo "[FAIL] empty comment body" >&2
  exit 1
fi

# Guard against the common escaped-newline mistake.
if [[ "$BODY" == *"\\n"* ]] && [[ "$BODY" != *$'\n'* ]]; then
  echo "[FAIL] body appears to contain escaped newlines (\\n) instead of real line breaks" >&2
  echo "Use a multiline file/heredoc and pass that file to scripts/tea_comment.sh" >&2
  exit 1
fi

YES="" ~/bin/tea comment "$INDEX" --repo "$REPO" "$BODY"

