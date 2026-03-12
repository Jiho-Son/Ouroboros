#!/usr/bin/env bash
# Simple watchdog for The Ouroboros process.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime_instance_env.sh
source "$SCRIPT_DIR/runtime_instance_env.sh"
runtime_resolve_defaults
cd "$ROOT_DIR"

PID_FILE="${PID_FILE:-$LOG_DIR/app.pid}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/watchdog.log}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"
STATUS_EVERY="${STATUS_EVERY:-10}"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    printf '%s %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$1" | tee -a "$LOG_FILE"
}

if [ ! -f "$PID_FILE" ]; then
    log "[ERROR] pid file not found: $PID_FILE"
    exit 1
fi

PID="$(cat "$PID_FILE")"
if [ -z "$PID" ]; then
    log "[ERROR] pid file is empty: $PID_FILE"
    exit 1
fi

log "[INFO] watchdog started (pid=$PID, interval=${CHECK_INTERVAL}s)"

count=0
while true; do
    if kill -0 "$PID" 2>/dev/null; then
        count=$((count + 1))
        if [ $((count % STATUS_EVERY)) -eq 0 ]; then
            log "[INFO] process alive (pid=$PID)"
        fi
    else
        log "[ERROR] process stopped (pid=$PID)"
        exit 1
    fi
    sleep "$CHECK_INTERVAL"
done
