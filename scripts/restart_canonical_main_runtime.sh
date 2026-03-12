#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime_instance_env.sh
source "$SCRIPT_DIR/runtime_instance_env.sh"
runtime_resolve_defaults

TARGET_SHA=""
DRY_RUN="false"

usage() {
    cat <<'EOF'
Usage: bash scripts/restart_canonical_main_runtime.sh --target-sha <sha> [--dry-run]
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --target-sha)
            if [ "$#" -lt 2 ]; then
                echo "missing value for --target-sha" >&2
                usage >&2
                exit 1
            fi
            TARGET_SHA="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$TARGET_SHA" ]; then
    echo "--target-sha is required" >&2
    usage >&2
    exit 1
fi

if [ "${RUNTIME_BRANCH_NAME_RESOLVED:-}" != "main" ]; then
    echo "canonical restart only runs from the main checkout (resolved branch=${RUNTIME_BRANCH_NAME_RESOLVED:-unknown})" >&2
    exit 1
fi

RESTART_MARKER_FILE="${CANONICAL_RESTART_MARKER_FILE:-$LOG_DIR/canonical_restart.last_sha}"
RESTART_LOG_FILE="${CANONICAL_RESTART_LOG_FILE:-$LOG_DIR/canonical_restart.log}"
RESTART_LOCK_FILE="${CANONICAL_RESTART_LOCK_FILE:-$LOG_DIR/canonical_restart.lock}"
STOP_CMD="${CANONICAL_RESTART_STOP_CMD:-bash \"$SCRIPT_DIR/stop_overnight.sh\"}"
START_CMD="${CANONICAL_RESTART_START_CMD:-bash \"$SCRIPT_DIR/run_overnight.sh\"}"

announce() {
    local message="$1"
    printf '%s\n' "$message"
}

log() {
    local message="$1"
    local timestamp
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s %s\n' "$timestamp" "$message" | tee -a "$RESTART_LOG_FILE" >/dev/null
}

if [ "$DRY_RUN" = "true" ]; then
    announce "dry-run: target_sha=$TARGET_SHA"
    announce "dry-run: log_dir=$LOG_DIR"
    announce "dry-run: marker_file=$RESTART_MARKER_FILE"
    announce "dry-run: stop_cmd=$STOP_CMD"
    announce "dry-run: start_cmd=$START_CMD"
    exit 0
fi

mkdir -p "$LOG_DIR"

if command -v flock >/dev/null 2>&1; then
    exec 9>"$RESTART_LOCK_FILE"
    flock 9
fi

if [ -f "$RESTART_MARKER_FILE" ]; then
    last_sha="$(cat "$RESTART_MARKER_FILE" || true)"
    if [ "$last_sha" = "$TARGET_SHA" ]; then
        log "[SKIP] target_sha=$TARGET_SHA already processed"
        announce "target_sha=$TARGET_SHA already processed"
        exit 0
    fi
fi

log "[START] target_sha=$TARGET_SHA"
bash -lc "$STOP_CMD"
bash -lc "$START_CMD"
printf '%s\n' "$TARGET_SHA" > "$RESTART_MARKER_FILE"
log "[DONE] target_sha=$TARGET_SHA marker=$RESTART_MARKER_FILE"
announce "canonical restart completed for $TARGET_SHA"
