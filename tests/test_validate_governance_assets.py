from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_governance_assets.py"
    spec = importlib.util.spec_from_file_location("validate_governance_assets", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_is_policy_file_detects_ouroboros_policy_docs() -> None:
    module = _load_module()
    assert module.is_policy_file("docs/ouroboros/85_loss_recovery_action_plan.md")
    assert not module.is_policy_file("docs/ouroboros/01_requirements_registry.md")
    assert not module.is_policy_file("docs/workflow.md")
    assert not module.is_policy_file("docs/ouroboros/notes.txt")


def test_validate_registry_sync_requires_registry_update_when_policy_changes() -> None:
    module = _load_module()
    errors: list[str] = []
    module.validate_registry_sync(
        ["docs/ouroboros/85_loss_recovery_action_plan.md"],
        errors,
    )
    assert errors
    assert "policy file changed without updating" in errors[0]


def test_validate_registry_sync_passes_when_registry_included() -> None:
    module = _load_module()
    errors: list[str] = []
    module.validate_registry_sync(
        [
            "docs/ouroboros/85_loss_recovery_action_plan.md",
            "docs/ouroboros/01_requirements_registry.md",
        ],
        errors,
    )
    assert errors == []


def test_load_changed_files_supports_explicit_paths() -> None:
    module = _load_module()
    errors: list[str] = []
    changed = module.load_changed_files(
        ["./docs/ouroboros/85_loss_recovery_action_plan.md", " src/main.py "],
        errors,
    )
    assert errors == []
    assert changed == [
        "docs/ouroboros/85_loss_recovery_action_plan.md",
        "src/main.py",
    ]


def test_load_changed_files_with_range_uses_git_diff(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        assert cmd[:3] == ["git", "diff", "--name-only"]
        assert check is True
        assert capture_output is True
        assert text is True
        return SimpleNamespace(stdout="docs/ouroboros/85_loss_recovery_action_plan.md\nsrc/main.py\n")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    changed = module.load_changed_files(["abc...def"], errors)
    assert errors == []
    assert changed == [
        "docs/ouroboros/85_loss_recovery_action_plan.md",
        "src/main.py",
    ]


def test_validate_task_req_mapping_reports_missing_req_reference(tmp_path) -> None:
    module = _load_module()
    doc = tmp_path / "work_orders.md"
    doc.write_text(
        "- `TASK-OPS-999` no req mapping line\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    module.validate_task_req_mapping(errors, task_doc=doc)
    assert errors
    assert "TASK without REQ mapping" in errors[0]


def test_validate_task_req_mapping_passes_when_req_present(tmp_path) -> None:
    module = _load_module()
    doc = tmp_path / "work_orders.md"
    doc.write_text(
        "- `TASK-OPS-999` (`REQ-OPS-001`): enforce timezone labels\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    module.validate_task_req_mapping(errors, task_doc=doc)
    assert errors == []


def test_validate_pr_traceability_warns_when_req_missing(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("GOVERNANCE_PR_TITLE", "feat: update policy checker")
    monkeypatch.setenv("GOVERNANCE_PR_BODY", "Refs: TASK-OPS-001 TEST-ACC-007")
    warnings: list[str] = []
    module.validate_pr_traceability(warnings)
    assert warnings
    assert "PR text missing REQ-ID reference" in warnings
