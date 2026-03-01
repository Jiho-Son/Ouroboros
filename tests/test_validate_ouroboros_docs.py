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

    module.validate_plan_source_link(path, "./source/ouroboros_plan_v2.txt", errors)
    module.validate_plan_source_link(path, "./source/ouroboros_plan_v3.txt", errors)

    assert errors == []


def test_validate_plan_source_link_rejects_root_relative_path() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/README.md").resolve()

    module.validate_plan_source_link(
        path,
        "/home/agentson/repos/The-Ouroboros/ouroboros_plan_v2.txt",
        errors,
    )

    assert errors
    assert "invalid plan link path" in errors[0]
    assert "use ./source/ouroboros_plan_v2.txt" in errors[0]


def test_validate_plan_source_link_rejects_repo_root_relative_path() -> None:
    module = _load_module()
    errors: list[str] = []
    path = Path("docs/ouroboros/README.md").resolve()

    module.validate_plan_source_link(path, "../../ouroboros_plan_v2.txt", errors)

    assert errors
    assert "invalid plan link path" in errors[0]
    assert "must resolve to docs/ouroboros/source/ouroboros_plan_v2.txt" in errors[0]
