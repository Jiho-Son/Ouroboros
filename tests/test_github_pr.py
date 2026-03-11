from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "github_pr.py"
    spec = importlib.util.spec_from_file_location("github_pr", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_require_token_prefers_gh_token(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    assert module._require_token() == "gh-token"


def test_require_token_falls_back_to_github_token(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    assert module._require_token() == "github-token"


def test_require_token_raises_without_token(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        module._require_token()


def test_repo_from_origin_supports_https(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_git", lambda *args: "https://github.com/Jiho-Son/Ouroboros.git")
    assert module._repo_from_origin() == ("Jiho-Son", "Ouroboros")


def test_repo_from_origin_supports_ssh(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_git", lambda *args: "git@github.com:Jiho-Son/Ouroboros.git")
    assert module._repo_from_origin() == ("Jiho-Son", "Ouroboros")


def test_pr_for_branch_queries_current_branch(monkeypatch) -> None:
    module = _load_module()
    calls: list[tuple[str, str, dict[str, str] | None]] = []

    def fake_api_request(method, path, *, query=None, payload=None):  # noqa: ANN001
        calls.append((method, path, query))
        assert payload is None
        return [{"number": 809, "state": "open"}]

    monkeypatch.setattr(module, "_repo_from_origin", lambda: ("Jiho-Son", "Ouroboros"))
    monkeypatch.setattr(
        module,
        "_current_branch",
        lambda: "feature/issue-809-harness-engineering-rework",
    )
    monkeypatch.setattr(module, "_api_request", fake_api_request)

    pr = module._pr_for_branch()

    assert pr == {"number": 809, "state": "open"}
    assert calls == [
        (
            "GET",
            "/repos/Jiho-Son/Ouroboros/pulls",
            {
                "head": "Jiho-Son:feature/issue-809-harness-engineering-rework",
                "state": "all",
            },
        )
    ]
