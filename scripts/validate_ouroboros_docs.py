#!/usr/bin/env python3
"""Validate Ouroboros planning docs for metadata, links, and ID consistency."""

from __future__ import annotations

import re
import sys
from pathlib import Path

DOC_DIR = Path("docs/ouroboros")
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
        collect_ids(path, text, defs, refs)
        collect_req_traceability(text, req_to_task, req_to_test)

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
