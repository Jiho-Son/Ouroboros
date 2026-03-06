#!/usr/bin/env python3
"""Validate Ouroboros planning docs for metadata, links, and ID consistency."""

from __future__ import annotations

import re
import sys
from pathlib import Path

DOC_DIR = Path("docs/ouroboros")
RUNTIME_DOC_PATHS = (
    Path("README.md"),
    Path("docs/commands.md"),
    Path("docs/skills.md"),
    Path("docs/live-trading-checklist.md"),
    Path("src/notifications/README.md"),
)
META_PATTERN = re.compile(
    r"<!--\n"
    r"Doc-ID: (?P<doc_id>[^\n]+)\n"
    r"Version: (?P<version>[^\n]+)\n"
    r"Status: (?P<status>[^\n]+)\n"
    r"Owner: (?P<owner>[^\n]+)\n"
    r"Updated: (?P<updated>\d{4}-\d{2}-\d{2})\n"
    r"-->",
    re.MULTILINE,
)
ID_PATTERN = re.compile(r"\b(?:REQ|RULE|TASK|TEST|DOC)-[A-Z0-9-]+-\d{3}\b")
DEF_PATTERN = re.compile(
    r"^-\s+`(?P<id>(?:REQ|RULE|TASK|TEST|DOC)-[A-Z0-9-]+-\d{3})`",
    re.MULTILINE,
)
LINK_PATTERN = re.compile(r"\[[^\]]+\]\((?P<link>[^)]+)\)")
LINE_DEF_PATTERN = re.compile(
    r"^-\s+`(?P<id>(?:REQ|RULE|TASK|TEST|DOC)-[A-Z0-9-]+-\d{3})`.*$",
    re.MULTILINE,
)
PLAN_LINK_PATTERN = re.compile(r"ouroboros_plan_v(?P<version>[23])\.txt$")
ALLOWED_PLAN_TARGETS = {
    "2": (DOC_DIR / "source" / "ouroboros_plan_v2.txt").resolve(),
    "3": (DOC_DIR / "source" / "ouroboros_plan_v3.txt").resolve(),
}
ISSUE_REF_PATTERN = re.compile(r"#(?P<issue>\d+)")
ISSUE_DONE_PATTERN = re.compile(r"(?:✅|머지|해소|완료)")
ISSUE_PENDING_PATTERN = re.compile(r"(?:잔여|오픈 상태|추적 이슈)")
FORBIDDEN_RUNTIME_PAPER_COMMAND_PATTERN = re.compile(
    r"(?P<cmd>(?:[A-Z_][A-Z0-9_]*=[^\s`]+\s+)*python\s+-m\s+src\.main\b[^\n`]*--mode=paper\b)"
)
RUNTIME_PAPER_ALLOWLIST_HINTS = (
    "banned",
    "금지",
    "do not run",
    "실행 금지",
)


def iter_docs() -> list[Path]:
    return sorted([p for p in DOC_DIR.glob("*.md") if p.is_file()])


def validate_metadata(path: Path, text: str, errors: list[str], doc_ids: dict[str, Path]) -> None:
    match = META_PATTERN.search(text)
    if not match:
        errors.append(f"{path}: missing or malformed metadata block")
        return
    doc_id = match.group("doc_id").strip()
    if doc_id in doc_ids:
        errors.append(f"{path}: duplicate Doc-ID {doc_id} (already in {doc_ids[doc_id]})")
    else:
        doc_ids[doc_id] = path


def validate_plan_source_link(path: Path, link: str, errors: list[str]) -> bool:
    normalized = link.strip()
    # Ignore in-page anchors and parse the filesystem part for validation.
    link_path = normalized.split("#", 1)[0].strip()
    if not link_path:
        return False
    match = PLAN_LINK_PATTERN.search(link_path)
    if not match:
        return False

    version = match.group("version")
    expected_target = ALLOWED_PLAN_TARGETS[version]
    if link_path.startswith("/"):
        errors.append(
            f"{path}: invalid plan link path -> {link} "
            f"(use ./source/ouroboros_plan_v{version}.txt)"
        )
        return True

    resolved_target = (path.parent / link_path).resolve()
    if resolved_target != expected_target:
        errors.append(
            f"{path}: invalid plan link path -> {link} "
            f"(must resolve to docs/ouroboros/source/ouroboros_plan_v{version}.txt)"
        )
        return True
    return False


def validate_links(path: Path, text: str, errors: list[str]) -> None:
    for m in LINK_PATTERN.finditer(text):
        link = m.group("link").strip()
        if not link or link.startswith("http") or link.startswith("#"):
            continue
        if validate_plan_source_link(path, link, errors):
            continue
        link_path = link.split("#", 1)[0].strip()
        if link_path.startswith("/"):
            target = Path(link_path)
        else:
            target = (path.parent / link_path).resolve()
        if not target.exists():
            errors.append(f"{path}: broken link -> {link}")


def collect_ids(path: Path, text: str, defs: dict[str, Path], refs: dict[str, set[Path]]) -> None:
    for m in DEF_PATTERN.finditer(text):
        defs[m.group("id")] = path
    for m in ID_PATTERN.finditer(text):
        idv = m.group(0)
        refs.setdefault(idv, set()).add(path)


def collect_req_traceability(
    text: str, req_to_task: dict[str, set[str]], req_to_test: dict[str, set[str]]
) -> None:
    for m in LINE_DEF_PATTERN.finditer(text):
        line = m.group(0)
        item_id = m.group("id")
        req_ids = [rid for rid in ID_PATTERN.findall(line) if rid.startswith("REQ-")]
        if item_id.startswith("TASK-"):
            for req_id in req_ids:
                req_to_task.setdefault(req_id, set()).add(item_id)
        if item_id.startswith("TEST-"):
            for req_id in req_ids:
                req_to_test.setdefault(req_id, set()).add(item_id)


def validate_issue_status_consistency(path: Path, text: str, errors: list[str]) -> None:
    issue_done_lines: dict[str, list[int]] = {}
    issue_pending_lines: dict[str, list[int]] = {}

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        issue_ids = [m.group("issue") for m in ISSUE_REF_PATTERN.finditer(line)]
        if not issue_ids:
            continue

        is_pending = bool(ISSUE_PENDING_PATTERN.search(line))
        is_done = bool(ISSUE_DONE_PATTERN.search(line)) and not is_pending
        if not is_pending and not is_done:
            continue

        for issue_id in issue_ids:
            if is_done:
                issue_done_lines.setdefault(issue_id, []).append(line_no)
            if is_pending:
                issue_pending_lines.setdefault(issue_id, []).append(line_no)

    conflicted_issues = sorted(set(issue_done_lines) & set(issue_pending_lines))
    for issue_id in conflicted_issues:
        errors.append(
            f"{path}: conflicting status for issue #{issue_id} "
            f"(done at lines {issue_done_lines[issue_id]}, "
            f"pending at lines {issue_pending_lines[issue_id]})"
        )


def validate_forbidden_runtime_paper_commands(path: Path, text: str, errors: list[str]) -> None:
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if "--mode=paper" not in line or "python -m src.main" not in line:
            continue
        lowered = line.lower()
        if any(hint in lowered for hint in RUNTIME_PAPER_ALLOWLIST_HINTS):
            continue
        match = FORBIDDEN_RUNTIME_PAPER_COMMAND_PATTERN.search(line)
        if match is None:
            continue
        errors.append(
            f"{path}:{line_no}: forbidden runtime paper command example -> {match.group('cmd')}"
        )


def main() -> int:
    if not DOC_DIR.exists():
        print(f"ERROR: missing directory {DOC_DIR}")
        return 1

    docs = iter_docs()
    if not docs:
        print(f"ERROR: no markdown docs found in {DOC_DIR}")
        return 1

    errors: list[str] = []
    doc_ids: dict[str, Path] = {}
    defs: dict[str, Path] = {}
    refs: dict[str, set[Path]] = {}
    req_to_task: dict[str, set[str]] = {}
    req_to_test: dict[str, set[str]] = {}

    for path in docs:
        text = path.read_text(encoding="utf-8")
        validate_metadata(path, text, errors, doc_ids)
        validate_links(path, text, errors)
        if path.name == "80_implementation_audit.md":
            validate_issue_status_consistency(path, text, errors)
        collect_ids(path, text, defs, refs)
        collect_req_traceability(text, req_to_task, req_to_test)

    for path in RUNTIME_DOC_PATHS:
        if not path.exists():
            errors.append(f"missing runtime doc for paper-ban validation: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        validate_forbidden_runtime_paper_commands(path, text, errors)

    for idv, where_used in sorted(refs.items()):
        if idv.startswith("DOC-"):
            continue
        if idv not in defs:
            files = ", ".join(str(p) for p in sorted(where_used))
            errors.append(f"undefined ID {idv}, used in: {files}")

    for idv in sorted(defs):
        if not idv.startswith("REQ-"):
            continue
        if idv not in req_to_task:
            errors.append(f"REQ without TASK mapping: {idv}")
        if idv not in req_to_test:
            errors.append(f"REQ without TEST mapping: {idv}")

    warnings: list[str] = []
    for idv, where_def in sorted(defs.items()):
        if len(refs.get(idv, set())) <= 1 and (idv.startswith("REQ-") or idv.startswith("RULE-")):
            warnings.append(f"orphan ID {idv} defined in {where_def} (not referenced elsewhere)")

    if errors:
        print("[FAIL] Ouroboros docs validation failed")
        for err in errors:
            print(f"- {err}")
        return 1

    print(f"[OK] validated {len(docs)} docs in {DOC_DIR}")
    print(f"[OK] unique Doc-ID: {len(doc_ids)}")
    print(f"[OK] definitions: {len(defs)}, references: {len(refs)}")
    print(f"[OK] req->task mappings: {len(req_to_task)}")
    print(f"[OK] req->test mappings: {len(req_to_test)}")
    if warnings:
        print(f"[WARN] orphan IDs: {len(warnings)}")
        for w in warnings:
            print(f"- {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
