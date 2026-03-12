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
            "CANONICAL_RESTART_STOP_CMD": f"printf 'stop\\n' >> '{hooks_log}'",
            "CANONICAL_RESTART_START_CMD": f"printf 'start\\n' >> '{hooks_log}'",
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


def test_runtime_instance_defaults_keep_main_canonical(tmp_path: Path) -> None:
    state_root = tmp_path / "overnight"
    defaults = _resolve_runtime_defaults(state_root=state_root, branch="main")

    assert defaults["ROOT_DIR"] == str(REPO_ROOT)
    assert defaults["LOG_DIR"] == str(state_root)
    assert defaults["DASHBOARD_PORT"] == "8080"
    assert defaults["TMUX_SESSION_PREFIX"] == "ouroboros_overnight"
    assert defaults["LIVE_RUNTIME_LOCK_PATH"] == str(state_root / "live_runtime.lock")


def test_restart_canonical_main_runtime_rejects_non_main_branch(
    tmp_path: Path,
) -> None:
    completed, _, hooks_log, marker_path = _run_restart_canonical_main_runtime(
        tmp_path=tmp_path,
        branch="feature/worktree-runtime",
        target_sha="merge-sha-1",
    )

    assert completed.returncode != 0
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "canonical restart only runs from the main checkout" in output
    assert not hooks_log.exists()
    assert not marker_path.exists()


def test_restart_canonical_main_runtime_records_and_skips_duplicate_sha(
    tmp_path: Path,
) -> None:
    first, state_root, hooks_log, marker_path = _run_restart_canonical_main_runtime(
        tmp_path=tmp_path,
        branch="main",
        target_sha="merge-sha-1",
    )

    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "merge-sha-1"
    assert state_root.exists()

    second, _, _, _ = _run_restart_canonical_main_runtime(
        tmp_path=tmp_path,
        branch="main",
        target_sha="merge-sha-1",
    )

    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    output = f"{second.stdout}\n{second.stderr}"
    assert "already processed" in output
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "merge-sha-1"


def test_restart_canonical_main_runtime_dry_run_does_not_mutate_state(
    tmp_path: Path,
) -> None:
    completed, state_root, hooks_log, marker_path = _run_restart_canonical_main_runtime(
        tmp_path=tmp_path,
        branch="main",
        target_sha="merge-sha-2",
        dry_run=True,
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "dry-run" in output
    assert str(state_root) in output
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
