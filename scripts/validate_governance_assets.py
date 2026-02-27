#!/usr/bin/env python3
"""Validate persistent governance assets for agent workflow safety."""

from __future__ import annotations

import sys
from pathlib import Path


def must_contain(path: Path, required: list[str], errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"missing file: {path}")
        return
    text = path.read_text(encoding="utf-8")
    for token in required:
        if token not in text:
            errors.append(f"{path}: missing required token -> {token}")


def main() -> int:
    errors: list[str] = []

    pr_template = Path(".gitea/PULL_REQUEST_TEMPLATE.md")
    issue_template = Path(".gitea/ISSUE_TEMPLATE/runtime_verification.md")

    must_contain(
        pr_template,
        [
            "Closes #N",
            "Main -> Verifier Directive Contract",
            "Coverage Matrix",
            "NOT_OBSERVED",
            "tea",
            "gh",
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

    if errors:
        print("[FAIL] governance asset validation failed")
        for err in errors:
            print(f"- {err}")
        return 1

    print("[OK] governance assets validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())

