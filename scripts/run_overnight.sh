#!/usr/bin/env bash
# Start The Ouroboros overnight with logs and watchdog.

set -euo pipefail

LOG_DIR="${LOG_DIR:-data/overnight}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"
TMUX_AUTO="${TMUX_AUTO:-true}"
TMUX_ATTACH="${TMUX_ATTACH:-true}"
TMUX_SESSION_PREFIX="${TMUX_SESSION_PREFIX:-ouroboros_overnight}"
STARTUP_GRACE_SEC="${STARTUP_GRACE_SEC:-3}"
dashboard_port="${DASHBOARD_PORT:-8080}"
APP_CMD_BIN="${APP_CMD_BIN:-}"
APP_CMD_ARGS="${APP_CMD_ARGS:-}"
RUNS_DASHBOARD="false"

# Custom override contract:
# 1) Preferred: APP_CMD_BIN + APP_CMD_ARGS
#    - APP_CMD_BIN is treated as a single executable token.
#    - APP_CMD_ARGS uses shell-style word splitting; quote/escape inside this
#      variable is NOT preserved as a nested shell parse.
# 2) Legacy fallback: APP_CMD (raw shell command string)
#    - This path remains for backward compatibility.
#    - When APP_CMD includes --dashboard, caller should include explicit
#      DASHBOARD_PORT assignment in APP_CMD if non-default port is required.

if [ -n "$APP_CMD_BIN" ]; then
    USE_DEFAULT_APP_CMD="false"
    USE_SAFE_CUSTOM_APP_CMD="true"
    APP_CMD="${APP_CMD_BIN} ${APP_CMD_ARGS}"
    if [[ " $APP_CMD_ARGS " == *" --dashboard "* ]]; then
        RUNS_DASHBOARD="true"
    fi
elif [ -z "${APP_CMD:-}" ]; then
    USE_DEFAULT_APP_CMD="true"
    USE_SAFE_CUSTOM_APP_CMD="false"
    if [ -x ".venv/bin/python" ]; then
        PYTHON_BIN=".venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        echo ".venv/bin/python 또는 python3/python 실행 파일을 찾을 수 없습니다."
        exit 1
    fi

    APP_CMD="$PYTHON_BIN -m src.main --mode=live --dashboard"
    RUNS_DASHBOARD="true"
else
    USE_DEFAULT_APP_CMD="false"
    USE_SAFE_CUSTOM_APP_CMD="false"
    if [[ "$APP_CMD" == *"--dashboard"* ]]; then
        RUNS_DASHBOARD="true"
    fi
fi

mkdir -p "$LOG_DIR"

timestamp="$(date +"%Y%m%d_%H%M%S")"
RUN_LOG="$LOG_DIR/run_${timestamp}.log"
WATCHDOG_LOG="$LOG_DIR/watchdog_${timestamp}.log"
PID_FILE="$LOG_DIR/app.pid"
WATCHDOG_PID_FILE="$LOG_DIR/watchdog.pid"

is_port_in_use() {
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
    # No supported socket inspection command found.
    return 1
}

if [ -f "$PID_FILE" ]; then
    old_pid="$(cat "$PID_FILE" || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "앱이 이미 실행 중입니다. pid=$old_pid"
        exit 1
    fi
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] starting: $APP_CMD" | tee -a "$RUN_LOG"
if [ "$RUNS_DASHBOARD" = "true" ] && is_port_in_use "$dashboard_port"; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] startup failed: dashboard port ${dashboard_port} already in use" | tee -a "$RUN_LOG"
    exit 1
fi

if [ "$USE_DEFAULT_APP_CMD" = "true" ]; then
    # Default path avoids shell word-splitting on executable paths.
    nohup env DASHBOARD_PORT="$dashboard_port" "$PYTHON_BIN" -m src.main --mode=live --dashboard >>"$RUN_LOG" 2>&1 &
elif [ "$USE_SAFE_CUSTOM_APP_CMD" = "true" ]; then
    # Safer custom path: executable path is handled as a single token.
    if [ -n "$APP_CMD_ARGS" ]; then
        # shellcheck disable=SC2206
        app_args=( $APP_CMD_ARGS )
        nohup env DASHBOARD_PORT="$dashboard_port" "$APP_CMD_BIN" "${app_args[@]}" >>"$RUN_LOG" 2>&1 &
    else
        nohup env DASHBOARD_PORT="$dashboard_port" "$APP_CMD_BIN" >>"$RUN_LOG" 2>&1 &
    fi
else
    # Custom APP_CMD is treated as a shell command string.
    # If executable paths include spaces, they must be quoted inside APP_CMD.
    # Legacy compatibility path: caller owns quoting and env var injection.
    nohup bash -lc "exec env $APP_CMD" >>"$RUN_LOG" 2>&1 &
fi
app_pid=$!
echo "$app_pid" > "$PID_FILE"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] app pid=$app_pid" | tee -a "$RUN_LOG"

nohup env PID_FILE="$PID_FILE" LOG_FILE="$WATCHDOG_LOG" CHECK_INTERVAL="$CHECK_INTERVAL" \
    bash scripts/watchdog.sh >/dev/null 2>&1 &
watchdog_pid=$!
echo "$watchdog_pid" > "$WATCHDOG_PID_FILE"

sleep "$STARTUP_GRACE_SEC"
if ! kill -0 "$app_pid" 2>/dev/null; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] startup failed: app process exited early (pid=$app_pid)" | tee -a "$RUN_LOG"
    [ -n "${watchdog_pid:-}" ] && kill "$watchdog_pid" 2>/dev/null || true
    tail -n 20 "$RUN_LOG" || true
    exit 1
fi
if ! kill -0 "$watchdog_pid" 2>/dev/null; then
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] startup failed: watchdog exited early (pid=$watchdog_pid)" | tee -a "$WATCHDOG_LOG"
    kill "$app_pid" 2>/dev/null || true
    tail -n 20 "$WATCHDOG_LOG" || true
    exit 1
fi

cat <<EOF
시작 완료
- app pid: $app_pid
- watchdog pid: $watchdog_pid
- app log: $RUN_LOG
- watchdog log: $WATCHDOG_LOG

실시간 확인:
tail -f "$RUN_LOG"
tail -f "$WATCHDOG_LOG"
EOF

if [ "$TMUX_AUTO" = "true" ]; then
    if ! command -v tmux >/dev/null 2>&1; then
        echo "tmux를 찾지 못해 자동 세션 생성은 건너뜁니다."
        exit 0
    fi

    session_name="${TMUX_SESSION_PREFIX}_${timestamp}"
    window_name="overnight"
    tmux new-session -d -s "$session_name" -n "$window_name" "tail -f '$RUN_LOG'"
    tmux split-window -t "${session_name}:${window_name}" -v "tail -f '$WATCHDOG_LOG'"
    tmux select-layout -t "${session_name}:${window_name}" even-vertical

    echo "tmux session 생성: $session_name"
    echo "수동 접속: tmux attach -t $session_name"

    if [ -z "${TMUX:-}" ] && [ "$TMUX_ATTACH" = "true" ]; then
        tmux attach -t "$session_name"
    fi
fi
