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
        assert cmd[0] == "git"
        assert capture_output is True
        assert text is True
        if cmd[:4] == ["git", "rev-parse", "--verify", "--quiet"]:
            assert check is False
            return SimpleNamespace(returncode=0, stdout="")
        assert cmd[:3] == ["git", "diff", "--name-only"]
        assert check is True
        return SimpleNamespace(
            stdout="docs/ouroboros/85_loss_recovery_action_plan.md\nsrc/main.py\n"
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    changed = module.load_changed_files(["abc...def"], errors)
    assert errors == []
    assert changed == [
        "docs/ouroboros/85_loss_recovery_action_plan.md",
        "src/main.py",
    ]


def test_load_changed_files_with_range_skips_when_before_sha_unreachable(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []

    def fake_run(cmd, check, capture_output, text):  # noqa: ANN001
        assert cmd[0] == "git"
        assert capture_output is True
        assert text is True
        if cmd[:4] == ["git", "rev-parse", "--verify", "--quiet"]:
            assert check is False
            if cmd[-1] == "missing^{commit}":
                return SimpleNamespace(returncode=1, stdout="")
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError("git diff should not run when before SHA is unreachable")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    changed = module.load_changed_files(["missing...head"], errors)
    assert errors == []
    assert changed == []


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


def test_validate_pr_traceability_fails_when_req_missing(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("GOVERNANCE_PR_TITLE", "feat: update policy checker")
    monkeypatch.setenv("GOVERNANCE_PR_BODY", "Refs: TASK-OPS-001 TEST-ACC-007")
    errors: list[str] = []
    module.validate_pr_traceability(errors)
    assert errors
    assert "PR text missing REQ-ID reference" in errors


def test_validate_read_only_approval_requires_evidence(monkeypatch) -> None:
    module = _load_module()
    changed_files = ["src/core/risk_manager.py"]
    errors: list[str] = []
    warnings: list[str] = []
    monkeypatch.setenv(
        "GOVERNANCE_PR_BODY",
        "\n".join(
            [
                "## READ-ONLY Approval (Required when touching READ-ONLY files)",
                "- Touched READ-ONLY files: src/core/risk_manager.py",
                "- Human approval: TBD",
                "- Test suite 1: pytest -q",
                "- Test suite 2: TBD",
            ]
        ),
    )

    module.validate_read_only_approval(changed_files, errors, warnings)
    assert warnings == []
    assert any("Human approval" in err for err in errors)
    assert any("Test suite 2" in err for err in errors)


def test_validate_read_only_approval_passes_with_complete_evidence(monkeypatch) -> None:
    module = _load_module()
    changed_files = ["src/core/risk_manager.py"]
    errors: list[str] = []
    warnings: list[str] = []
    monkeypatch.setenv(
        "GOVERNANCE_PR_BODY",
        "\n".join(
            [
                "## READ-ONLY Approval (Required when touching READ-ONLY files)",
                "- Touched READ-ONLY files: src/core/risk_manager.py",
                "- Human approval: https://example.com/review/123",
                "- Test suite 1: pytest -q tests/test_risk.py",
                "- Test suite 2: pytest -q tests/test_main.py -k risk",
            ]
        ),
    )

    module.validate_read_only_approval(changed_files, errors, warnings)
    assert errors == []
    assert warnings == []


def test_validate_read_only_approval_fails_without_pr_body(monkeypatch) -> None:
    module = _load_module()
    changed_files = ["src/core/risk_manager.py"]
    errors: list[str] = []
    warnings: list[str] = []
    monkeypatch.delenv("GOVERNANCE_PR_BODY", raising=False)

    module.validate_read_only_approval(changed_files, errors, warnings)
    assert warnings == []
    assert errors
    assert "approval evidence is required" in errors[0]


def test_validate_read_only_approval_skips_when_no_readonly_file_changed() -> None:
    module = _load_module()
    changed_files = ["src/main.py"]
    errors: list[str] = []
    warnings: list[str] = []

    module.validate_read_only_approval(changed_files, errors, warnings)
    assert errors == []
    assert warnings == []


def test_must_contain_enforces_workflow_newline_helper_tokens(tmp_path) -> None:
    module = _load_module()
    workflow_doc = tmp_path / "workflow.md"
    workflow_doc.write_text(
        "\n".join(
            [
                "Session Handover Gate (Mandatory)",
                "python3 scripts/session_handover_check.py --strict",
                "Agent GitHub Preflight (Mandatory)",
                "python3 scripts/github_pr.py create",
            ]
        ),
        encoding="utf-8",
    )
    errors: list[str] = []
    module.must_contain(
        workflow_doc,
        [
            "Session Handover Gate (Mandatory)",
            "session_handover_check.py --strict",
            "Agent GitHub Preflight (Mandatory)",
            "scripts/github_pr.py",
        ],
        errors,
    )
    assert errors == []


def test_must_contain_fails_when_workflow_missing_newline_helper_token(tmp_path) -> None:
    module = _load_module()
    workflow_doc = tmp_path / "workflow.md"
    workflow_doc.write_text(
        "\n".join(
            [
                "Session Handover Gate (Mandatory)",
                "python3 scripts/session_handover_check.py --strict",
            ]
        ),
        encoding="utf-8",
    )
    errors: list[str] = []
    module.must_contain(
        workflow_doc,
        ["scripts/github_pr.py"],
        errors,
    )
    assert any("scripts/github_pr.py" in err for err in errors)


def test_must_contain_enforces_commands_newline_section_tokens(tmp_path) -> None:
    module = _load_module()
    commands_doc = tmp_path / "commands.md"
    commands_doc.write_text(
        "\n".join(
            [
                "Session Handover Preflight (Mandatory)",
                "python3 scripts/session_handover_check.py --strict",
                "GitHub Helper + CLI",
                "gh auth status",
                "python3 scripts/github_pr.py current",
            ]
        ),
        encoding="utf-8",
    )
    errors: list[str] = []
    module.must_contain(
        commands_doc,
        [
            "Session Handover Preflight (Mandatory)",
            "session_handover_check.py --strict",
            "GitHub Helper + CLI",
            "gh auth status",
            "scripts/github_pr.py",
        ],
        errors,
    )
    assert errors == []


def test_must_contain_fails_when_commands_missing_newline_section_token(tmp_path) -> None:
    module = _load_module()
    commands_doc = tmp_path / "commands.md"
    commands_doc.write_text(
        "\n".join(
            [
                "Session Handover Preflight (Mandatory)",
                "python3 scripts/session_handover_check.py --strict",
                "gh auth status",
            ]
        ),
        encoding="utf-8",
    )
    errors: list[str] = []
    module.must_contain(
        commands_doc,
        ["scripts/github_pr.py"],
        errors,
    )
    assert any("scripts/github_pr.py" in err for err in errors)


def test_validate_task_test_pairing_reports_missing_test_reference(tmp_path) -> None:
    module = _load_module()
    doc = tmp_path / "work_orders.md"
    doc.write_text(
        "- `TASK-OPS-999` (`REQ-OPS-001`): enforce timezone labels only\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    module.validate_task_test_pairing(errors, task_doc=doc)
    assert errors
    assert "TASK without TEST mapping" in errors[0]


def test_validate_task_test_pairing_passes_when_test_present(tmp_path) -> None:
    module = _load_module()
    doc = tmp_path / "work_orders.md"
    doc.write_text(
        "- `TASK-OPS-999` (`REQ-OPS-001`,`TEST-ACC-007`): enforce timezone labels\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    module.validate_task_test_pairing(errors, task_doc=doc)
    assert errors == []


def test_validate_timezone_policy_tokens_requires_kst_or_utc(tmp_path, monkeypatch) -> None:
    module = _load_module()
    docs = tmp_path / "docs"
    ouroboros = docs / "ouroboros"
    docs.mkdir(parents=True)
    ouroboros.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    (ouroboros / "01_requirements_registry.md").write_text("REQ-OPS-001\nUTC\n", encoding="utf-8")
    (ouroboros / "30_code_level_work_orders.md").write_text(
        "TASK-OPS-001 (`REQ-OPS-001`,`TEST-ACC-007`)\nKST\n",
        encoding="utf-8",
    )
    (docs / "workflow.md").write_text("timezone policy: KST and UTC\n", encoding="utf-8")

    errors: list[str] = []
    module.validate_timezone_policy_tokens(errors)
    assert errors == []

    (docs / "workflow.md").write_text("timezone policy missing labels\n", encoding="utf-8")
    errors = []
    module.validate_timezone_policy_tokens(errors)
    assert errors
    assert any("missing timezone policy token" in err for err in errors)
