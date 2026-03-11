from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "validate_docs_sync.py"
    spec = importlib.util.spec_from_file_location("validate_docs_sync", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_command_endpoints_parses_markdown_table_rows() -> None:
    module = _load_module()
    text = "\n".join(
        [
            "| Endpoint | Description |",
            "|----------|-------------|",
            "| `GET /api/status` | status |",
            "| `POST /api/run` | run |",
            "| not-a-row | ignored |",
        ]
    )
    endpoints = module.collect_command_endpoints(text)
    assert endpoints == ["GET /api/status", "POST /api/run"]


def test_validate_links_resolve_detects_absolute_and_broken_links(tmp_path) -> None:
    module = _load_module()
    doc = tmp_path / "doc.md"
    existing = tmp_path / "ok.md"
    existing.write_text("# ok\n", encoding="utf-8")
    doc.write_text(
        "\n".join(
            [
                "[ok](./ok.md)",
                "[abs](/tmp/nowhere.md)",
                "[broken](./missing.md)",
            ]
        ),
        encoding="utf-8",
    )
    errors: list[str] = []
    module.validate_links_resolve(doc, doc.read_text(encoding="utf-8"), errors)

    assert any("absolute link is forbidden" in err for err in errors)
    assert any("broken link" in err for err in errors)


def test_validate_summary_docs_reference_core_docs(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["README.md"]): (
            "docs/workflow.md docs/commands.md docs/testing.md"
        ),
        str(module.REQUIRED_FILES["CLAUDE.md"]): "docs/workflow.md docs/commands.md",
        str(module.REQUIRED_FILES["AGENTS.md"]): (
            "docs/workflow.md docs/commands.md docs/agent-constraints.md "
            "docs/README.md .codex/worktree_init.sh"
        ),
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_summary_docs_reference_core_docs(errors)
    assert errors == []


def test_validate_summary_docs_reference_core_docs_reports_missing_links(
    monkeypatch,
) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["README.md"]): "docs/workflow.md",
        str(module.REQUIRED_FILES["CLAUDE.md"]): "docs/workflow.md",
        str(module.REQUIRED_FILES["AGENTS.md"]): "docs/workflow.md",
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_summary_docs_reference_core_docs(errors)

    assert any("README.md" in err and "docs/commands.md" in err for err in errors)
    assert any("README.md" in err and "docs/testing.md" in err for err in errors)
    assert any("CLAUDE.md" in err and "docs/commands.md" in err for err in errors)
    assert any("AGENTS.md" in err and "docs/commands.md" in err for err in errors)
    assert any("AGENTS.md" in err and "docs/agent-constraints.md" in err for err in errors)


def test_validate_required_files_exist_includes_agents_md() -> None:
    module = _load_module()
    assert "AGENTS.md" in module.REQUIRED_FILES


def test_validate_required_files_exist_includes_harness_publish_files() -> None:
    module = _load_module()
    assert "agent_constraints" in module.REQUIRED_FILES
    assert "push_skill" in module.REQUIRED_FILES


def test_validate_github_harness_guidance_passes(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["commands"]): (
            "## Repository VCS Rule (Mandatory)\n"
            "- GitHub 기준으로 수행한다.\n"
            "- `gh auth status`\n"
            "- `python3 scripts/github_pr.py current`\n"
        ),
        str(module.REQUIRED_FILES["workflow"]): (
            "## Agent GitHub Preflight (Mandatory)\n"
            "python3 scripts/github_pr.py create --title test --body-file /tmp/pr.md\n"
        ),
        str(module.REQUIRED_FILES["agent_constraints"]): (
            "Before any GitHub issue/PR/comment operation, read docs first.\n"
            "Use scripts/github_pr.py for unattended PR operations.\n"
        ),
        str(module.REQUIRED_FILES["push_skill"]): (
            "python3 scripts/github_pr.py field --field html_url\n"
            "python3 scripts/validate_pr_body.py --body-file /tmp/pr_body.md\n"
            "pytest -v --cov=src --cov-report=term-missing\n"
        ),
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_github_harness_guidance(errors)
    assert errors == []


def test_validate_github_harness_guidance_reports_stale_tokens(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["commands"]): (
            "이 저장소의 티켓/PR/코멘트 작업은 Gitea 기준으로 수행한다.\n"
            "`gh`(GitHub CLI) 명령 사용은 금지한다.\n"
        ),
        str(module.REQUIRED_FILES["workflow"]): (
            "## Agent Gitea Preflight (Mandatory)\n"
            "`gh issue`, `gh pr` 등 GitHub CLI 명령은 사용 금지다.\n"
        ),
        str(module.REQUIRED_FILES["agent_constraints"]): (
            "Use `tea` for Gitea operations; do not use GitHub CLI (`gh`) "
            "in this repository workflow.\n"
        ),
        str(module.REQUIRED_FILES["push_skill"]): (
            "make -C elixir all\n"
            "mix pr_body.check --file /tmp/pr_body.md\n"
            ".github/pull_request_template.md\n"
        ),
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_github_harness_guidance(errors)
    assert any("docs/commands.md" in err and "GitHub 기준" in err for err in errors)
    assert any("docs/workflow.md" in err and "Agent GitHub Preflight" in err for err in errors)
    assert any(
        "docs/agent-constraints.md" in err and "scripts/github_pr.py" in err
        for err in errors
    )
    assert any(
        ".codex/skills/push/SKILL.md" in err and "make -C elixir all" in err
        for err in errors
    )


def test_validate_commands_endpoint_duplicates_reports_duplicates(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []
    text = "\n".join(
        [
            "| `GET /api/status` | status |",
            "| `GET /api/status` | duplicate |",
        ]
    )

    def fake_read(path: Path) -> str:
        assert path == module.REQUIRED_FILES["commands"]
        return text

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_commands_endpoint_duplicates(errors)
    assert errors
    assert "duplicated API endpoint row -> GET /api/status" in errors[0]


def test_validate_testing_doc_has_dynamic_count_guidance(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []

    def fake_read(path: Path) -> str:
        assert path == module.REQUIRED_FILES["testing"]
        return "Use pytest --collect-only -q for dynamic counts."

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_testing_doc_has_dynamic_count_guidance(errors)
    assert errors == []


def test_validate_pr_body_postcheck_guidance_passes(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["commands"]): (
            "PR Body Post-Check (Mandatory)\n"
            "python3 scripts/validate_pr_body.py --pr <PR_NUMBER>\n"
        ),
        str(module.REQUIRED_FILES["workflow"]): (
            "PR 생성 직후 본문 무결성 검증(필수)\n"
            "python3 scripts/validate_pr_body.py --pr <PR_NUMBER>\n"
        ),
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_pr_body_postcheck_guidance(errors)
    assert errors == []


def test_validate_pr_body_postcheck_guidance_reports_missing_tokens(
    monkeypatch,
) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["commands"]): "PR Body Post-Check (Mandatory)\n",
        str(module.REQUIRED_FILES["workflow"]): "PR Body Post-Check\n",
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_pr_body_postcheck_guidance(errors)
    assert any("commands.md" in err for err in errors)
    assert any("workflow.md" in err for err in errors)
