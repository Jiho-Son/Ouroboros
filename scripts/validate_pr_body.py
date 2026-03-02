#!/usr/bin/env python3
"""Validate PR body formatting to prevent escaped-newline artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HEADER_PATTERN = re.compile(r"^##\s+\S+", re.MULTILINE)
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:-|\*|\d+\.)\s+\S+", re.MULTILINE)


def validate_pr_body_text(text: str) -> list[str]:
    errors: list[str] = []
    if "\\n" in text and "\n" not in text:
        errors.append("body contains escaped newline sequence (\\n)")
    if text.count("```") % 2 != 0:
        errors.append("body has unbalanced fenced code blocks (``` count is odd)")
    if not HEADER_PATTERN.search(text):
        errors.append("body is missing markdown section headers (e.g. '## Summary')")
    if not LIST_ITEM_PATTERN.search(text):
        errors.append("body is missing markdown list items")
    return errors


def fetch_pr_body(pr_number: int) -> str:
    try:
        completed = subprocess.run(
            [
                "tea",
                "api",
                "-R",
                "origin",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(f"failed to fetch PR #{pr_number}: {exc}") from exc

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse PR payload for #{pr_number}: {exc}") from exc

    body = payload.get("body", "")
    if not isinstance(body, str):
        raise RuntimeError(f"unexpected PR body type for #{pr_number}: {type(body).__name__}")
    return body


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate PR body markdown formatting and escaped-newline artifacts."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr", type=int, help="PR number to fetch via `tea api`")
    group.add_argument("--body-file", type=Path, help="Path to markdown body file")
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

    errors = validate_pr_body_text(body)
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
