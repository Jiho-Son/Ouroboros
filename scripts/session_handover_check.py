#!/usr/bin/env python3
"""Session handover preflight gate.

This script enforces a minimal handover record per working branch so that
new sessions cannot start implementation without reading the required docs
and recording current intent.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REQUIRED_DOCS = (
    Path("docs/workflow.md"),
    Path("docs/commands.md"),
    Path("docs/agent-constraints.md"),
)
HANDOVER_LOG = Path("workflow/session-handover.md")


def _run_git(*args: str) -> str:
    try:
        return (
            subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return ""


def _current_branch() -> str:
    branch = _run_git("branch", "--show-current")
    if branch:
        return branch
    return _run_git("rev-parse", "--abbrev-ref", "HEAD")


def _latest_entry(text: str) -> str:
    chunks = text.split("\n### ")
    if not chunks:
        return ""
    if chunks[0].startswith("### "):
        chunks[0] = chunks[0][4:]
    latest = chunks[-1].strip()
    if not latest:
        return ""
    if not latest.startswith("### "):
        latest = f"### {latest}"
    return latest


def _check_required_files(errors: list[str]) -> None:
    for path in REQUIRED_DOCS:
        if not path.exists():
            errors.append(f"missing required document: {path}")
    if not HANDOVER_LOG.exists():
        errors.append(f"missing handover log: {HANDOVER_LOG}")


def _check_handover_entry(
    *,
    branch: str,
    strict: bool,
    errors: list[str],
) -> None:
    if not HANDOVER_LOG.exists():
        return
    text = HANDOVER_LOG.read_text(encoding="utf-8")
    latest = _latest_entry(text)
    if not latest:
        errors.append("handover log has no session entry")
        return

    required_tokens = (
        "- branch:",
        "- docs_checked:",
        "- open_issues_reviewed:",
        "- next_ticket:",
    )
    for token in required_tokens:
        if token not in latest:
            errors.append(f"latest handover entry missing token: {token}")

    if strict:
        today_utc = datetime.now(UTC).date().isoformat()
        if today_utc not in latest:
            errors.append(
                f"latest handover entry must contain today's UTC date ({today_utc})"
            )
        branch_token = f"- branch: {branch}"
        if branch_token not in latest:
            errors.append(
                "latest handover entry must target current branch "
                f"({branch_token})"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate session handover gate requirements."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enforce today-date and current-branch match on latest handover entry.",
    )
    args = parser.parse_args()

    errors: list[str] = []
    _check_required_files(errors)

    branch = _current_branch()
    if not branch:
        errors.append("cannot resolve current git branch")
    elif branch in {"main", "master"}:
        errors.append(f"working branch must not be {branch}")

    _check_handover_entry(branch=branch, strict=args.strict, errors=errors)

    if errors:
        print("[FAIL] session handover check failed")
        for err in errors:
            print(f"- {err}")
        return 1

    print("[OK] session handover check passed")
    print(f"[OK] branch={branch}")
    print(f"[OK] handover_log={HANDOVER_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
