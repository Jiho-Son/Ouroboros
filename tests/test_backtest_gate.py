from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_backtest_gate_auto_skip_works_without_rg(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "git",
        """#!/bin/bash
set -euo pipefail
if [ "$1" = "rev-parse" ]; then
  exit 0
fi
if [ "$1" = "diff" ]; then
  printf '%s\n' 'docs/workflow.md'
  exit 0
fi
exit 1
""",
    )

    for tool in ("mkdir", "date", "tee", "grep"):
        target = subprocess.run(
            ["which", tool],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        os.symlink(target, fake_bin / tool)

    env = os.environ.copy()
    env["PATH"] = str(fake_bin)
    env["BACKTEST_MODE"] = "auto"
    env["BASE_REF"] = "origin/main"
    env["FORCE_FULL_BACKTEST"] = "false"
    env["LOG_DIR"] = str(tmp_path / "logs")

    result = subprocess.run(
        ["/bin/bash", "scripts/backtest_gate.sh"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "[PASS] backtest gate skipped" in (result.stdout + result.stderr)
