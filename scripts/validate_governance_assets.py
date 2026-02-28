#!/usr/bin/env python3
"""Validate persistent governance assets for agent workflow safety."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REQUIREMENTS_REGISTRY = "docs/ouroboros/01_requirements_registry.md"


def must_contain(path: Path, required: list[str], errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"missing file: {path}")
        return
    text = path.read_text(encoding="utf-8")
    for token in required:
        if token not in text:
            errors.append(f"{path}: missing required token -> {token}")


def normalize_changed_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_policy_file(path: str) -> bool:
    normalized = normalize_changed_path(path)
    if not normalized.endswith(".md"):
        return False
    if not normalized.startswith("docs/ouroboros/"):
        return False
    return normalized != REQUIREMENTS_REGISTRY


def load_changed_files(args: list[str], errors: list[str]) -> list[str]:
    if not args:
        return []

    # Single range input (e.g. BASE..HEAD or BASE...HEAD)
    if len(args) == 1 and ".." in args[0]:
        range_spec = args[0]
        try:
            completed = subprocess.run(
                ["git", "diff", "--name-only", range_spec],
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            errors.append(f"failed to load changed files from range '{range_spec}': {exc}")
            return []
        return [
            normalize_changed_path(line)
            for line in completed.stdout.splitlines()
            if line.strip()
        ]

    return [normalize_changed_path(path) for path in args if path.strip()]


def validate_registry_sync(changed_files: list[str], errors: list[str]) -> None:
    if not changed_files:
        return

    changed_set = set(changed_files)
    policy_changed = any(is_policy_file(path) for path in changed_set)
    registry_changed = REQUIREMENTS_REGISTRY in changed_set
    if policy_changed and not registry_changed:
        errors.append(
            "policy file changed without updating docs/ouroboros/01_requirements_registry.md"
        )


def main() -> int:
    errors: list[str] = []
    changed_files = load_changed_files(sys.argv[1:], errors)

    pr_template = Path(".gitea/PULL_REQUEST_TEMPLATE.md")
    issue_template = Path(".gitea/ISSUE_TEMPLATE/runtime_verification.md")
    workflow_doc = Path("docs/workflow.md")
    commands_doc = Path("docs/commands.md")
    handover_script = Path("scripts/session_handover_check.py")
    handover_log = Path("workflow/session-handover.md")

    must_contain(
        pr_template,
        [
            "Closes #N",
            "Main -> Verifier Directive Contract",
            "Coverage Matrix",
            "NOT_OBSERVED",
            "tea",
            "gh",
            "Session Handover Gate",
            "session_handover_check.py --strict",
        ],
        errors,
    )
    must_contain(
        issue_template,
        [
            "[RUNTIME-VERIFY][SCN-XXX]",
            "Requirement Mapping",
            "Close Criteria",
            "NOT_OBSERVED = 0",
        ],
        errors,
    )
    must_contain(
        workflow_doc,
        [
            "Session Handover Gate (Mandatory)",
            "session_handover_check.py --strict",
        ],
        errors,
    )
    must_contain(
        commands_doc,
        [
            "Session Handover Preflight (Mandatory)",
            "session_handover_check.py --strict",
        ],
        errors,
    )
    must_contain(
        handover_log,
        [
            "Session Handover Log",
            "- branch:",
            "- docs_checked:",
            "- open_issues_reviewed:",
            "- next_ticket:",
        ],
        errors,
    )
    if not handover_script.exists():
        errors.append(f"missing file: {handover_script}")

    validate_registry_sync(changed_files, errors)

    if errors:
        print("[FAIL] governance asset validation failed")
        for err in errors:
            print(f"- {err}")
        return 1

    print("[OK] governance assets validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
