#!/usr/bin/env bash
# Morning summary for overnight run logs.

set -euo pipefail

LOG_DIR="${LOG_DIR:-data/overnight}"

if [ ! -d "$LOG_DIR" ]; then
    echo "로그 디렉터리가 없습니다: $LOG_DIR"
    exit 1
fi

latest_run="$(ls -1t "$LOG_DIR"/run_*.log 2>/dev/null | head -n 1 || true)"
latest_watchdog="$(ls -1t "$LOG_DIR"/watchdog_*.log 2>/dev/null | head -n 1 || true)"

if [ -z "$latest_run" ]; then
    echo "run 로그가 없습니다: $LOG_DIR/run_*.log"
    exit 1
fi

echo "Overnight report"
echo "- run log: $latest_run"
if [ -n "$latest_watchdog" ]; then
    echo "- watchdog log: $latest_watchdog"
fi

start_line="$(head -n 1 "$latest_run" || true)"
end_line="$(tail -n 1 "$latest_run" || true)"

info_count="$(rg -c '"level": "INFO"' "$latest_run" || true)"
warn_count="$(rg -c '"level": "WARNING"' "$latest_run" || true)"
error_count="$(rg -c '"level": "ERROR"' "$latest_run" || true)"
critical_count="$(rg -c '"level": "CRITICAL"' "$latest_run" || true)"
traceback_count="$(rg -c 'Traceback' "$latest_run" || true)"

echo "- start: ${start_line:-N/A}"
echo "- end:   ${end_line:-N/A}"
echo "- INFO: ${info_count:-0}"
echo "- WARNING: ${warn_count:-0}"
echo "- ERROR: ${error_count:-0}"
echo "- CRITICAL: ${critical_count:-0}"
echo "- Traceback: ${traceback_count:-0}"

if [ -n "$latest_watchdog" ]; then
    watchdog_errors="$(rg -c '\[ERROR\]' "$latest_watchdog" || true)"
    echo "- watchdog ERROR: ${watchdog_errors:-0}"
    echo ""
    echo "최근 watchdog 로그:"
    tail -n 5 "$latest_watchdog" || true
fi

echo ""
echo "최근 앱 로그:"
tail -n 20 "$latest_run" || true
