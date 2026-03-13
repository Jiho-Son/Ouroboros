from __future__ import annotations

import os
import signal
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_OVERNIGHT = REPO_ROOT / "scripts" / "run_overnight.sh"
RUNTIME_MONITOR = REPO_ROOT / "scripts" / "runtime_verify_monitor.sh"
RUNTIME_INSTANCE_ENV = REPO_ROOT / "scripts" / "runtime_instance_env.sh"
RESTART_CANONICAL_MAIN_RUNTIME = (
    REPO_ROOT / "scripts" / "restart_canonical_main_runtime.sh"
)
SYMPHONY_BEFORE_REMOVE_CANONICAL_RESTART = (
    REPO_ROOT / "scripts" / "symphony_before_remove_canonical_restart.sh"
)


def _latest_runtime_log(log_dir: Path) -> str:
    logs = sorted(log_dir.glob("runtime_verify_*.log"))
    assert logs, "runtime monitor did not produce log output"
    return logs[-1].read_text(encoding="utf-8")


def _resolve_runtime_defaults(*, state_root: Path, branch: str) -> dict[str, str]:
    completed = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{RUNTIME_INSTANCE_ENV}"; '
                "runtime_resolve_defaults; "
                'printf "ROOT_DIR=%s\nLOG_DIR=%s\nDASHBOARD_PORT=%s\n'
                'TMUX_SESSION_PREFIX=%s\nLIVE_RUNTIME_LOCK_PATH=%s\n" '
                '"$ROOT_DIR" "$LOG_DIR" "$DASHBOARD_PORT" '
                '"$TMUX_SESSION_PREFIX" "$LIVE_RUNTIME_LOCK_PATH"'
            ),
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "RUNTIME_BRANCH_NAME": branch,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    pairs = [line.split("=", 1) for line in completed.stdout.strip().splitlines()]
    return {key: value for key, value in pairs}


def _run_restart_canonical_main_runtime(
    *,
    tmp_path: Path,
    branch: str,
    target_sha: str,
    dry_run: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    state_root = tmp_path / "overnight"
    hooks_log = tmp_path / "restart-hooks.log"
    marker_path = state_root / "canonical_restart.last_sha"
    env = os.environ.copy()
    env.update(
        {
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "RUNTIME_BRANCH_NAME": branch,
            "CANONICAL_RESTART_STOP_CMD": f"printf 'stop\n' >> '{hooks_log}'",
            "CANONICAL_RESTART_START_CMD": f"printf 'start\n' >> '{hooks_log}'",
        }
    )
    args = [
        "bash",
        str(RESTART_CANONICAL_MAIN_RUNTIME),
        "--target-sha",
        target_sha,
    ]
    if dry_run:
        args.append("--dry-run")

    completed = subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, state_root, hooks_log, marker_path


def _write_fake_git(*, tmp_path: Path) -> Path:
    fake_git = tmp_path / "fake_git.py"
    fake_git.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path


def log(message: str) -> None:
    log_path = os.environ.get("FAKE_GIT_LOG_PATH")
    if not log_path:
        return
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def parse(argv: list[str]) -> tuple[Path, list[str]]:
    cwd = Path(os.getcwd())
    args = list(argv)
    while args[:1] == ["-C"]:
        cwd = Path(args[1])
        args = args[2:]
    return cwd, args


def main() -> int:
    cwd, args = parse(sys.argv[1:])
    workspace_root = Path(os.environ["FAKE_GIT_WORKSPACE_ROOT"])
    canonical_root = Path(os.environ["FAKE_GIT_CANONICAL_ROOT"])
    workspace_branch = os.environ["FAKE_GIT_WORKSPACE_BRANCH"]
    workspace_sha = os.environ["FAKE_GIT_WORKSPACE_SHA"]
    canonical_branch = os.environ.get("FAKE_GIT_CANONICAL_BRANCH", "main")
    canonical_head = os.environ.get("FAKE_GIT_CANONICAL_HEAD", "canonical-head")
    target_sha = os.environ["FAKE_GIT_TARGET_SHA"]
    merged_by_git = os.environ.get("FAKE_GIT_MERGED_BY_GIT", "false") == "true"
    remote_url = os.environ.get("FAKE_GIT_REMOTE_URL", "https://github.com/test-owner/test-repo.git")

    if args == ["branch", "--show-current"]:
        if cwd == workspace_root:
            print(workspace_branch)
            return 0
        if cwd == canonical_root:
            print(canonical_branch)
            return 0
        raise SystemExit(f"unexpected cwd for branch lookup: {cwd}")

    if args == ["rev-parse", "HEAD"]:
        if cwd == workspace_root:
            print(workspace_sha)
            return 0
        if cwd == canonical_root:
            print(canonical_head)
            return 0
        raise SystemExit(f"unexpected cwd for HEAD lookup: {cwd}")

    if args == ["rev-parse", "origin/main"]:
        print(target_sha)
        return 0

    if args == ["remote", "get-url", "origin"]:
        print(remote_url)
        return 0

    if args == ["worktree", "list", "--porcelain"]:
        print(
            f"worktree {workspace_root}\n"
            f"HEAD {workspace_sha}\n"
            f"branch refs/heads/{workspace_branch}\n"
        )
        print(
            f"worktree {canonical_root}\n"
            f"HEAD {canonical_head}\n"
            f"branch refs/heads/{canonical_branch}\n"
        )
        return 0

    if args == ["fetch", "origin"]:
        log(f"fetch:{cwd}")
        return 0

    if args == ["pull", "--ff-only", "origin", "main"]:
        log(f"pull:{cwd}")
        return 0

    if args[:2] == ["merge-base", "--is-ancestor"]:
        log(f"merge-base:{cwd}:{' '.join(args[2:])}")
        return 0 if merged_by_git else 1

    raise SystemExit(f"unsupported fake git args: {args}")


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    return fake_git


def _write_fake_gh(*, tmp_path: Path) -> Path:
    fake_gh = tmp_path / "fake_gh.py"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:3] != ["api", "-X", "GET"]:
        raise SystemExit(f"unsupported fake gh args: {args}")

    merged = os.environ.get("FAKE_GH_MERGED", "false") == "true"
    workspace_branch = os.environ["FAKE_GH_WORKSPACE_BRANCH"]
    workspace_sha = os.environ["FAKE_GH_WORKSPACE_SHA"]
    payload = []
    if merged:
        payload.append(
            {
                "merged_at": "2026-03-14T00:00:00Z",
                "base": {"ref": "main"},
                "head": {"ref": workspace_branch, "sha": workspace_sha},
            }
        )
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    return fake_gh


def _run_symphony_before_remove_hook(
    *,
    tmp_path: Path,
    merged_by_git: bool,
    github_merged: bool,
    target_sha: str,
    dry_run: bool = False,
    workspace_branch: str = "feature/issue-811",
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path, Path]:
    workspace_root = tmp_path / "workspace"
    canonical_root = tmp_path / "canonical-main"
    state_root = tmp_path / "state-root"
    hooks_log = tmp_path / "restart-hooks.log"
    git_log = tmp_path / "fake-git.log"
    marker_path = state_root / "canonical_restart.last_sha"
    workspace_root.mkdir(parents=True, exist_ok=True)
    canonical_root.mkdir(parents=True, exist_ok=True)
    fake_git = _write_fake_git(tmp_path=tmp_path)
    fake_gh = _write_fake_gh(tmp_path=tmp_path)

    env = os.environ.copy()
    env.update(
        {
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "CANONICAL_RESTART_GIT_BIN": str(fake_git),
            "CANONICAL_RESTART_GH_BIN": str(fake_gh),
            "CANONICAL_RESTART_STOP_CMD": f"printf 'stop\n' >> '{hooks_log}'",
            "CANONICAL_RESTART_START_CMD": f"printf 'start\n' >> '{hooks_log}'",
            "FAKE_GIT_LOG_PATH": str(git_log),
            "FAKE_GIT_WORKSPACE_ROOT": str(workspace_root),
            "FAKE_GIT_CANONICAL_ROOT": str(canonical_root),
            "FAKE_GIT_WORKSPACE_BRANCH": workspace_branch,
            "FAKE_GIT_WORKSPACE_SHA": "workspace-sha-1",
            "FAKE_GIT_TARGET_SHA": target_sha,
            "FAKE_GIT_MERGED_BY_GIT": "true" if merged_by_git else "false",
            "FAKE_GH_MERGED": "true" if github_merged else "false",
            "FAKE_GH_WORKSPACE_BRANCH": workspace_branch,
            "FAKE_GH_WORKSPACE_SHA": "workspace-sha-1",
        }
    )

    args = ["bash", str(SYMPHONY_BEFORE_REMOVE_CANONICAL_RESTART)]
    if dry_run:
        args.append("--dry-run")

    completed = subprocess.run(
        args,
        cwd=workspace_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, canonical_root, hooks_log, marker_path, git_log


def test_runtime_instance_defaults_keep_main_canonical(tmp_path: Path) -> None:
    state_root = tmp_path / "overnight"
    defaults = _resolve_runtime_defaults(state_root=state_root, branch="main")

    assert defaults["ROOT_DIR"] == str(REPO_ROOT)
    assert defaults["LOG_DIR"] == str(state_root)
    assert defaults["DASHBOARD_PORT"] == "8080"
    assert defaults["TMUX_SESSION_PREFIX"] == "ouroboros_overnight"
    assert defaults["LIVE_RUNTIME_LOCK_PATH"] == str(state_root / "live_runtime.lock")


def test_before_remove_canonical_restart_skips_unmerged_worktree(
    tmp_path: Path,
) -> None:
    completed, canonical_root, hooks_log, marker_path, git_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=False,
            github_merged=False,
            target_sha="main-sha-1",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "not merged into origin/main" in output
    assert str(canonical_root) in output
    assert not hooks_log.exists()
    assert not marker_path.exists()
    assert "pull:" not in git_log.read_text(encoding="utf-8")


def test_before_remove_canonical_restart_uses_github_merge_signal_for_squash_merges(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=False,
            github_merged=True,
            target_sha="main-sha-squash",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-squash"


def test_before_remove_canonical_restart_skips_main_workspace_cleanup(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-main",
            workspace_branch="main",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert "skip" in completed.stdout.lower()
    assert not hooks_log.exists()
    assert not marker_path.exists()


def test_before_remove_canonical_restart_restarts_canonical_main_once_per_target_sha(
    tmp_path: Path,
) -> None:
    first, canonical_root, hooks_log, marker_path, git_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-1",
        )
    )

    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-1"
    assert f"pull:{canonical_root}" in git_log.read_text(encoding="utf-8")

    second, _, _, _, _ = _run_symphony_before_remove_hook(
        tmp_path=tmp_path,
        merged_by_git=True,
        github_merged=True,
        target_sha="main-sha-1",
    )

    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    output = f"{second.stdout}\n{second.stderr}"
    assert "already processed" in output
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-1"


def test_before_remove_canonical_restart_dry_run_reports_plan_without_mutation(
    tmp_path: Path,
) -> None:
    completed, canonical_root, hooks_log, marker_path, _ = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-2",
            dry_run=True,
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "dry-run" in output
    assert str(canonical_root) in output
    assert str(marker_path) in output
    assert not hooks_log.exists()
    assert not marker_path.exists()


def test_runtime_instance_defaults_isolate_non_main_branch(tmp_path: Path) -> None:
    state_root = tmp_path / "overnight"
    defaults = _resolve_runtime_defaults(
        state_root=state_root,
        branch="feature/worktree-runtime",
    )

    assert defaults["ROOT_DIR"] == str(REPO_ROOT)
    assert defaults["LOG_DIR"] == str(state_root / "feature-worktree-runtime")
    assert defaults["DASHBOARD_PORT"] != "8080"
    assert defaults["TMUX_SESSION_PREFIX"] == (
        "ouroboros_overnight_feature-worktree-runtime"
    )
    assert defaults["LIVE_RUNTIME_LOCK_PATH"] == str(
        state_root / "feature-worktree-runtime" / "live_runtime.lock"
    )


def test_runtime_verify_monitor_detects_live_process_without_pid_files(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    fake_live = subprocess.Popen(
        ["bash", "-lc", 'exec -a "src.main --mode=live" sleep 10'],
        cwd=REPO_ROOT,
    )
    try:
        env = os.environ.copy()
        env.update(
            {
                "ROOT_DIR": str(REPO_ROOT),
                "LOG_DIR": str(log_dir),
                "INTERVAL_SEC": "1",
                "MAX_HOURS": "1",
                "MAX_LOOPS": "1",
                "POLICY_TZ": "UTC",
            }
        )
        completed = subprocess.run(
            ["bash", str(RUNTIME_MONITOR)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

        log_text = _latest_runtime_log(log_dir)
        assert "app_alive=1" in log_text
        assert "[COVERAGE] LIVE_MODE=PASS source=process_liveness" in log_text
        assert "[ANOMALY]" not in log_text
    finally:
        fake_live.terminate()
        fake_live.wait(timeout=5)


def test_runtime_verify_monitor_uses_branch_scoped_defaults_when_unset(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "overnight"
    env = os.environ.copy()
    env.update(
        {
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "RUNTIME_BRANCH_NAME": "feature/worktree-runtime",
            "INTERVAL_SEC": "1",
            "MAX_HOURS": "1",
            "MAX_LOOPS": "1",
            "POLICY_TZ": "UTC",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUNTIME_MONITOR)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    log_dir = state_root / "feature-worktree-runtime"
    log_text = _latest_runtime_log(log_dir)
    assert "[INFO] runtime verify monitor started" in log_text
    assert "[HEARTBEAT]" in log_text
    assert "/home/agentson/repos/The-Ouroboros" not in log_text


def test_run_overnight_fails_fast_when_dashboard_port_in_use(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        env = os.environ.copy()
        env.update(
            {
                "LOG_DIR": str(log_dir),
                "TMUX_AUTO": "false",
                "DASHBOARD_PORT": str(port),
            }
        )
        completed = subprocess.run(
            ["bash", str(RUN_OVERNIGHT)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        output = f"{completed.stdout}\n{completed.stderr}"
        assert "already in use" in output
    finally:
        sock.close()


def test_run_overnight_writes_live_pid_and_watchdog_pid(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "LOG_DIR": str(log_dir),
            "TMUX_AUTO": "false",
            "STARTUP_GRACE_SEC": "1",
            "CHECK_INTERVAL": "2",
            "APP_CMD_BIN": "sleep",
            "APP_CMD_ARGS": "10",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUN_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"

    app_pid = int((log_dir / "app.pid").read_text(encoding="utf-8").strip())
    watchdog_pid = int((log_dir / "watchdog.pid").read_text(encoding="utf-8").strip())

    os.kill(app_pid, 0)
    os.kill(watchdog_pid, 0)

    for pid in (watchdog_pid, app_pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_run_overnight_uses_branch_scoped_defaults_when_log_dir_unset(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "overnight"
    env = os.environ.copy()
    env.update(
        {
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "RUNTIME_BRANCH_NAME": "feature/worktree-runtime",
            "TMUX_AUTO": "false",
            "STARTUP_GRACE_SEC": "1",
            "CHECK_INTERVAL": "2",
            "APP_CMD_BIN": "sleep",
            "APP_CMD_ARGS": "10",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUN_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"

    log_dir = state_root / "feature-worktree-runtime"
    app_pid = int((log_dir / "app.pid").read_text(encoding="utf-8").strip())
    watchdog_pid = int((log_dir / "watchdog.pid").read_text(encoding="utf-8").strip())
    assert "- dashboard port: 8080" not in completed.stdout

    for pid in (watchdog_pid, app_pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_run_overnight_fails_when_process_exits_before_grace_period(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "LOG_DIR": str(log_dir),
            "TMUX_AUTO": "false",
            "STARTUP_GRACE_SEC": "1",
            "APP_CMD_BIN": "false",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUN_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "startup failed:" in output

    watchdog_pid_file = log_dir / "watchdog.pid"
    if watchdog_pid_file.exists():
        watchdog_pid = int(watchdog_pid_file.read_text(encoding="utf-8").strip())
        with pytest.raises(ProcessLookupError):
            os.kill(watchdog_pid, 0)


def test_runtime_verify_monitor_survives_when_no_live_pid(tmp_path: Path) -> None:
    """Regression test for #413: monitor loop must not exit when pgrep finds no live process.

    With set -euo pipefail, pgrep returning exit 1 (no match) would cause the
    whole script to abort via the pipefail mechanism. The fix captures pgrep
    output via a local variable with || true so pipefail is never triggered.

    Verifies that the script: (1) exits 0 after completing MAX_LOOPS=1, and
    (2) logs a HEARTBEAT entry. Whether live_pids is 'none' or not depends on
    what processes happen to be running; either way the script must not crash.
    """
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "ROOT_DIR": str(REPO_ROOT),
            "LOG_DIR": str(log_dir),
            "INTERVAL_SEC": "1",
            "MAX_HOURS": "1",
            "MAX_LOOPS": "1",
            "POLICY_TZ": "UTC",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUNTIME_MONITOR)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"monitor exited non-zero (pipefail regression?): {completed.stderr}"
    )
    log_text = _latest_runtime_log(log_dir)
    assert "[INFO] runtime verify monitor started" in log_text
    assert "[HEARTBEAT]" in log_text, "monitor did not complete a heartbeat cycle"
    # live_pids may be 'none' (no match) or a pid (process found) — either is valid.
    # The critical invariant is that the script survived the loop without pipefail abort.
