#!/usr/bin/env bash
# Mirror the latest successful scheduled Backtest Gate artifact into local logs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime_instance_env.sh
source "$SCRIPT_DIR/runtime_instance_env.sh"
runtime_resolve_defaults
cd "$ROOT_DIR"

GH_BIN="${BACKTEST_GATE_GH_BIN:-gh}"
WORKFLOW_NAME="${BACKTEST_GATE_WORKFLOW:-backtest-gate.yml}"
WORKFLOW_BRANCH="${BACKTEST_GATE_BRANCH:-main}"
WORKFLOW_EVENT="${BACKTEST_GATE_EVENT:-schedule}"
ARTIFACT_NAME="${BACKTEST_GATE_ARTIFACT_NAME:-backtest-gate-logs}"
TARGET_LOG_DIR="${BACKTEST_GATE_LOG_DIR:-$ROOT_DIR/data/backtest-gate}"
MARKER_FILE="${BACKTEST_GATE_SYNC_MARKER_FILE:-$TARGET_LOG_DIR/.latest_backtest_gate_run}"

require_gh() {
  if [ -x "$GH_BIN" ]; then
    return 0
  fi
  command -v "$GH_BIN" >/dev/null 2>&1
}

latest_run_id() {
  "$GH_BIN" run list \
    --workflow "$WORKFLOW_NAME" \
    --branch "$WORKFLOW_BRANCH" \
    --event "$WORKFLOW_EVENT" \
    --limit 1 \
    --json databaseId,status,conclusion |
    python3 -c '
import json
import sys

runs = json.load(sys.stdin)
if not runs:
    sys.exit(0)
run = runs[0]
if run.get("status") != "completed" or run.get("conclusion") != "success":
    sys.exit(0)
print(run["databaseId"])
'
}

has_any_local_log() {
  local first_log=""

  if [ -d "$TARGET_LOG_DIR" ]; then
    first_log="$(find "$TARGET_LOG_DIR" -maxdepth 1 -type f -name 'backtest_gate_*.log' -print -quit 2>/dev/null)"
  fi

  [ -n "$first_log" ]
}

sync_latest_artifact() {
  local run_id="$1"
  local tmp_dir
  local count

  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"; trap - RETURN ERR' RETURN ERR
  "$GH_BIN" run download "$run_id" -n "$ARTIFACT_NAME" -D "$tmp_dir" >/dev/null

  mkdir -p "$TARGET_LOG_DIR"
  count=0
  while IFS= read -r -d '' file_path; do
    cp "$file_path" "$TARGET_LOG_DIR/$(basename "$file_path")"
    count=$((count + 1))
  done < <(find "$tmp_dir" -type f -name 'backtest_gate_*.log' -print0 | sort -z)

  if [ "$count" -eq 0 ]; then
    echo "artifact_empty run_id=$run_id" >&2
    return 1
  fi

  printf '%s\n' "$run_id" > "$MARKER_FILE"
  echo "synced run_id=$run_id files=$count"
}

main() {
  local run_id
  local marker_run_id

  if ! require_gh; then
    echo "gh_unavailable bin=$GH_BIN" >&2
    exit 1
  fi

  run_id="$(latest_run_id)"
  if [ -z "$run_id" ]; then
    echo "no_scheduled_run"
    exit 0
  fi

  marker_run_id="$(cat "$MARKER_FILE" 2>/dev/null || true)"
  if [ "$marker_run_id" = "$run_id" ] && has_any_local_log; then
    echo "already_synced run_id=$run_id"
    exit 0
  fi

  sync_latest_artifact "$run_id"
}

main "$@"
