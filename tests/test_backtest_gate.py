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


def test_sync_backtest_gate_artifact_downloads_latest_schedule_run(
    tmp_path, fake_backtest_gate_gh_factory
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    artifact_source = tmp_path / "backtest_gate_20260331_171626.log"
    artifact_source.write_text(
        "2026-03-31T17:16:32Z [PASS] full backtest gate passed\n",
        encoding="utf-8",
    )

    fake_gh = tmp_path / "fake_gh.py"
    fake_backtest_gate_gh_factory(fake_gh)

    output_dir = tmp_path / "backtest-gate"
    marker_file = tmp_path / ".latest_backtest_gate_run"
    env = os.environ.copy()
    env.update(
        {
            "BACKTEST_GATE_GH_BIN": str(fake_gh),
            "BACKTEST_GATE_LOG_DIR": str(output_dir),
            "BACKTEST_GATE_SYNC_MARKER_FILE": str(marker_file),
            "FAKE_BACKTEST_GATE_RUN_ID": "23810195275",
            "FAKE_BACKTEST_GATE_ARTIFACT_SOURCE": str(artifact_source),
        }
    )

    first = subprocess.run(
        ["/bin/bash", "scripts/sync_backtest_gate_artifact.sh"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "synced run_id=23810195275 files=1"
    mirrored = output_dir / artifact_source.name
    assert mirrored.exists()
    assert mirrored.read_text(encoding="utf-8") == artifact_source.read_text(
        encoding="utf-8"
    )
    assert marker_file.read_text(encoding="utf-8").strip() == "23810195275"

    second = subprocess.run(
        ["/bin/bash", "scripts/sync_backtest_gate_artifact.sh"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "already_synced run_id=23810195275"


def test_sync_backtest_gate_artifact_cleans_up_tmp_dir_on_download_failure(
    tmp_path, fake_backtest_gate_gh_factory
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    fake_gh = fake_backtest_gate_gh_factory(tmp_path / "fake_gh.py")
    output_dir = tmp_path / "backtest-gate"
    marker_file = tmp_path / ".latest_backtest_gate_run"
    download_dest_record = tmp_path / "download_dest.txt"
    env = os.environ.copy()
    env.update(
        {
            "BACKTEST_GATE_GH_BIN": str(fake_gh),
            "BACKTEST_GATE_LOG_DIR": str(output_dir),
            "BACKTEST_GATE_SYNC_MARKER_FILE": str(marker_file),
            "FAKE_BACKTEST_GATE_RUN_ID": "23810195275",
            "FAKE_BACKTEST_GATE_DOWNLOAD_FAIL": "true",
            "FAKE_BACKTEST_GATE_DOWNLOAD_DEST_RECORD": str(download_dest_record),
            "FAKE_BACKTEST_GATE_ARTIFACT_SOURCE": str(
                tmp_path / "unused_backtest_gate.log"
            ),
        }
    )

    completed = subprocess.run(
        ["/bin/bash", "scripts/sync_backtest_gate_artifact.sh"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    tmp_dir = Path(download_dest_record.read_text(encoding="utf-8").strip())
    assert not tmp_dir.exists(), "temporary download directory leaked on failure"
