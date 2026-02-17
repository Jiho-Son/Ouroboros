#!/usr/bin/env bash
# Stop The Ouroboros overnight app/watchdog/tmux session.

set -euo pipefail

LOG_DIR="${LOG_DIR:-data/overnight}"
PID_FILE="$LOG_DIR/app.pid"
WATCHDOG_PID_FILE="$LOG_DIR/watchdog.pid"
TMUX_SESSION_PREFIX="${TMUX_SESSION_PREFIX:-ouroboros_overnight}"
KILL_TIMEOUT="${KILL_TIMEOUT:-5}"

stop_pid() {
    local name="$1"
    local pid="$2"

    if [ -z "$pid" ]; then
        echo "$name PID가 비어 있습니다."
        return 1
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        echo "$name 프로세스가 이미 종료됨 (pid=$pid)"
        return 0
    fi

    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 "$KILL_TIMEOUT"); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "$name 종료됨 (pid=$pid)"
            return 0
        fi
        sleep 1
    done

    kill -9 "$pid" 2>/dev/null || true
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "$name 강제 종료됨 (pid=$pid)"
        return 0
    fi

    echo "$name 종료 실패 (pid=$pid)"
    return 1
}

status=0

if [ -f "$WATCHDOG_PID_FILE" ]; then
    watchdog_pid="$(cat "$WATCHDOG_PID_FILE" || true)"
    stop_pid "watchdog" "$watchdog_pid" || status=1
    rm -f "$WATCHDOG_PID_FILE"
else
    echo "watchdog pid 파일 없음: $WATCHDOG_PID_FILE"
fi

if [ -f "$PID_FILE" ]; then
    app_pid="$(cat "$PID_FILE" || true)"
    stop_pid "app" "$app_pid" || status=1
    rm -f "$PID_FILE"
else
    echo "app pid 파일 없음: $PID_FILE"
fi

if command -v tmux >/dev/null 2>&1; then
    sessions="$(tmux ls 2>/dev/null | awk -F: -v p="$TMUX_SESSION_PREFIX" '$1 ~ "^" p "_" {print $1}')"
    if [ -n "$sessions" ]; then
        while IFS= read -r s; do
            [ -z "$s" ] && continue
            tmux kill-session -t "$s" 2>/dev/null || true
            echo "tmux 세션 종료: $s"
        done <<< "$sessions"
    else
        echo "종료할 tmux 세션 없음 (prefix=${TMUX_SESSION_PREFIX}_)"
    fi
fi

exit "$status"
