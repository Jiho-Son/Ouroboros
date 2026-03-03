#!/usr/bin/env bash
# Runtime verification monitor with coverage + forbidden invariant checks.

set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/home/agentson/repos/The-Ouroboros}"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/data/overnight}"
INTERVAL_SEC="${INTERVAL_SEC:-60}"
MAX_HOURS="${MAX_HOURS:-24}"
MAX_LOOPS="${MAX_LOOPS:-0}"
POLICY_TZ="${POLICY_TZ:-Asia/Seoul}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"

cd "$ROOT_DIR"

OUT_LOG="$LOG_DIR/runtime_verify_$(date +%Y%m%d_%H%M%S).log"
END_TS=$(( $(date +%s) + MAX_HOURS*3600 ))
loops=0

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" | tee -a "$OUT_LOG" >/dev/null
}

check_signal() {
  local name="$1"
  local pattern="$2"
  local run_log="$3"

  if rg -q "$pattern" "$run_log"; then
    log "[COVERAGE] ${name}=PASS pattern=${pattern}"
    return 0
  fi
  log "[COVERAGE] ${name}=NOT_OBSERVED pattern=${pattern}"
  return 1
}

find_live_pids() {
  # Detect live-mode process even when run_overnight pid files are absent.
  pgrep -af "[s]rc.main --mode=live" 2>/dev/null | awk '{print $1}' | tr '\n' ',' | sed 's/,$//'
}

check_forbidden() {
  local name="$1"
  local pattern="$2"
  local run_log="$3"

  if rg -q "$pattern" "$run_log"; then
    log "[FORBIDDEN] ${name}=HIT pattern=${pattern}"
    return 1
  fi
  log "[FORBIDDEN] ${name}=CLEAR pattern=${pattern}"
  return 0
}

is_port_listening() {
  local port="$1"

  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | grep -Eq ":${port}[[:space:]]"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | grep -Eq "[:.]${port}[[:space:]]"
    return $?
  fi
  return 1
}

log "[INFO] runtime verify monitor started interval=${INTERVAL_SEC}s max_hours=${MAX_HOURS} policy_tz=${POLICY_TZ}"

while true; do
  loops=$((loops + 1))
  now=$(date +%s)
  if [ "$now" -ge "$END_TS" ]; then
    log "[INFO] monitor completed (time window reached)"
    exit 0
  fi
  if [ "$MAX_LOOPS" -gt 0 ] && [ "$loops" -gt "$MAX_LOOPS" ]; then
    log "[INFO] monitor completed (max loops reached)"
    exit 0
  fi

  latest_run="$(ls -t "$LOG_DIR"/run_*.log 2>/dev/null | head -n1 || true)"

  # Basic liveness hints.
  app_pid="$(cat "$LOG_DIR/app.pid" 2>/dev/null || true)"
  wd_pid="$(cat "$LOG_DIR/watchdog.pid" 2>/dev/null || true)"
  live_pids="$(find_live_pids)"
  app_alive=0
  wd_alive=0
  port_alive=0
  [ -n "$app_pid" ] && kill -0 "$app_pid" 2>/dev/null && app_alive=1
  [ -n "$wd_pid" ] && kill -0 "$wd_pid" 2>/dev/null && wd_alive=1
  if [ "$app_alive" -eq 0 ] && [ -n "$live_pids" ]; then
    app_alive=1
  fi
  is_port_listening "$DASHBOARD_PORT" && port_alive=1
  log "[HEARTBEAT] run_log=${latest_run:-none} app_alive=$app_alive watchdog_alive=$wd_alive port=${DASHBOARD_PORT} alive=$port_alive live_pids=${live_pids:-none}"

  defer_log_checks=0
  if [ -z "$latest_run" ] && [ "$app_alive" -eq 1 ]; then
    defer_log_checks=1
    log "[INFO] run log not yet available; defer log-based coverage checks"
  fi

  if [ -z "$latest_run" ] && [ "$defer_log_checks" -eq 0 ]; then
    log "[ANOMALY] no run log found"
  fi

  # Coverage matrix rows (session paths and policy gate evidence).
  not_observed=0
  if [ "$app_alive" -eq 1 ]; then
    log "[COVERAGE] LIVE_MODE=PASS source=process_liveness"
  else
    if [ -n "$latest_run" ]; then
      check_signal "LIVE_MODE" "Mode: live" "$latest_run" || not_observed=$((not_observed+1))
    else
      log "[COVERAGE] LIVE_MODE=NOT_OBSERVED reason=no_run_log_no_live_pid"
      not_observed=$((not_observed+1))
    fi
  fi
  if [ "$defer_log_checks" -eq 1 ]; then
    for deferred in KR_LOOP NXT_PATH US_PRE_PATH US_DAY_PATH US_AFTER_PATH ORDER_POLICY_SESSION; do
      log "[COVERAGE] ${deferred}=DEFERRED reason=no_run_log_process_alive"
    done
  elif [ -n "$latest_run" ]; then
    check_signal "KR_LOOP" "Processing market: Korea Exchange" "$latest_run" || not_observed=$((not_observed+1))
    check_signal "NXT_PATH" "NXT_PRE|NXT_AFTER|session=NXT_" "$latest_run" || not_observed=$((not_observed+1))
    check_signal "US_PRE_PATH" "US_PRE|session=US_PRE" "$latest_run" || not_observed=$((not_observed+1))
    check_signal "US_DAY_PATH" "US_DAY|session=US_DAY|Processing market: .*NASDAQ|Processing market: .*NYSE|Processing market: .*AMEX" "$latest_run" || not_observed=$((not_observed+1))
    check_signal "US_AFTER_PATH" "US_AFTER|session=US_AFTER" "$latest_run" || not_observed=$((not_observed+1))
    check_signal "ORDER_POLICY_SESSION" "Order policy rejected .*\\[session=" "$latest_run" || not_observed=$((not_observed+1))
  else
    for missing in KR_LOOP NXT_PATH US_PRE_PATH US_DAY_PATH US_AFTER_PATH ORDER_POLICY_SESSION; do
      log "[COVERAGE] ${missing}=NOT_OBSERVED reason=no_run_log"
      not_observed=$((not_observed+1))
    done
  fi

  if [ "$not_observed" -gt 0 ]; then
    log "[ANOMALY] coverage_not_observed=$not_observed (treat as FAIL)"
  else
    log "[OK] coverage complete (NOT_OBSERVED=0)"
  fi

  # Forbidden invariants: must never happen under given policy context.
  forbidden_hits=0
  policy_dow="$(TZ="$POLICY_TZ" date +%u)" # 1..7 (Mon..Sun)
  is_weekend=0
  if [ "$policy_dow" -ge 6 ]; then
    is_weekend=1
  fi

  if [ "$defer_log_checks" -eq 1 ]; then
    log "[FORBIDDEN] WEEKEND_KR_SESSION_ACTIVE=SKIP reason=no_run_log_process_alive"
  elif [ "$is_weekend" -eq 1 ]; then
    # Weekend policy: KR regular session loop must never appear.
    if [ -n "$latest_run" ]; then
      check_forbidden "WEEKEND_KR_SESSION_ACTIVE" \
        "Market session active: KR|session=KRX_REG|Processing market: Korea Exchange" \
        "$latest_run" || forbidden_hits=$((forbidden_hits+1))
    else
      log "[FORBIDDEN] WEEKEND_KR_SESSION_ACTIVE=SKIP reason=no_run_log"
    fi
  else
    log "[FORBIDDEN] WEEKEND_KR_SESSION_ACTIVE=SKIP reason=weekday"
  fi

  if [ "$forbidden_hits" -gt 0 ]; then
    log "[P0] forbidden_invariant_hits=$forbidden_hits (treat as immediate FAIL)"
  else
    log "[OK] forbidden invariants clear"
  fi

  sleep "$INTERVAL_SEC"
done
