#!/usr/bin/env python3
"""Validate top-level docs synchronization invariants."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(".")
REQUIRED_FILES = {
    "README.md": REPO_ROOT / "README.md",
    "CLAUDE.md": REPO_ROOT / "CLAUDE.md",
    "commands": REPO_ROOT / "docs" / "commands.md",
    "testing": REPO_ROOT / "docs" / "testing.md",
    "workflow": REPO_ROOT / "docs" / "workflow.md",
}

LINK_PATTERN = re.compile(r"\[[^\]]+\]\((?P<link>[^)]+)\)")
ENDPOINT_ROW_PATTERN = re.compile(
    r"^\|\s*`(?P<endpoint>(?:GET|POST|PUT|PATCH|DELETE)\s+/[^`]*)`\s*\|"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def validate_required_files_exist(errors: list[str]) -> None:
    for name, path in REQUIRED_FILES.items():
        if not path.exists():
            errors.append(f"missing required doc file ({name}): {path}")


def validate_links_resolve(doc_path: Path, text: str, errors: list[str]) -> None:
    for match in LINK_PATTERN.finditer(text):
        raw_link = match.group("link").strip()
        if not raw_link or raw_link.startswith("#") or raw_link.startswith("http"):
            continue
        link_path = raw_link.split("#", 1)[0].strip()
        if not link_path:
            continue
        if link_path.startswith("/"):
            errors.append(f"{doc_path}: absolute link is forbidden -> {raw_link}")
            continue
        target = (doc_path.parent / link_path).resolve()
        if not target.exists():
            errors.append(f"{doc_path}: broken link -> {raw_link}")


def validate_summary_docs_reference_core_docs(errors: list[str]) -> None:
    required_links = {
        "README.md": ("docs/workflow.md", "docs/commands.md", "docs/testing.md"),
        "CLAUDE.md": ("docs/workflow.md", "docs/commands.md"),
    }
    for file_name, links in required_links.items():
        doc_path = REQUIRED_FILES[file_name]
        text = _read(doc_path)
        for link in links:
            if link not in text:
                errors.append(f"{doc_path}: missing core doc link reference -> {link}")


def collect_command_endpoints(text: str) -> list[str]:
    endpoints: list[str] = []
    for line in text.splitlines():
        match = ENDPOINT_ROW_PATTERN.match(line.strip())
        if match:
            endpoints.append(match.group("endpoint"))
    return endpoints


def validate_commands_endpoint_duplicates(errors: list[str]) -> None:
    text = _read(REQUIRED_FILES["commands"])
    endpoints = collect_command_endpoints(text)
    seen: set[str] = set()
    duplicates: set[str] = set()
    for endpoint in endpoints:
        if endpoint in seen:
            duplicates.add(endpoint)
        seen.add(endpoint)
    for endpoint in sorted(duplicates):
        errors.append(f"docs/commands.md: duplicated API endpoint row -> {endpoint}")


def validate_testing_doc_has_dynamic_count_guidance(errors: list[str]) -> None:
    text = _read(REQUIRED_FILES["testing"])
    if "pytest --collect-only -q" not in text:
        errors.append(
            "docs/testing.md: missing dynamic test count guidance "
            "(pytest --collect-only -q)"
        )


def validate_pr_body_postcheck_guidance(errors: list[str]) -> None:
    required_tokens = {
        "commands": (
            "PR Body Post-Check (Mandatory)",
            "python3 scripts/validate_pr_body.py --pr <PR_NUMBER>",
        ),
        "workflow": (
            "PR 생성 직후 본문 무결성 검증(필수)",
            "python3 scripts/validate_pr_body.py --pr <PR_NUMBER>",
        ),
    }
    for key, tokens in required_tokens.items():
        path = REQUIRED_FILES[key]
        text = _read(path)
        for token in tokens:
            if token not in text:
                errors.append(f"{path}: missing PR body post-check guidance token -> {token}")


def main() -> int:
    errors: list[str] = []

    validate_required_files_exist(errors)
    if errors:
        print("[FAIL] docs sync validation failed")
        for err in errors:
            print(f"- {err}")
        return 1

    readme_text = _read(REQUIRED_FILES["README.md"])
    claude_text = _read(REQUIRED_FILES["CLAUDE.md"])
    validate_links_resolve(REQUIRED_FILES["README.md"], readme_text, errors)
    validate_links_resolve(REQUIRED_FILES["CLAUDE.md"], claude_text, errors)
    validate_links_resolve(
        REQUIRED_FILES["commands"], _read(REQUIRED_FILES["commands"]), errors
    )
    validate_links_resolve(REQUIRED_FILES["testing"], _read(REQUIRED_FILES["testing"]), errors)
    validate_links_resolve(
        REQUIRED_FILES["workflow"], _read(REQUIRED_FILES["workflow"]), errors
    )

    validate_summary_docs_reference_core_docs(errors)
    validate_commands_endpoint_duplicates(errors)
    validate_testing_doc_has_dynamic_count_guidance(errors)
    validate_pr_body_postcheck_guidance(errors)

    if errors:
        print("[FAIL] docs sync validation failed")
        for err in errors:
            print(f"- {err}")
        return 1

    print("[OK] docs sync validated")
    print("[OK] summary docs link to core docs and links resolve")
    print("[OK] commands endpoint rows have no duplicates")
    print("[OK] testing doc includes dynamic count guidance")
    print("[OK] PR body post-check guidance exists in commands/workflow docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
