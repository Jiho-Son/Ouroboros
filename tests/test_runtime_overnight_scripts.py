from __future__ import annotations

import os
import signal
import socket
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_OVERNIGHT = REPO_ROOT / "scripts" / "run_overnight.sh"
RUNTIME_MONITOR = REPO_ROOT / "scripts" / "runtime_verify_monitor.sh"


def _latest_runtime_log(log_dir: Path) -> str:
    logs = sorted(log_dir.glob("runtime_verify_*.log"))
    assert logs, "runtime monitor did not produce log output"
    return logs[-1].read_text(encoding="utf-8")


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
    finally:
        fake_live.terminate()
        fake_live.wait(timeout=5)


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
            "APP_CMD": "sleep 10",
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
