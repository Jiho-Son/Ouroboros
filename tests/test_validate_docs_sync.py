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
    assert any("AGENTS.md" in err and ".codex/worktree_init.sh" in err for err in errors)


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


def test_validate_github_harness_guidance_passes(monkeypatch) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["commands"]): (
            "GitHub 기준\n"
            "gh auth status\n"
            "gh pr status\n"
        ),
        str(module.REQUIRED_FILES["workflow"]): (
            "## Agent GitHub Preflight (Mandatory)\n"
            "gh auth status\n"
            "gh pr status\n"
        ),
        str(module.REQUIRED_FILES["agent_constraints"]): (
            "GitHub issue/PR/comment operation\n"
            "Use `gh` for GitHub operations.\n"
        ),
        str(module.REQUIRED_FILES["push_skill"]): (
            ".github/pull_request_template.md\n"
            "gh pr create\n"
            "python3 scripts/validate_pr_body.py\n"
            "pytest -v --cov=src --cov-report=term-missing\n"
        ),
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_github_harness_guidance(errors)
    assert errors == []


def test_validate_github_harness_guidance_reports_stale_gitea_tokens(
    monkeypatch,
) -> None:
    module = _load_module()
    errors: list[str] = []
    fake_docs = {
        str(module.REQUIRED_FILES["commands"]): "tea Gitea",
        str(module.REQUIRED_FILES["workflow"]): "## Agent Gitea Preflight (Mandatory)",
        str(module.REQUIRED_FILES["agent_constraints"]): (
            "Use `tea` for Gitea operations; do not use GitHub CLI (`gh`) "
            "in this repository workflow."
        ),
        str(module.REQUIRED_FILES["push_skill"]): "mix pr_body.check\nmake -C elixir all",
    }

    def fake_read(path: Path) -> str:
        return fake_docs[str(path)]

    monkeypatch.setattr(module, "_read", fake_read)
    module.validate_github_harness_guidance(errors)
    assert any("commands.md" in err for err in errors)
    assert any("workflow.md" in err for err in errors)
    assert any("agent-constraints.md" in err for err in errors)
    assert any("SKILL.md" in err for err in errors)
