from __future__ import annotations

import importlib.util
from pathlib import Path

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
            "- REQ-OPS-001 / TASK-OPS-001 / TEST-OPS-001",
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
            "- REQ-OPS-001 / TASK-OPS-001 / TEST-OPS-001",
            "",
            "## Validation",
            "```bash",
            "pytest -q",
            "```",
        ]
    )
    assert module.validate_pr_body_text(text) == []


def test_validate_pr_body_text_detects_missing_req_id() -> None:
    module = _load_module()
    text = "## Summary\n- TASK-OPS-001 / TEST-OPS-001 item\n"
    errors = module.validate_pr_body_text(text)
    assert any("REQ-ID" in err for err in errors)


def test_validate_pr_body_text_detects_missing_task_id() -> None:
    module = _load_module()
    text = "## Summary\n- REQ-OPS-001 / TEST-OPS-001 item\n"
    errors = module.validate_pr_body_text(text)
    assert any("TASK-ID" in err for err in errors)


def test_validate_pr_body_text_detects_missing_test_id() -> None:
    module = _load_module()
    text = "## Summary\n- REQ-OPS-001 / TASK-OPS-001 item\n"
    errors = module.validate_pr_body_text(text)
    assert any("TEST-ID" in err for err in errors)


def test_validate_pr_body_text_skips_governance_when_disabled() -> None:
    module = _load_module()
    text = "## Summary\n- item without any IDs\n"
    errors = module.validate_pr_body_text(text, check_governance=False)
    assert not any("REQ-ID" in err or "TASK-ID" in err or "TEST-ID" in err for err in errors)


def test_validate_pr_body_text_rejects_governance_ids_in_code_block_only() -> None:
    """Regression for review comment: IDs inside code fences must not count."""
    module = _load_module()
    text = "\n".join(
        [
            "## Summary",
            "- no governance IDs in narrative text",
            "```text",
            "REQ-FAKE-999",
            "TASK-FAKE-999",
            "TEST-FAKE-999",
            "```",
        ]
    )
    errors = module.validate_pr_body_text(text)
    assert any("REQ-ID" in err for err in errors)
    assert any("TASK-ID" in err for err in errors)
    assert any("TEST-ID" in err for err in errors)


def test_fetch_pr_body_reads_body_from_github_helper(monkeypatch) -> None:
    module = _load_module()

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return type("Completed", (), {"stdout": "https://github.com/Jiho-Son/Ouroboros.git\n"})()
        assert cmd[:3] == ["python3", "scripts/github_pr.py", "field"]
        assert check is True
        assert capture_output is True
        assert text is True
        return type("Completed", (), {"stdout": "## Summary\n- item\n"})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.fetch_pr_body(391) == "## Summary\n- item"


def test_fetch_pr_body_raises_on_helper_failure(monkeypatch) -> None:
    module = _load_module()

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        if cmd[:4] == ["git", "remote", "get-url", "origin"]:
            return type("Completed", (), {"stdout": "https://github.com/Jiho-Son/Ouroboros.git\n"})()
        raise module.subprocess.CalledProcessError(1, cmd, stderr="boom")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        module.fetch_pr_body(391)
