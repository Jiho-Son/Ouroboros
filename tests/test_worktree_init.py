from __future__ import annotations

import subprocess
from pathlib import Path


def test_worktree_init_dry_run_describes_python_bootstrap_steps() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", ".codex/worktree_init.sh", "--dry-run"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"repo_root={repo_root}" in result.stdout
    assert "python3 -m venv --system-site-packages" in result.stdout
    assert "pip install --no-build-isolation -e .[dev]" in result.stdout
    assert "cp .env.example .env" in result.stdout
