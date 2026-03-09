from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_stdlib_logging_not_shadowed_when_src_is_first_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = """
from pathlib import Path
import sys

repo_root = Path.cwd()
sys.path.insert(0, str(repo_root / "src"))

import logging

assert hasattr(logging, "getLogger")
assert "src/logging" not in str(getattr(logging, "__file__", ""))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_main_py_help_runs_without_pythonpath() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "src/main.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
