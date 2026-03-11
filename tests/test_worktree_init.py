from __future__ import annotations

import subprocess
from pathlib import Path


def test_worktree_init_dry_run_describes_python_bootstrap() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        ["bash", ".codex/worktree_init.sh", "--dry-run"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "mode=dry-run" in completed.stdout
    assert "python3 -m venv --system-site-packages" in completed.stdout
    assert "pip install --no-build-isolation -e .[dev]" in completed.stdout
    assert "cp .env.example .env" in completed.stdout
    assert "elixir" not in completed.stdout
