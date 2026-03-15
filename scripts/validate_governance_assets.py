#!/usr/bin/env python3
"""Validate persistent governance assets for agent workflow safety."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REQUIREMENTS_REGISTRY = "docs/ouroboros/01_requirements_registry.md"
TASK_WORK_ORDERS_DOC = "docs/ouroboros/30_code_level_work_orders.md"
TASK_DEF_LINE = re.compile(r"^-\s+`(?P<task_id>TASK-[A-Z0-9-]+-\d{3})`(?P<body>.*)$")
REQ_ID_IN_LINE = re.compile(r"\bREQ-[A-Z0-9-]+-\d{3}\b")
TASK_ID_IN_TEXT = re.compile(r"\bTASK-[A-Z0-9-]+-\d{3}\b")
TEST_ID_IN_TEXT = re.compile(r"\bTEST-[A-Z0-9-]+-\d{3}\b")
READ_ONLY_FILES = {"src/core/risk_manager.py"}
PLACEHOLDER_VALUES = {"", "tbd", "n/a", "na", "none", "<link>", "<required>"}
TIMEZONE_TOKEN_PATTERN = re.compile(r"\b(?:KST|UTC)\b")
KOREAN_COMMUNICATION_POLICY_TOKENS = (
    "## Korean Communication Policy (Mandatory)",
    "한글을 기본 언어로 사용한다.",
    "Linear workpad, 이슈 코멘트, PR 코멘트, 최종 보고의 서술 문장은 한글 위주로 작성한다.",
    "코드/명령어/경로/식별자는 원문 표기를 유지한다.",
)


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


def _git_revision_exists(revision: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


def load_changed_files(args: list[str], errors: list[str]) -> list[str]:
    if not args:
        return []

    # Single range input (e.g. BASE..HEAD or BASE...HEAD)
    if len(args) == 1 and ".." in args[0]:
        range_spec = args[0]
        if "..." in range_spec:
            lhs, rhs = range_spec.split("...", 1)
        else:
            lhs, rhs = range_spec.split("..", 1)
        if not lhs or not rhs:
            errors.append(f"invalid range spec: '{range_spec}'")
            return []
        # Force-push updates can reference an unreachable "before" SHA on CI runners.
        # In that case, skip changed-file narrowing and run full governance checks.
        if not _git_revision_exists(lhs) or not _git_revision_exists(rhs):
            return []
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


def validate_task_req_mapping(errors: list[str], *, task_doc: Path | None = None) -> None:
    path = task_doc or Path(TASK_WORK_ORDERS_DOC)
    if not path.exists():
        errors.append(f"missing file: {path}")
        return

    text = path.read_text(encoding="utf-8")
    found_task = False
    for line in text.splitlines():
        m = TASK_DEF_LINE.match(line.strip())
        if not m:
            continue
        found_task = True
        if not REQ_ID_IN_LINE.search(m.group("body")):
            errors.append(
                f"{path}: TASK without REQ mapping -> {m.group('task_id')}"
            )
    if not found_task:
        errors.append(f"{path}: no TASK definitions found")


def validate_task_test_pairing(errors: list[str], *, task_doc: Path | None = None) -> None:
    """Fail when TASK definitions are not linked to at least one TEST id."""
    path = task_doc or Path(TASK_WORK_ORDERS_DOC)
    if not path.exists():
        errors.append(f"missing file: {path}")
        return

    text = path.read_text(encoding="utf-8")
    found_task = False
    for line in text.splitlines():
        m = TASK_DEF_LINE.match(line.strip())
        if not m:
            continue
        found_task = True
        if not TEST_ID_IN_TEXT.search(m.group("body")):
            errors.append(f"{path}: TASK without TEST mapping -> {m.group('task_id')}")
    if not found_task:
        errors.append(f"{path}: no TASK definitions found")


def validate_timezone_policy_tokens(errors: list[str]) -> None:
    """Fail-fast check for REQ-OPS-001 governance tokens."""
    required_docs = [
        Path("docs/ouroboros/01_requirements_registry.md"),
        Path("docs/ouroboros/30_code_level_work_orders.md"),
        Path("docs/workflow.md"),
    ]
    for path in required_docs:
        if not path.exists():
            errors.append(f"missing file: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        if not TIMEZONE_TOKEN_PATTERN.search(text):
            errors.append(f"{path}: missing timezone policy token (KST/UTC)")


def validate_korean_communication_tokens(
    errors: list[str], *, workflow_doc: Path | None = None
) -> None:
    path = workflow_doc or Path("WORKFLOW.md")
    if not path.exists():
        errors.append(f"missing file: {path}")
        return

    text = path.read_text(encoding="utf-8")
    for token in KOREAN_COMMUNICATION_POLICY_TOKENS:
        if token not in text:
            errors.append(f"{path}: missing Korean communication policy token -> {token}")


def validate_pr_traceability(errors: list[str]) -> None:
    title = os.getenv("GOVERNANCE_PR_TITLE", "").strip()
    body = os.getenv("GOVERNANCE_PR_BODY", "").strip()
    if not title and not body:
        return

    text = f"{title}\n{body}"
    if not REQ_ID_IN_LINE.search(text):
        errors.append("PR text missing REQ-ID reference")
    if not TASK_ID_IN_TEXT.search(text):
        errors.append("PR text missing TASK-ID reference")
    if not TEST_ID_IN_TEXT.search(text):
        errors.append("PR text missing TEST-ID reference")


def _parse_pr_evidence_line(text: str, field: str) -> str | None:
    pattern = re.compile(rf"^\s*-\s*{re.escape(field)}:\s*(?P<value>.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None
    return match.group("value").strip()


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    return normalized in PLACEHOLDER_VALUES


def validate_read_only_approval(
    changed_files: list[str], errors: list[str], warnings: list[str]
) -> None:
    changed_set = set(changed_files)
    touched = sorted(path for path in READ_ONLY_FILES if path in changed_set)
    if not touched:
        return

    body = os.getenv("GOVERNANCE_PR_BODY", "").strip()
    if not body:
        errors.append(
            "READ-ONLY file changed but PR body is unavailable; approval evidence is required"
        )
        return

    if "READ-ONLY Approval" not in body:
        errors.append("READ-ONLY file changed without 'READ-ONLY Approval' section in PR body")
        return

    touched_field = _parse_pr_evidence_line(body, "Touched READ-ONLY files")
    human_approval = _parse_pr_evidence_line(body, "Human approval")
    test_suite_1 = _parse_pr_evidence_line(body, "Test suite 1")
    test_suite_2 = _parse_pr_evidence_line(body, "Test suite 2")

    if _is_placeholder(touched_field):
        errors.append("READ-ONLY Approval section missing 'Touched READ-ONLY files' evidence")
    if _is_placeholder(human_approval):
        errors.append("READ-ONLY Approval section missing 'Human approval' evidence")
    if _is_placeholder(test_suite_1):
        errors.append("READ-ONLY Approval section missing 'Test suite 1' evidence")
    if _is_placeholder(test_suite_2):
        errors.append("READ-ONLY Approval section missing 'Test suite 2' evidence")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    changed_files = load_changed_files(sys.argv[1:], errors)

    pr_template = Path(".github/pull_request_template.md")
    issue_template = Path(".gitea/ISSUE_TEMPLATE/runtime_verification.md")
    workflow_doc = Path("docs/workflow.md")
    commands_doc = Path("docs/commands.md")
    handover_script = Path("scripts/session_handover_check.py")
    handover_log = Path("workflow/session-handover.md")

    must_contain(
        pr_template,
        [
            "## Linked Issue",
            "## Scope",
            "## Summary",
            "## Validation",
            "## Risks",
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
            "Agent GitHub Preflight (Mandatory)",
            "gh auth status",
        ],
        errors,
    )
    must_contain(
        commands_doc,
        [
            "Session Handover Preflight (Mandatory)",
            "session_handover_check.py --strict",
            "GitHub CLI",
            "gh auth status",
            "gh pr status",
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
    validate_task_req_mapping(errors)
    validate_task_test_pairing(errors)
    validate_timezone_policy_tokens(errors)
    validate_korean_communication_tokens(errors)
    validate_pr_traceability(errors)
    validate_read_only_approval(changed_files, errors, warnings)

    if errors:
        print("[FAIL] governance asset validation failed")
        for err in errors:
            print(f"- {err}")
        return 1

    print("[OK] governance assets validated")
    if warnings:
        print(f"[WARN] governance advisory: {len(warnings)}")
        for warn in warnings:
            print(f"- {warn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
