#!/usr/bin/env python3
"""Minimal GitHub pull request helper for unattended runs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request


def _require_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GH_TOKEN or GITHUB_TOKEN is required")
    return token


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repo_from_origin() -> tuple[str, str]:
    remote = _git("remote", "get-url", "origin")
    if remote.startswith("git@github.com:"):
        slug = remote.removeprefix("git@github.com:")
    elif remote.startswith("https://github.com/"):
        slug = remote.removeprefix("https://github.com/")
    else:
        raise RuntimeError(f"unsupported origin remote for GitHub API helper: {remote}")
    owner, repo = slug.removesuffix(".git").split("/", 1)
    return owner, repo


def _api_request(
    method: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    token = _require_token()
    url = f"https://api.github.com{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc
    return json.loads(body) if body else None


def _current_branch() -> str:
    return _git("branch", "--show-current")


def _pr_for_branch(*, state: str = "all") -> dict[str, Any] | None:
    owner, repo = _repo_from_origin()
    branch = _current_branch()
    pulls = _api_request(
        "GET",
        f"/repos/{owner}/{repo}/pulls",
        query={"head": f"{owner}:{branch}", "state": state},
    )
    if not pulls:
        return None
    return pulls[0]


def cmd_current(_: argparse.Namespace) -> int:
    pr = _pr_for_branch()
    if pr is None:
        return 1
    print(json.dumps(pr))
    return 0


def cmd_field(args: argparse.Namespace) -> int:
    if args.pr is None:
        pr = _pr_for_branch()
        if pr is None:
            return 1
    else:
        owner, repo = _repo_from_origin()
        pr = _api_request("GET", f"/repos/{owner}/{repo}/pulls/{args.pr}")
    value: Any = pr
    for segment in args.field.split("."):
        value = value[segment]
    if isinstance(value, (dict, list)):
        print(json.dumps(value))
    else:
        print(value)
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    owner, repo = _repo_from_origin()
    body = Path(args.body_file).read_text(encoding="utf-8")
    pr = _api_request(
        "POST",
        f"/repos/{owner}/{repo}/pulls",
        payload={
            "title": args.title,
            "head": args.head or _current_branch(),
            "base": args.base,
            "body": body,
        },
    )
    print(json.dumps(pr))
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    owner, repo = _repo_from_origin()
    payload: dict[str, Any] = {}
    if args.title is not None:
        payload["title"] = args.title
    if args.body_file is not None:
        payload["body"] = Path(args.body_file).read_text(encoding="utf-8")
    pr = _api_request("PATCH", f"/repos/{owner}/{repo}/pulls/{args.pr}", payload=payload)
    print(json.dumps(pr))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub pull request helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    current = subparsers.add_parser("current", help="Print current branch PR JSON")
    current.set_defaults(func=cmd_current)

    field = subparsers.add_parser("field", help="Print one PR field")
    field.add_argument("--field", required=True)
    field.add_argument("--pr", type=int)
    field.set_defaults(func=cmd_field)

    create = subparsers.add_parser("create", help="Create a PR")
    create.add_argument("--title", required=True)
    create.add_argument("--body-file", required=True)
    create.add_argument("--base", default="main")
    create.add_argument("--head")
    create.set_defaults(func=cmd_create)

    edit = subparsers.add_parser("edit", help="Edit an existing PR")
    edit.add_argument("--pr", type=int, required=True)
    edit.add_argument("--title")
    edit.add_argument("--body-file")
    edit.set_defaults(func=cmd_edit)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
