#!/usr/bin/env bash
# Backtest gate for PR/push/scheduled verification.

set -euo pipefail

MODE="${BACKTEST_MODE:-auto}"          # auto | smoke | full
BASE_REF="${BASE_REF:-origin/main}"    # used when MODE=auto
FORCE_FULL="${FORCE_FULL_BACKTEST:-false}"
LOG_DIR="${LOG_DIR:-data/backtest-gate}"

mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/backtest_gate_${STAMP}.log"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" | tee -a "$LOG_FILE"
}

run_cmd() {
  log "[RUN] $*"
  "$@" 2>&1 | tee -a "$LOG_FILE"
}

resolve_mode_from_changes() {
  if [ "$FORCE_FULL" = "true" ]; then
    echo "full"
    return
  fi

  if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    log "[WARN] BASE_REF not found: $BASE_REF; fallback to full"
    echo "full"
    return
  fi

  changed_files="$(git diff --name-only "$BASE_REF"...HEAD || true)"
  if [ -z "$changed_files" ]; then
    log "[INFO] no changed files between $BASE_REF...HEAD; skip backtest gate"
    echo "skip"
    return
  fi

  log "[INFO] changed files from $BASE_REF...HEAD:"
  while IFS= read -r line; do
    [ -n "$line" ] && log "  - $line"
  done <<< "$changed_files"

  # Backtest-sensitive areas: analysis/strategy/runtime execution semantics.
  if printf '%s\n' "$changed_files" | rg -q \
    '^(src/analysis/|src/strategy/|src/strategies/|src/main.py|src/markets/|src/broker/|tests/test_backtest_|tests/test_triple_barrier.py|tests/test_walk_forward_split.py|tests/test_main.py|docs/ouroboros/)'
  then
    echo "full"
  else
    echo "skip"
  fi
}

SMOKE_TESTS=(
  tests/test_backtest_pipeline_integration.py
  tests/test_triple_barrier.py
  tests/test_walk_forward_split.py
  tests/test_backtest_cost_guard.py
  tests/test_backtest_execution_model.py
)

FULL_TESTS=(
  tests/test_backtest_pipeline_integration.py
  tests/test_triple_barrier.py
  tests/test_walk_forward_split.py
  tests/test_backtest_cost_guard.py
  tests/test_backtest_execution_model.py
  tests/test_main.py
)

main() {
  log "[INFO] backtest gate started mode=$MODE base_ref=$BASE_REF force_full=$FORCE_FULL"

  selected_mode="$MODE"
  if [ "$MODE" = "auto" ]; then
    selected_mode="$(resolve_mode_from_changes)"
  fi

  case "$selected_mode" in
    skip)
      log "[PASS] backtest gate skipped (no backtest-sensitive changes)"
      exit 0
      ;;
    smoke)
      run_cmd python3 -m pytest -q "${SMOKE_TESTS[@]}"
      log "[PASS] smoke backtest gate passed"
      ;;
    full)
      run_cmd python3 -m pytest -q "${SMOKE_TESTS[@]}"
      # Runtime semantics tied to v2 staged-exit must remain covered in full gate.
      run_cmd python3 -m pytest -q tests/test_main.py -k \
        "staged_exit_override or runtime_exit_cache_cleared or run_daily_session_applies_staged_exit_override_on_hold"
      log "[PASS] full backtest gate passed"
      ;;
    *)
      log "[FAIL] invalid BACKTEST_MODE=$selected_mode (expected auto|smoke|full)"
      exit 2
      ;;
  esac
}

main "$@"
