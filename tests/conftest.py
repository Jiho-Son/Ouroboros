"""Shared test fixtures for The Ouroboros test suite."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from src.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Return a Settings instance with safe test defaults."""
    return Settings(
        KIS_APP_KEY="test_app_key",
        KIS_APP_SECRET="test_app_secret",
        KIS_ACCOUNT_NO="12345678-01",
        KIS_BASE_URL="https://openapivts.koreainvestment.com:9443",
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="test_gemini_key",
        CIRCUIT_BREAKER_PCT=-3.0,
        FAT_FINGER_PCT=30.0,
        CONFIDENCE_THRESHOLD=80,
        DB_PATH=":memory:",
        ENABLED_MARKETS="KR",
    )


@pytest.fixture
def fake_backtest_gate_gh_factory() -> Callable[..., Path]:
    """Return a factory that writes a fake `gh` binary for Backtest Gate tests."""

    def factory(path: Path, *, env_prefix: str = "FAKE_BACKTEST_GATE") -> Path:
        script = """#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path


def _arg_value(args: list[str], flag: str) -> str:
    index = args.index(flag)
    return args[index + 1]


def main() -> int:
    args = sys.argv[1:]

    if args[:2] == ["run", "list"]:
        payload = [
            {
                "databaseId": int(os.environ["{PREFIX}_RUN_ID"]),
                "status": "completed",
                "conclusion": "success",
                "createdAt": "2026-03-31T17:15:44Z",
                "updatedAt": "2026-03-31T17:16:37Z",
                "headBranch": "main",
                "event": "schedule",
            }
        ]
        print(json.dumps(payload))
        return 0

    if args[:2] == ["run", "download"]:
        destination = Path(_arg_value(args, "-D"))
        destination.mkdir(parents=True, exist_ok=True)
        record_path = os.environ.get("{PREFIX}_DOWNLOAD_DEST_RECORD")
        if record_path:
            Path(record_path).write_text(str(destination), encoding="utf-8")
        if os.environ.get("{PREFIX}_DOWNLOAD_FAIL", "false") == "true":
            return 1
        source = Path(os.environ["{PREFIX}_ARTIFACT_SOURCE"])
        shutil.copy(source, destination / source.name)
        return 0

    raise SystemExit(f"unsupported fake gh args: {args}")


if __name__ == "__main__":
    raise SystemExit(main())
"""
        path.write_text(script.replace("{PREFIX}", env_prefix), encoding="utf-8")
        path.chmod(0o755)
        return path

    return factory
