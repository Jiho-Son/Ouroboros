#!/usr/bin/env bash

set -euo pipefail

GIT_BIN="${CANONICAL_RESTART_GIT_BIN:-git}"
GH_BIN="${CANONICAL_RESTART_GH_BIN:-gh}"
CANONICAL_ROOT=""
WORKSPACE_SHA=""
DRY_RUN="false"

usage() {
    cat <<'USAGE'
Usage: bash scripts/symphony_before_remove_canonical_restart.sh [--canonical-root <path>] [--workspace-sha <sha>] [--dry-run]
USAGE
}

run_git() {
    "$GIT_BIN" "$@"
}

run_gh() {
    "$GH_BIN" "$@"
}

announce() {
    printf '%s\n' "$1"
}

discover_canonical_root() {
    local worktree_path=""
    local line=""

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            worktree\ *)
                worktree_path="${line#worktree }"
                ;;
            "")
                worktree_path=""
                ;;
            branch\ refs/heads/main)
                if [ -n "$worktree_path" ]; then
                    printf '%s\n' "$worktree_path"
                    return 0
                fi
                ;;
        esac
    done < <(run_git worktree list --porcelain)

    return 1
}

resolve_state_root() {
    local canonical_root="$1"
    local state_root="${OVERNIGHT_STATE_ROOT:-$canonical_root/data/overnight}"

    if [[ "$state_root" != /* ]]; then
        state_root="$canonical_root/$state_root"
    fi

    printf '%s\n' "$state_root"
}

github_repo_slug_from_remote() {
    local remote_url="$1"
    local slug=""

    case "$remote_url" in
        https://github.com/*)
            slug="${remote_url#https://github.com/}"
            ;;
        ssh://git@github.com/*)
            slug="${remote_url#ssh://git@github.com/}"
            ;;
        git@github.com:*)
            slug="${remote_url#git@github.com:}"
            ;;
        *)
            return 1
            ;;
    esac

    slug="${slug%.git}"
    if [[ "$slug" != */* ]]; then
        return 1
    fi

    printf '%s\n' "$slug"
}

github_confirms_merge() {
    local repo_slug="$1"
    local workspace_branch="$2"
    local workspace_sha="$3"
    local repo_owner=""
    local response=""

    if [ -z "$repo_slug" ] || ! command -v python3 >/dev/null 2>&1; then
        return 1
    fi

    repo_owner="${repo_slug%%/*}"
    if [ -z "$repo_owner" ]; then
        return 1
    fi

    if ! response="$(
        run_gh api -X GET \
            "repos/$repo_slug/pulls?state=closed&base=main&head=$repo_owner:$workspace_branch&per_page=20" \
            2>/dev/null
    )"; then
        return 1
    fi

    RESPONSE="$response" python3 -c '
import json
import os
import sys

branch, sha = sys.argv[1:]

try:
    payload = json.loads(os.environ["RESPONSE"])
except (KeyError, json.JSONDecodeError):
    raise SystemExit(1)

if not isinstance(payload, list):
    raise SystemExit(1)

for pr in payload:
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    # Limitation: head.sha can drift if new commits are pushed after squash merge.
    if (
        pr.get("merged_at")
        and base.get("ref") == "main"
        and head.get("ref") == branch
        and head.get("sha") == sha
    ):
        raise SystemExit(0)

raise SystemExit(1)
' "$workspace_branch" "$workspace_sha"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --canonical-root)
            if [ "$#" -lt 2 ]; then
                echo "missing value for --canonical-root" >&2
                usage >&2
                exit 1
            fi
            CANONICAL_ROOT="$2"
            shift 2
            ;;
        --workspace-sha)
            if [ "$#" -lt 2 ]; then
                echo "missing value for --workspace-sha" >&2
                usage >&2
                exit 1
            fi
            WORKSPACE_SHA="$2"
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

workspace_branch="$(run_git branch --show-current)"
if [ -z "$WORKSPACE_SHA" ]; then
    WORKSPACE_SHA="$(run_git rev-parse HEAD)"
fi

if [ "$workspace_branch" = "main" ]; then
    announce "skip: before_remove invoked from a main worktree; canonical runtime left unchanged"
    exit 0
fi

if [ -z "$CANONICAL_ROOT" ]; then
    CANONICAL_ROOT="$(discover_canonical_root || true)"
fi

if [ -z "$CANONICAL_ROOT" ]; then
    echo "unable to discover canonical main worktree from git worktree metadata" >&2
    exit 1
fi

STATE_ROOT="$(resolve_state_root "$CANONICAL_ROOT")"
MARKER_FILE="${CANONICAL_RESTART_MARKER_FILE:-$STATE_ROOT/canonical_restart.last_sha}"
LOG_FILE="${CANONICAL_RESTART_LOG_FILE:-$STATE_ROOT/canonical_restart.log}"
LOCK_FILE="${CANONICAL_RESTART_LOCK_FILE:-$STATE_ROOT/canonical_restart.lock}"
LOCK_DIR="${LOCK_FILE}.d"
STOP_CMD="${CANONICAL_RESTART_STOP_CMD:-env RUNTIME_REPO_ROOT=\"$CANONICAL_ROOT\" RUNTIME_BRANCH_NAME=main bash \"$CANONICAL_ROOT/scripts/stop_overnight.sh\"}"
START_CMD="${CANONICAL_RESTART_START_CMD:-env RUNTIME_REPO_ROOT=\"$CANONICAL_ROOT\" RUNTIME_BRANCH_NAME=main TMUX_ATTACH=false bash \"$CANONICAL_ROOT/scripts/run_overnight.sh\"}"

log() {
    local timestamp=""
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s %s\n' "$timestamp" "$1" >> "$LOG_FILE"
}

if [ "$DRY_RUN" != "true" ]; then
    mkdir -p "$STATE_ROOT"
    log "[INFO] hook invoked cwd=$(pwd) workspace_branch=$workspace_branch workspace_sha=$WORKSPACE_SHA canonical_root=$CANONICAL_ROOT"
fi

canonical_branch="$(run_git -C "$CANONICAL_ROOT" branch --show-current)"
if [ "$canonical_branch" != "main" ]; then
    if [ "$DRY_RUN" != "true" ]; then
        log "[ERROR] canonical restart requires main checkout resolved_branch=$canonical_branch root=$CANONICAL_ROOT"
    fi
    echo "canonical restart requires a main checkout (resolved branch=$canonical_branch, root=$CANONICAL_ROOT)" >&2
    exit 1
fi

REMOTE_URL="$(run_git -C "$CANONICAL_ROOT" remote get-url origin 2>/dev/null || true)"
REPO_SLUG="$(github_repo_slug_from_remote "$REMOTE_URL" || true)"

if [ "$DRY_RUN" = "true" ]; then
    announce "dry-run: workspace_branch=$workspace_branch"
    announce "dry-run: workspace_sha=$WORKSPACE_SHA"
    announce "dry-run: canonical_root=$CANONICAL_ROOT"
    announce "dry-run: canonical_branch=$canonical_branch"
    announce "dry-run: marker_file=$MARKER_FILE"
    announce "dry-run: stop_cmd=$STOP_CMD"
    announce "dry-run: start_cmd=$START_CMD"
    exit 0
fi

fetch_output=""
if ! fetch_output="$(run_git -C "$CANONICAL_ROOT" fetch origin 2>&1)"; then
    log "[ERROR] fetch origin failed canonical_root=$CANONICAL_ROOT detail=$fetch_output"
    echo "canonical fetch failed for root=$CANONICAL_ROOT: $fetch_output" >&2
    exit 1
fi

TARGET_SHA="$(run_git -C "$CANONICAL_ROOT" rev-parse origin/main)"

if ! run_git -C "$CANONICAL_ROOT" merge-base --is-ancestor "$WORKSPACE_SHA" "$TARGET_SHA"; then
    if github_confirms_merge "$REPO_SLUG" "$workspace_branch" "$WORKSPACE_SHA"; then
        log "[INFO] github merge fallback matched workspace_branch=$workspace_branch workspace_sha=$WORKSPACE_SHA target_sha=$TARGET_SHA canonical_root=$CANONICAL_ROOT"
        announce "github merge fallback matched workspace_branch=$workspace_branch sha=$WORKSPACE_SHA target_sha=$TARGET_SHA canonical_root=$CANONICAL_ROOT"
    else
        log "[SKIP] workspace_branch=$workspace_branch workspace_sha=$WORKSPACE_SHA is not merged into origin/main target_sha=$TARGET_SHA canonical_root=$CANONICAL_ROOT"
        announce "workspace branch=$workspace_branch sha=$WORKSPACE_SHA is not merged into origin/main target_sha=$TARGET_SHA canonical_root=$CANONICAL_ROOT"
        exit 0
    fi
fi

LOCKDIR_HELD="false"
cleanup_lockdir() {
    if [ "$LOCKDIR_HELD" = "true" ]; then
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
}

acquire_lock() {
    local disable_flock="${CANONICAL_RESTART_DISABLE_FLOCK:-false}"
    local lock_wait_seconds="${CANONICAL_RESTART_LOCK_WAIT_SECONDS:-30}"
    local waited_tenths=0
    local max_wait_tenths=0

    case "$lock_wait_seconds" in
        ''|*[!0-9]*)
            lock_wait_seconds=30
            ;;
    esac
    if [ "$lock_wait_seconds" -le 0 ]; then
        lock_wait_seconds=30
    fi

    if [ "$disable_flock" != "true" ] && command -v flock >/dev/null 2>&1; then
        exec 9>"$LOCK_FILE"
        flock 9
        return 0
    fi

    max_wait_tenths=$((lock_wait_seconds * 10))
    log "[WARN] flock unavailable; using mkdir lock fallback lock_dir=$LOCK_DIR"
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        waited_tenths=$((waited_tenths + 1))
        if [ "$waited_tenths" -ge "$max_wait_tenths" ]; then
            log "[ERROR] lock acquisition timed out after ${lock_wait_seconds}s lock_dir=$LOCK_DIR"
            announce "lock acquisition timed out after ${lock_wait_seconds}s lock_dir=$LOCK_DIR"
            exit 1
        fi
        sleep 0.1
    done
    LOCKDIR_HELD="true"
    trap cleanup_lockdir EXIT
}

acquire_lock

pull_output=""
if ! pull_output="$(run_git -C "$CANONICAL_ROOT" pull --ff-only origin main 2>&1)"; then
    log "[ERROR] pull --ff-only origin main failed canonical_root=$CANONICAL_ROOT detail=$pull_output"
    announce "canonical pull failed for root=$CANONICAL_ROOT"
    exit 1
fi

if [ -f "$MARKER_FILE" ]; then
    last_sha="$(cat "$MARKER_FILE" || true)"
    if [ "$last_sha" = "$TARGET_SHA" ]; then
        log "[SKIP] target_sha=$TARGET_SHA already processed"
        announce "target_sha=$TARGET_SHA already processed"
        exit 0
    fi
fi

log "[START] target_sha=$TARGET_SHA workspace_branch=$workspace_branch workspace_sha=$WORKSPACE_SHA canonical_root=$CANONICAL_ROOT"
bash -c "$STOP_CMD"
if ! bash -c "$START_CMD"; then
    log "[CRITICAL] canonical runtime start failed after stop; manual intervention required"
    announce "canonical runtime start failed after stop; manual intervention required"
    exit 1
fi

marker_tmp="$(mktemp "${MARKER_FILE}.tmp.XXXXXX")"
printf '%s\n' "$TARGET_SHA" > "$marker_tmp"
mv "$marker_tmp" "$MARKER_FILE"

log "[DONE] target_sha=$TARGET_SHA marker=$MARKER_FILE"
announce "canonical restart completed for target_sha=$TARGET_SHA canonical_root=$CANONICAL_ROOT"
