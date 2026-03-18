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


def test_must_contain_enforces_workflow_github_tokens(tmp_path) -> None:
    module = _load_module()
    workflow_doc = tmp_path / "workflow.md"
    workflow_doc.write_text(
        "\n".join(
            [
                "Session Handover Gate (Mandatory)",
                "python3 scripts/session_handover_check.py --strict",
                "Agent GitHub Preflight (Mandatory)",
                "gh auth status",
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
            "gh auth status",
        ],
        errors,
    )
    assert errors == []


def test_must_contain_fails_when_workflow_missing_github_token(tmp_path) -> None:
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
        ["gh auth status"],
        errors,
    )
    assert any("gh auth status" in err for err in errors)


def test_must_contain_enforces_commands_github_tokens(tmp_path) -> None:
    module = _load_module()
    commands_doc = tmp_path / "commands.md"
    commands_doc.write_text(
        "\n".join(
            [
                "Session Handover Preflight (Mandatory)",
                "python3 scripts/session_handover_check.py --strict",
                "GitHub CLI",
                "gh auth status",
                "gh pr status",
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
            "GitHub CLI",
            "gh auth status",
            "gh pr status",
        ],
        errors,
    )
    assert errors == []


def test_must_contain_fails_when_commands_missing_github_token(tmp_path) -> None:
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
        ["gh pr status"],
        errors,
    )
    assert any("gh pr status" in err for err in errors)


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


def test_validate_korean_communication_tokens_passes_with_section_scoped_keywords(
    tmp_path, monkeypatch
) -> None:
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    (tmp_path / "WORKFLOW.md").write_text(
        "\n".join(
            [
                "## Korean Communication Policy (Mandatory)",
                "- Symphony unattended Linear 작업의 서술형 문장은 한글을 기본 언어로 사용한다.",
                "- 기술 토큰(코드/경로/명령/식별자)은 원문 표기를 유지한다.",
                "- 이 규칙은 Linear workpad, 이슈 코멘트, 최종 보고에 동일하게 적용한다.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs / "agent-constraints.md").write_text(
        "\n".join(
            [
                "## History",
                "### arbitrary-entry",
                "- Symphony unattended Linear 실행에서 workpad/코멘트/최종 보고의 "
                "서술형 문장은 한글을 기본으로 작성한다.",
                "- 기술 토큰은 원문 표기를 유지한다.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    module.validate_korean_communication_tokens(errors)
    assert errors == []


def test_validate_korean_communication_tokens_fails_when_workflow_file_missing(
    tmp_path, monkeypatch
) -> None:
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    (docs / "agent-constraints.md").write_text(
        "### arbitrary-entry\n- 한글 기본\n- 원문 표기 유지\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    module.validate_korean_communication_tokens(errors)
    assert any("missing file: WORKFLOW.md" in err for err in errors)


def test_validate_korean_communication_tokens_checks_keywords_inside_policy_section_only(
    tmp_path, monkeypatch
) -> None:
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    (tmp_path / "WORKFLOW.md").write_text(
        "\n".join(
            [
                "## Korean Communication Policy (Mandatory)",
                "- Symphony unattended 작업에서는 한글을 기본으로 작성한다.",
                "",
                "## Unrelated Section",
                "- 기술 토큰은 원문 표기를 유지한다.",
                "- Linear workpad/코멘트/최종 보고 경로는 별도 문단에서 설명한다.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs / "agent-constraints.md").write_text(
        "\n".join(
            [
                "### arbitrary-entry",
                "- 한글 기본 작성",
                "- 기술 토큰 원문 표기 유지",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    module.validate_korean_communication_tokens(errors)
    assert any("WORKFLOW.md: missing Korean policy keyword group" in err for err in errors)


def test_validate_korean_communication_tokens_constraints_do_not_require_fixed_date(
    tmp_path, monkeypatch
) -> None:
    module = _load_module()
    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    (tmp_path / "WORKFLOW.md").write_text(
        "\n".join(
            [
                "## Korean Communication Policy (Mandatory)",
                "- Symphony unattended Linear 작업의 서술형 문장은 한글을 기본 언어로 사용한다.",
                "- 기술 토큰(코드/경로/명령/식별자)은 원문 표기를 유지한다.",
                "- 이 규칙은 Linear workpad, 이슈 코멘트, 최종 보고에 동일하게 적용한다.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (docs / "agent-constraints.md").write_text(
        "\n".join(
            [
                "### arbitrary-entry",
                "- Symphony unattended Linear 실행에서 workpad/코멘트/최종 보고의 "
                "서술형 문장은 한글을 기본으로 작성한다.",
                "- 기술 토큰은 원문 표기를 유지한다.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    module.validate_korean_communication_tokens(errors)
    assert errors == []


def test_extract_markdown_section_stops_at_h1_boundary() -> None:
    module = _load_module()

    section = module._extract_markdown_section(
        "\n".join(
            [
                "## Korean Communication Policy (Mandatory)",
                "- keep this line",
                "# Next Top Header",
                "- should not be included",
            ]
        ),
        module.KOREAN_POLICY_WORKFLOW_HEADER,
    )

    assert section == "- keep this line"


def test_extract_markdown_section_stops_at_h3_boundary() -> None:
    module = _load_module()

    section = module._extract_markdown_section(
        "\n".join(
            [
                "## Korean Communication Policy (Mandatory)",
                "- keep this line",
                "### Nested Header",
                "- should not be included",
            ]
        ),
        module.KOREAN_POLICY_WORKFLOW_HEADER,
    )

    assert section == "- keep this line"


def test_extract_markdown_section_ignores_indented_hash_lines() -> None:
    module = _load_module()

    section = module._extract_markdown_section(
        "\n".join(
            [
                "## Korean Communication Policy (Mandatory)",
                "- keep this line",
                "  ## not-a-real-header",
                "- still inside section",
                "## Next Header",
                "- should not be included",
            ]
        ),
        module.KOREAN_POLICY_WORKFLOW_HEADER,
    )

    assert section == "\n".join(
        [
            "- keep this line",
            "  ## not-a-real-header",
            "- still inside section",
        ]
    )


def test_validate_keyword_groups_reports_missing_keywords() -> None:
    module = _load_module()
    errors: list[str] = []

    module._validate_keyword_groups(
        "기술",
        (("technical-token-preservation", ("기술", "토큰", "원문", "표기")),),
        path=Path("docs/agent-constraints.md"),
        errors=errors,
    )

    assert len(errors) == 1
    assert "docs/agent-constraints.md: missing Korean policy keyword group" in errors[0]
    assert "technical-token-preservation" in errors[0]
    for keyword in ("토큰", "원문", "표기"):
        assert keyword in errors[0]
