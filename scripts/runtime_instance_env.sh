#!/usr/bin/env bash

runtime_repo_root() {
    if [ -n "${RUNTIME_REPO_ROOT:-}" ]; then
        printf '%s\n' "$RUNTIME_REPO_ROOT"
        return 0
    fi

    local helper_dir
    helper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if git -C "$helper_dir/.." rev-parse --show-toplevel >/dev/null 2>&1; then
        git -C "$helper_dir/.." rev-parse --show-toplevel
        return 0
    fi

    (
        cd "$helper_dir/.." && pwd
    )
}

runtime_branch_name() {
    local repo_root="$1"
    if [ -n "${RUNTIME_BRANCH_NAME:-}" ]; then
        printf '%s\n' "$RUNTIME_BRANCH_NAME"
        return 0
    fi
    git -C "$repo_root" branch --show-current 2>/dev/null || true
}

runtime_branch_slug() {
    local branch_name="$1"
    local repo_root="$2"
    local raw="${RUNTIME_INSTANCE_SLUG:-$branch_name}"

    if [ -z "$raw" ]; then
        raw="$(basename "$repo_root")"
    fi

    raw="$(printf '%s' "$raw" | tr '/[:space:]' '-' | tr -cd 'A-Za-z0-9._-')"
    raw="${raw#-}"
    raw="${raw%-}"
    if [ -z "$raw" ]; then
        raw="runtime"
    fi

    printf '%s\n' "$raw"
}

runtime_state_root() {
    local repo_root="$1"
    local state_root="${OVERNIGHT_STATE_ROOT:-$repo_root/data/overnight}"

    if [[ "$state_root" != /* ]]; then
        state_root="$repo_root/$state_root"
    fi

    printf '%s\n' "$state_root"
}

runtime_default_log_dir() {
    local state_root="$1"
    local branch_name="$2"
    local branch_slug="$3"

    if [ "$branch_name" = "main" ]; then
        printf '%s\n' "$state_root"
        return 0
    fi

    printf '%s\n' "$state_root/$branch_slug"
}

runtime_default_tmux_prefix() {
    local branch_name="$1"
    local branch_slug="$2"

    if [ "$branch_name" = "main" ]; then
        printf '%s\n' "ouroboros_overnight"
        return 0
    fi

    printf '%s\n' "ouroboros_overnight_$branch_slug"
}

runtime_default_dashboard_port() {
    local branch_name="$1"
    local seed="$2"

    if [ "$branch_name" = "main" ]; then
        printf '%s\n' "8080"
        return 0
    fi

    local checksum
    checksum="$(printf '%s' "$seed" | cksum | awk '{print $1}')"
    printf '%s\n' "$((18080 + (checksum % 20000)))"
}

runtime_resolve_defaults() {
    local repo_root
    local branch_name
    local branch_slug
    local state_root

    repo_root="$(runtime_repo_root)"
    branch_name="$(runtime_branch_name "$repo_root")"
    branch_slug="$(runtime_branch_slug "$branch_name" "$repo_root")"
    state_root="$(runtime_state_root "$repo_root")"

    RUNTIME_REPO_ROOT_RESOLVED="$repo_root"
    RUNTIME_BRANCH_NAME_RESOLVED="$branch_name"
    RUNTIME_BRANCH_SLUG_RESOLVED="$branch_slug"

    if [ -z "${ROOT_DIR:-}" ]; then
        ROOT_DIR="$repo_root"
    fi
    if [ -z "${LOG_DIR:-}" ]; then
        LOG_DIR="$(runtime_default_log_dir "$state_root" "$branch_name" "$branch_slug")"
    fi
    if [ -z "${DASHBOARD_PORT:-}" ]; then
        DASHBOARD_PORT="$(runtime_default_dashboard_port "$branch_name" "$repo_root:$branch_name")"
    fi
    if [ -z "${TMUX_SESSION_PREFIX:-}" ]; then
        TMUX_SESSION_PREFIX="$(runtime_default_tmux_prefix "$branch_name" "$branch_slug")"
    fi
    if [ -z "${LIVE_RUNTIME_LOCK_PATH:-}" ]; then
        LIVE_RUNTIME_LOCK_PATH="$LOG_DIR/live_runtime.lock"
    fi

    export ROOT_DIR
    export LOG_DIR
    export DASHBOARD_PORT
    export TMUX_SESSION_PREFIX
    export LIVE_RUNTIME_LOCK_PATH
    export RUNTIME_REPO_ROOT_RESOLVED
    export RUNTIME_BRANCH_NAME_RESOLVED
    export RUNTIME_BRANCH_SLUG_RESOLVED
}
