from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_pr_body.py"
    spec = importlib.util.spec_from_file_location("validate_pr_body", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_pr_body_text_detects_escaped_newline() -> None:
    module = _load_module()
    errors = module.validate_pr_body_text("## Summary\\n- item")
    assert any("escaped newline" in err for err in errors)


def test_validate_pr_body_text_detects_escaped_newline_in_multiline_body() -> None:
    module = _load_module()
    text = "## Summary\n- first line\n- broken line with \\n literal"
    errors = module.validate_pr_body_text(text)
    assert any("escaped newline" in err for err in errors)


def test_validate_pr_body_text_allows_escaped_newline_in_code_blocks() -> None:
    module = _load_module()
    text = "\n".join(
        [
            "## Summary",
            "- example uses `\\n` for explanation",
            "```bash",
            "printf 'line1\\nline2\\n'",
            "```",
        ]
    )
    assert module.validate_pr_body_text(text) == []


def test_validate_pr_body_text_detects_unbalanced_code_fence() -> None:
    module = _load_module()
    errors = module.validate_pr_body_text("## Summary\n- item\n```bash\necho hi\n")
    assert any("unbalanced fenced code blocks" in err for err in errors)


def test_validate_pr_body_text_detects_missing_structure() -> None:
    module = _load_module()
    errors = module.validate_pr_body_text("plain text only")
    assert any("missing markdown section headers" in err for err in errors)
    assert any("missing markdown list items" in err for err in errors)


def test_validate_pr_body_text_passes_with_valid_markdown() -> None:
    module = _load_module()
    text = "\n".join(
        [
            "## Summary",
            "- item",
            "",
            "## Validation",
            "```bash",
            "pytest -q",
            "```",
        ]
    )
    assert module.validate_pr_body_text(text) == []


def test_fetch_pr_body_reads_body_from_tea_api(monkeypatch) -> None:
    module = _load_module()

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        assert cmd[0] == "/tmp/tea-bin"
        assert check is True
        assert capture_output is True
        assert text is True
        return SimpleNamespace(stdout=json.dumps({"body": "## Summary\n- item"}))

    monkeypatch.setattr(module, "resolve_tea_binary", lambda: "/tmp/tea-bin")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.fetch_pr_body(391) == "## Summary\n- item"


def test_fetch_pr_body_rejects_non_string_body(monkeypatch) -> None:
    module = _load_module()

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        return SimpleNamespace(stdout=json.dumps({"body": 123}))

    monkeypatch.setattr(module, "resolve_tea_binary", lambda: "/tmp/tea-bin")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        module.fetch_pr_body(391)


def test_resolve_tea_binary_falls_back_to_home_bin(monkeypatch, tmp_path) -> None:
    module = _load_module()
    tea_home = tmp_path / "bin" / "tea"
    tea_home.parent.mkdir(parents=True)
    tea_home.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    assert module.resolve_tea_binary() == str(tea_home)
