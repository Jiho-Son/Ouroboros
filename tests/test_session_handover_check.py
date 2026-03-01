from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "session_handover_check.py"
    spec = importlib.util.spec_from_file_location("session_handover_check", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ci_mode_skips_date_branch_and_merge_gate(monkeypatch, tmp_path) -> None:
    module = _load_module()
    handover = tmp_path / "session-handover.md"
    handover.write_text(
        "\n".join(
            [
                "### 2000-01-01 | session=test",
                "- branch: feature/other-branch",
                "- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md",
                "- open_issues_reviewed: #1",
                "- next_ticket: #123",
                "- process_gate_checked: process_ticket=#1 merged_to_feature_branch=no",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "HANDOVER_LOG", handover)

    errors: list[str] = []
    module._check_handover_entry(
        branch="feature/current-branch",
        strict=True,
        ci_mode=True,
        errors=errors,
    )
    assert errors == []


def test_ci_mode_still_blocks_tbd_next_ticket(monkeypatch, tmp_path) -> None:
    module = _load_module()
    handover = tmp_path / "session-handover.md"
    handover.write_text(
        "\n".join(
            [
                "### 2000-01-01 | session=test",
                "- branch: feature/other-branch",
                "- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md",
                "- open_issues_reviewed: #1",
                "- next_ticket: #TBD",
                "- process_gate_checked: process_ticket=#1 merged_to_feature_branch=no",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "HANDOVER_LOG", handover)

    errors: list[str] = []
    module._check_handover_entry(
        branch="feature/current-branch",
        strict=True,
        ci_mode=True,
        errors=errors,
    )
    assert "latest handover entry must not use placeholder next_ticket (#TBD)" in errors


def test_non_ci_strict_enforces_date_branch_and_merge_gate(monkeypatch, tmp_path) -> None:
    module = _load_module()
    handover = tmp_path / "session-handover.md"
    handover.write_text(
        "\n".join(
            [
                "### 2000-01-01 | session=test",
                "- branch: feature/other-branch",
                "- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md",
                "- open_issues_reviewed: #1",
                "- next_ticket: #123",
                "- process_gate_checked: process_ticket=#1 merged_to_feature_branch=no",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "HANDOVER_LOG", handover)

    errors: list[str] = []
    module._check_handover_entry(
        branch="feature/current-branch",
        strict=True,
        ci_mode=False,
        errors=errors,
    )
    assert any("must contain today's UTC date" in e for e in errors)
    assert any("must target current branch" in e for e in errors)
    assert any("merged_to_feature_branch=no" in e for e in errors)


def test_non_ci_strict_still_blocks_tbd_next_ticket(monkeypatch, tmp_path) -> None:
    module = _load_module()
    handover = tmp_path / "session-handover.md"
    handover.write_text(
        "\n".join(
            [
                "### 2000-01-01 | session=test",
                "- branch: feature/other-branch",
                "- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md",
                "- open_issues_reviewed: #1",
                "- next_ticket: #TBD",
                "- process_gate_checked: process_ticket=#1 merged_to_feature_branch=yes",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "HANDOVER_LOG", handover)

    errors: list[str] = []
    module._check_handover_entry(
        branch="feature/current-branch",
        strict=True,
        ci_mode=False,
        errors=errors,
    )
    assert "latest handover entry must not use placeholder next_ticket (#TBD)" in errors
