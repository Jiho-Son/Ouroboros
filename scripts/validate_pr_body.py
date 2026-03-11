#!/usr/bin/env python3
"""Validate PR body formatting to prevent escaped-newline artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HEADER_PATTERN = re.compile(r"^##\s+\S+", re.MULTILINE)
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:-|\*|\d+\.)\s+\S+", re.MULTILINE)
FENCED_CODE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r"`[^`]*`")
REQ_ID_PATTERN = re.compile(r"\bREQ-[A-Z0-9-]+-\d{3}\b")
TASK_ID_PATTERN = re.compile(r"\bTASK-[A-Z0-9-]+-\d{3}\b")
TEST_ID_PATTERN = re.compile(r"\bTEST-[A-Z0-9-]+-\d{3}\b")


def _strip_code_segments(text: str) -> str:
    without_fences = FENCED_CODE_PATTERN.sub("", text)
    return INLINE_CODE_PATTERN.sub("", without_fences)


def _repo_from_origin() -> tuple[str, str]:
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if remote.startswith("git@github.com:"):
        slug = remote.removeprefix("git@github.com:")
    elif remote.startswith("https://github.com/"):
        slug = remote.removeprefix("https://github.com/")
    else:
        raise RuntimeError(f"unsupported origin remote for GitHub PR lookup: {remote}")
    owner, repo = slug.removesuffix(".git").split("/", 1)
    return owner, repo


def validate_pr_body_text(text: str, *, check_governance: bool = True) -> list[str]:
    errors: list[str] = []
    searchable = _strip_code_segments(text)
    if "\\n" in searchable:
        errors.append("body contains escaped newline sequence (\\n)")
    if text.count("```") % 2 != 0:
        errors.append("body has unbalanced fenced code blocks (``` count is odd)")
    if not HEADER_PATTERN.search(text):
        errors.append("body is missing markdown section headers (e.g. '## Summary')")
    if not LIST_ITEM_PATTERN.search(text):
        errors.append("body is missing markdown list items")
    if check_governance:
        # Check governance IDs against code-stripped text so IDs hidden in code
        # blocks or inline code are not counted (prevents spoof via code fences).
        if not REQ_ID_PATTERN.search(searchable):
            errors.append("body is missing REQ-ID traceability (e.g. REQ-OPS-001)")
        if not TASK_ID_PATTERN.search(searchable):
            errors.append("body is missing TASK-ID traceability (e.g. TASK-OPS-001)")
        if not TEST_ID_PATTERN.search(searchable):
            errors.append("body is missing TEST-ID traceability (e.g. TEST-OPS-001)")
    return errors


def fetch_pr_body(pr_number: int) -> str:
    owner, repo = _repo_from_origin()
    try:
        completed = subprocess.run(
            [
                "python3",
                "scripts/github_pr.py",
                "field",
                "--pr",
                str(pr_number),
                "--field",
                "body",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as exc:
        raise RuntimeError(f"failed to fetch PR #{pr_number} for {owner}/{repo}: {exc}") from exc
    return completed.stdout.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate PR body markdown formatting, escaped-newline artifacts, "
            "and governance traceability."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr", type=int, help="PR number to fetch via GitHub API helper")
    group.add_argument("--body-file", type=Path, help="Path to markdown body file")
    parser.add_argument(
        "--no-governance",
        action="store_true",
        help="Skip REQ-ID/TASK-ID/TEST-ID governance traceability checks",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.body_file is not None:
        if not args.body_file.exists():
            print(f"[FAIL] body file not found: {args.body_file}")
            return 1
        body = args.body_file.read_text(encoding="utf-8")
        source = f"file:{args.body_file}"
    else:
        body = fetch_pr_body(args.pr)
        source = f"pr:{args.pr}"

    errors = validate_pr_body_text(body, check_governance=not args.no_governance)
    if errors:
        print("[FAIL] PR body validation failed")
        print(f"- source: {source}")
        for err in errors:
            print(f"- {err}")
        return 1

    print("[OK] PR body validation passed")
    print(f"- source: {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
