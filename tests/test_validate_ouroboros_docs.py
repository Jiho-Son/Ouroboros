from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_ouroboros_docs.py"
    spec = importlib.util.spec_from_file_location("validate_ouroboros_docs", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_plan_source_link_accepts_canonical_source_path() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/README.md").resolve()

    assert module.validate_plan_source_link(path, "./source/ouroboros_plan_v2.txt", errors) is False
    assert module.validate_plan_source_link(path, "./source/ouroboros_plan_v3.txt", errors) is False

    assert errors == []


def test_validate_plan_source_link_rejects_root_relative_path() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/README.md").resolve()

    handled = module.validate_plan_source_link(
        path,
        "/home/agentson/repos/The-Ouroboros/ouroboros_plan_v2.txt",
        errors,
    )

    assert handled is True
    assert errors
    assert "invalid plan link path" in errors[0]
    assert "use ./source/ouroboros_plan_v2.txt" in errors[0]


def test_validate_plan_source_link_rejects_repo_root_relative_path() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/README.md").resolve()

    handled = module.validate_plan_source_link(path, "../../ouroboros_plan_v2.txt", errors)

    assert handled is True
    assert errors
    assert "invalid plan link path" in errors[0]
    assert "must resolve to docs/ouroboros/source/ouroboros_plan_v2.txt" in errors[0]


def test_validate_plan_source_link_accepts_fragment_suffix() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/README.md").resolve()

    handled = module.validate_plan_source_link(path, "./source/ouroboros_plan_v2.txt#sec", errors)

    assert handled is False
    assert errors == []


def test_validate_links_avoids_duplicate_error_for_invalid_plan_link(tmp_path) -> None:
    module = _load_module()
    errors: list[str] = []
    doc = tmp_path / "doc.md"
    doc.write_text(
        "[v2](/home/agentson/repos/The-Ouroboros/ouroboros_plan_v2.txt)\n",
        encoding="utf-8",
    )

    module.validate_links(doc, doc.read_text(encoding="utf-8"), errors)

    assert len(errors) == 1
    assert "invalid plan link path" in errors[0]


def test_validate_issue_status_consistency_reports_conflicts() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/80_implementation_audit.md").resolve()
    text = "\n".join(
        [
            "| REQ-V3-004 | 상태 | 부분 | `#328` 잔여 |",
            "| 항목 | 상태 | ✅ 완료 | `#328` 머지 |",
        ]
    )

    module.validate_issue_status_consistency(path, text, errors)

    assert len(errors) == 1
    assert "conflicting status for issue #328" in errors[0]


def test_validate_issue_status_consistency_allows_done_only() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/80_implementation_audit.md").resolve()
    text = "| 항목 | 상태 | ✅ 완료 | `#371` 머지 |"

    module.validate_issue_status_consistency(path, text, errors)

    assert errors == []


def test_validate_issue_status_consistency_allows_pending_only() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/80_implementation_audit.md").resolve()
    text = "| 항목 | 상태 | 부분 | `#390` 추적 이슈 |"

    module.validate_issue_status_consistency(path, text, errors)

    assert errors == []
