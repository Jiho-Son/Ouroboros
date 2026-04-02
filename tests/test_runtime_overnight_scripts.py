from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_TEMPLATE = REPO_ROOT / "WORKFLOW.md"
RUN_OVERNIGHT = REPO_ROOT / "scripts" / "run_overnight.sh"
STOP_OVERNIGHT = REPO_ROOT / "scripts" / "stop_overnight.sh"
RUNTIME_MONITOR = REPO_ROOT / "scripts" / "runtime_verify_monitor.sh"
RUNTIME_INSTANCE_ENV = REPO_ROOT / "scripts" / "runtime_instance_env.sh"
BASH = Path(shutil.which("bash") or "/bin/bash")
SYMPHONY_BEFORE_REMOVE_CANONICAL_RESTART = (
    REPO_ROOT / "scripts" / "symphony_before_remove_canonical_restart.sh"
)


def _latest_runtime_log(log_dir: Path) -> str:
    logs = sorted(log_dir.glob("runtime_verify_*.log"))
    assert logs, "runtime monitor did not produce log output"
    return logs[-1].read_text(encoding="utf-8")


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _runtime_monitor_helper_functions() -> str:
    lines = RUNTIME_MONITOR.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("log() {"))
    end = next(i for i, line in enumerate(lines) if line.startswith("check_forbidden() {"))
    return "\n".join(lines[start:end]) + "\n"


def _resolve_runtime_defaults(*, state_root: Path, branch: str) -> dict[str, str]:
    completed = subprocess.run(
        [
            str(BASH),
            "-lc",
            (
                f'source "{RUNTIME_INSTANCE_ENV}"; '
                "runtime_resolve_defaults; "
                'printf "ROOT_DIR=%s\nLOG_DIR=%s\nDASHBOARD_PORT=%s\n'
                'TMUX_SESSION_PREFIX=%s\nLIVE_RUNTIME_LOCK_PATH=%s\n'
                'RUNTIME_BRANCH_NAME_RESOLVED=%s\n" '
                '"$ROOT_DIR" "$LOG_DIR" "$DASHBOARD_PORT" '
                '"$TMUX_SESSION_PREFIX" "$LIVE_RUNTIME_LOCK_PATH" '
                '"$RUNTIME_BRANCH_NAME_RESOLVED"'
            ),
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "RUNTIME_BRANCH_NAME": branch,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    pairs = [line.split("=", 1) for line in completed.stdout.strip().splitlines()]
    return {key: value for key, value in pairs}


def _workflow_before_remove_command() -> str:
    command_lines: list[str] = []
    in_hooks = False
    in_before_remove = False

    for raw_line in WORKFLOW_TEMPLATE.read_text(encoding="utf-8").splitlines():
        if raw_line == "hooks:":
            in_hooks = True
            continue
        if not in_hooks:
            continue
        if raw_line and not raw_line.startswith("  "):
            break
        if raw_line == "  before_remove: |":
            in_before_remove = True
            continue
        if not in_before_remove:
            continue
        if raw_line.startswith("    "):
            command_lines.append(raw_line[4:])
            continue
        if not raw_line.strip():
            command_lines.append("")
            continue
        break

    command = "\n".join(command_lines).strip()
    if not command:
        raise AssertionError(
            "before_remove command not found in WORKFLOW.md -- check front matter indentation"
        )
    return command


def _write_fake_git(*, tmp_path: Path) -> Path:
    fake_git = tmp_path / "fake_git.py"
    fake_git.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path


def log(message: str) -> None:
    log_path = os.environ.get("FAKE_GIT_LOG_PATH")
    if not log_path:
        return
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\\n")


def is_under(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def parse(argv: list[str]) -> tuple[Path, list[str]]:
    cwd = Path(os.getcwd())
    args = list(argv)
    while args[:1] == ["-C"]:
        cwd = Path(args[1])
        args = args[2:]
    return cwd, args


def main() -> int:
    cwd, args = parse(sys.argv[1:])
    workspace_root = Path(os.environ["FAKE_GIT_WORKSPACE_ROOT"])
    canonical_root = Path(os.environ["FAKE_GIT_CANONICAL_ROOT"])
    bare_root = Path(os.environ.get("FAKE_GIT_BARE_ROOT", workspace_root / "bare-root"))
    workspace_branch = os.environ["FAKE_GIT_WORKSPACE_BRANCH"]
    workspace_sha = os.environ["FAKE_GIT_WORKSPACE_SHA"]
    canonical_branch = os.environ.get("FAKE_GIT_CANONICAL_BRANCH", "main")
    canonical_head = os.environ.get("FAKE_GIT_CANONICAL_HEAD", "canonical-head")
    target_sha = os.environ["FAKE_GIT_TARGET_SHA"]
    merged_by_git = os.environ.get("FAKE_GIT_MERGED_BY_GIT", "false") == "true"
    include_bare_entry = os.environ.get("FAKE_GIT_INCLUDE_BARE_ENTRY", "false") == "true"
    emit_stray_main_branch_after_bare = (
        os.environ.get("FAKE_GIT_EMIT_STRAY_MAIN_BRANCH_AFTER_BARE", "false")
        == "true"
    )
    remote_url = os.environ.get(
        "FAKE_GIT_REMOTE_URL",
        "https://github.com/test-owner/test-repo.git",
    )
    pull_fails = os.environ.get("FAKE_GIT_PULL_FAILS", "false") == "true"
    fetch_noise = os.environ.get("FAKE_GIT_FETCH_NOISE", "false") == "true"

    if args == ["branch", "--show-current"]:
        if is_under(workspace_root, cwd):
            print(workspace_branch)
            return 0
        if is_under(canonical_root, cwd):
            print(canonical_branch)
            return 0
        raise SystemExit(f"unexpected cwd for branch lookup: {cwd}")

    if args == ["rev-parse", "HEAD"]:
        if is_under(workspace_root, cwd):
            print(workspace_sha)
            return 0
        if is_under(canonical_root, cwd):
            print(canonical_head)
            return 0
        raise SystemExit(f"unexpected cwd for HEAD lookup: {cwd}")

    if args == ["rev-parse", "--show-toplevel"]:
        if is_under(workspace_root, cwd):
            print(workspace_root)
            return 0
        if is_under(canonical_root, cwd):
            print(canonical_root)
            return 0
        raise SystemExit(f"unexpected cwd for toplevel lookup: {cwd}")

    if args == ["rev-parse", "origin/main"]:
        print(target_sha)
        return 0

    if args == ["remote", "get-url", "origin"]:
        print(remote_url)
        return 0

    if args == ["worktree", "list", "--porcelain"]:
        if include_bare_entry:
            print(
                f"worktree {bare_root}\\n"
                "HEAD bare-head\\n"
                "bare\\n"
            )
            if emit_stray_main_branch_after_bare:
                print("branch refs/heads/main")
            print()
        print(
            f"worktree {workspace_root}\\n"
            f"HEAD {workspace_sha}\\n"
            f"branch refs/heads/{workspace_branch}\\n"
        )
        print(
            f"worktree {canonical_root}\\n"
            f"HEAD {canonical_head}\\n"
            f"branch refs/heads/{canonical_branch}\\n"
        )
        return 0

    if args == ["fetch", "origin"]:
        log(f"fetch:{cwd}")
        if fetch_noise:
            print("FAKE_FETCH_PROGRESS", file=sys.stderr)
        return 0

    if args == ["pull", "--ff-only", "origin", "main"]:
        log(f"pull:{cwd}")
        if pull_fails:
            print("pull failed: non-fast-forward", file=sys.stderr)
            return 1
        return 0

    if args[:2] == ["merge-base", "--is-ancestor"]:
        log(f"merge-base:{cwd}:{' '.join(args[2:])}")
        return 0 if merged_by_git else 1

    raise SystemExit(f"unsupported fake git args: {args}")


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    return fake_git


def _write_fake_gh(*, tmp_path: Path) -> Path:
    fake_gh = tmp_path / "fake_gh.py"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:3] != ["api", "-X", "GET"]:
        raise SystemExit(f"unsupported fake gh args: {args}")

    merged = os.environ.get("FAKE_GH_MERGED", "false") == "true"
    workspace_branch = os.environ["FAKE_GH_WORKSPACE_BRANCH"]
    workspace_sha = os.environ["FAKE_GH_WORKSPACE_SHA"]
    payload = []
    if merged:
        payload.append(
            {
                "merged_at": "2026-03-14T00:00:00Z",
                "base": {"ref": "main"},
                "head": {"ref": workspace_branch, "sha": workspace_sha},
            }
        )
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    return fake_gh


def _resolve_before_remove_workspace_root(*, tmp_path: Path) -> Path:
    return tmp_path / "workspace"


def _resolve_before_remove_invoke_cwd(*, tmp_path: Path, cwd_relative: str = "") -> Path:
    workspace_root = _resolve_before_remove_workspace_root(tmp_path=tmp_path)
    return workspace_root / cwd_relative if cwd_relative else workspace_root


def _run_symphony_before_remove_hook(
    *,
    tmp_path: Path,
    merged_by_git: bool,
    github_merged: bool,
    target_sha: str,
    dry_run: bool = False,
    workspace_branch: str = "feature/issue-811",
    pull_fails: bool = False,
    fetch_noise: bool = False,
    disable_flock: bool = False,
    start_fails: bool = False,
    precreate_lock_dir: bool = False,
    lock_wait_seconds: int | None = None,
    include_bare_entry: bool = False,
    emit_stray_main_branch_after_bare: bool = False,
    invocation_mode: str = "script",
    cwd_relative: str = "",
    timeout_sec: float = 10.0,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path, Path, Path]:
    workspace_root = _resolve_before_remove_workspace_root(tmp_path=tmp_path)
    canonical_root = (
        tmp_path / "repos" / "ouroboros_hub"
        if invocation_mode == "workflow"
        else tmp_path / "canonical-main"
    )
    bare_root = tmp_path / "canonical-bare"
    state_root = tmp_path / "state-root"
    hooks_log = tmp_path / "restart-hooks.log"
    git_log = tmp_path / "fake-git.log"
    marker_path = state_root / "canonical_restart.last_sha"
    restart_log = state_root / "canonical_restart.log"
    workspace_root.mkdir(parents=True, exist_ok=True)
    invoke_cwd = _resolve_before_remove_invoke_cwd(
        tmp_path=tmp_path, cwd_relative=cwd_relative
    )
    invoke_cwd.mkdir(parents=True, exist_ok=True)
    workspace_scripts = workspace_root / "scripts"
    workspace_scripts.mkdir(parents=True, exist_ok=True)
    workflow_hook_script = workspace_scripts / "symphony_before_remove_canonical_restart.sh"
    workflow_hook_script.write_text(
        (
            "#!/usr/bin/env bash\n"
            f"exec bash '{SYMPHONY_BEFORE_REMOVE_CANONICAL_RESTART}' \"$@\"\n"
        ),
        encoding="utf-8",
    )
    workflow_hook_script.chmod(0o755)
    canonical_root.mkdir(parents=True, exist_ok=True)
    bare_root.mkdir(parents=True, exist_ok=True)
    if precreate_lock_dir:
        (state_root / "canonical_restart.lock.d").mkdir(parents=True, exist_ok=True)
    fake_git = _write_fake_git(tmp_path=tmp_path)
    fake_gh = _write_fake_gh(tmp_path=tmp_path)
    shim_git = tmp_path / "git"
    shim_git.write_text(
        f"#!/usr/bin/env bash\nexec '{fake_git}' \"$@\"\n",
        encoding="utf-8",
    )
    shim_git.chmod(0o755)

    start_cmd = (
        "bash -c 'exit 17'"
        if start_fails
        else f"printf 'start\\n' >> '{hooks_log}'"
    )
    env = os.environ.copy()
    env.update(
        {
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "CANONICAL_RESTART_GIT_BIN": str(fake_git),
            "CANONICAL_RESTART_GH_BIN": str(fake_gh),
            "CANONICAL_RESTART_STOP_CMD": f"printf 'stop\\n' >> '{hooks_log}'",
            "CANONICAL_RESTART_START_CMD": start_cmd,
            "FAKE_GIT_LOG_PATH": str(git_log),
            "FAKE_GIT_WORKSPACE_ROOT": str(workspace_root),
            "FAKE_GIT_CANONICAL_ROOT": str(canonical_root),
            "FAKE_GIT_BARE_ROOT": str(bare_root),
            "FAKE_GIT_WORKSPACE_BRANCH": workspace_branch,
            "FAKE_GIT_WORKSPACE_SHA": "workspace-sha-1",
            "FAKE_GIT_TARGET_SHA": target_sha,
            "FAKE_GIT_MERGED_BY_GIT": "true" if merged_by_git else "false",
            "FAKE_GIT_PULL_FAILS": "true" if pull_fails else "false",
            "FAKE_GIT_FETCH_NOISE": "true" if fetch_noise else "false",
            "FAKE_GIT_INCLUDE_BARE_ENTRY": "true" if include_bare_entry else "false",
            "FAKE_GIT_EMIT_STRAY_MAIN_BRANCH_AFTER_BARE": (
                "true" if emit_stray_main_branch_after_bare else "false"
            ),
            "FAKE_GH_MERGED": "true" if github_merged else "false",
            "FAKE_GH_WORKSPACE_BRANCH": workspace_branch,
            "FAKE_GH_WORKSPACE_SHA": "workspace-sha-1",
            "CANONICAL_RESTART_DISABLE_FLOCK": "true" if disable_flock else "false",
            "HOME": str(tmp_path),
            "PATH": f"{tmp_path}:{env.get('PATH', '')}",
        }
    )
    if lock_wait_seconds is not None:
        env["CANONICAL_RESTART_LOCK_WAIT_SECONDS"] = str(lock_wait_seconds)

    if invocation_mode == "workflow":
        if dry_run:
            raise ValueError("workflow invocation does not support dry_run in this helper")
        args = ["bash", "-c", _workflow_before_remove_command()]
    else:
        args = ["bash", str(SYMPHONY_BEFORE_REMOVE_CANONICAL_RESTART)]
        if dry_run:
            args.append("--dry-run")

    completed = subprocess.run(
        args,
        cwd=invoke_cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    return completed, canonical_root, hooks_log, marker_path, git_log, restart_log


def test_runtime_instance_defaults_keep_main_canonical(tmp_path: Path) -> None:
    state_root = tmp_path / "overnight"
    defaults = _resolve_runtime_defaults(state_root=state_root, branch="main")

    assert defaults["ROOT_DIR"] == str(REPO_ROOT)
    assert defaults["LOG_DIR"] == str(state_root)
    assert defaults["DASHBOARD_PORT"] == "8080"
    assert defaults["TMUX_SESSION_PREFIX"] == "ouroboros_overnight"
    assert defaults["LIVE_RUNTIME_LOCK_PATH"] == str(state_root / "live_runtime.lock")


def test_runtime_instance_defaults_exports_resolved_branch_name(tmp_path: Path) -> None:
    state_root = tmp_path / "overnight"
    defaults = _resolve_runtime_defaults(state_root=state_root, branch="main")

    assert defaults["RUNTIME_BRANCH_NAME_RESOLVED"] == "main"


def test_before_remove_canonical_restart_skips_unmerged_worktree(
    tmp_path: Path,
) -> None:
    completed, canonical_root, hooks_log, marker_path, git_log, _ = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=False,
            github_merged=False,
            target_sha="main-sha-1",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "not merged into origin/main" in output
    assert str(canonical_root) in output
    assert not hooks_log.exists()
    assert not marker_path.exists()
    assert "pull:" not in git_log.read_text(encoding="utf-8")


def test_workflow_before_remove_hook_uses_git_ancestry_signal_from_nested_dir(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, _restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            # Keep the GitHub fallback disabled so this covers the git ancestry path only.
            merged_by_git=True,
            github_merged=False,
            target_sha="main-sha-workflow-hook",
            invocation_mode="workflow",
            cwd_relative="nested/context",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-workflow-hook"


def test_workflow_before_remove_hook_uses_github_fallback_signal_from_nested_dir(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            # `_git_log` stays intentionally unused here: this regression is scoped to
            # the restart decision and hook side effects, not the ancestry probe output.
            # Keep git ancestry disabled so this proves the GitHub fallback path only.
            merged_by_git=False,
            github_merged=True,
            target_sha="main-sha-workflow-github-fallback",
            invocation_mode="workflow",
            cwd_relative="nested/context",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    # Covers the immediate CLI/workflow output surfaced to the caller.
    assert "github merge fallback matched" in output
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert (
        marker_path.read_text(encoding="utf-8").strip()
        == "main-sha-workflow-github-fallback"
    )
    # Covers the persisted restart log written by the restart helper.
    assert "github merge fallback matched" in restart_log.read_text(encoding="utf-8")


def test_workflow_before_remove_hook_skips_unmerged_nested_dir_without_side_effects(
    tmp_path: Path,
) -> None:
    nested_cwd = _resolve_before_remove_invoke_cwd(
        tmp_path=tmp_path, cwd_relative="nested/context"
    )
    completed, canonical_root, hooks_log, marker_path, git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=False,
            github_merged=False,
            target_sha="main-sha-workflow-unmerged-skip",
            invocation_mode="workflow",
            cwd_relative="nested/context",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "not merged into origin/main" in output
    assert str(canonical_root) in output
    assert "github merge fallback matched" not in output
    assert not hooks_log.exists()
    assert not marker_path.exists()
    git_log_text = git_log.read_text(encoding="utf-8")
    assert "pull:" not in git_log_text
    log_text = restart_log.read_text(encoding="utf-8")
    assert "hook invoked" in log_text
    assert f"cwd={nested_cwd}" in log_text
    assert "not merged into origin/main" in log_text
    assert "github merge fallback matched" not in log_text


def test_workflow_before_remove_command_raises_when_hook_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_workflow = tmp_path / "WORKFLOW.md"
    broken_workflow.write_text(
        """---
hooks:
  after_create: |
    echo noop
---
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tests.test_runtime_overnight_scripts.WORKFLOW_TEMPLATE", broken_workflow)

    with pytest.raises(AssertionError, match="before_remove command not found"):
        _workflow_before_remove_command()


def test_before_remove_canonical_restart_logs_invocation_and_skip_reason(
    tmp_path: Path,
) -> None:
    completed, canonical_root, hooks_log, marker_path, git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=False,
            github_merged=False,
            target_sha="main-sha-skip-log",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "not merged into origin/main" in output
    assert str(canonical_root) in output
    assert not hooks_log.exists()
    assert not marker_path.exists()
    assert "pull:" not in git_log.read_text(encoding="utf-8")
    log_text = restart_log.read_text(encoding="utf-8")
    assert "hook invoked" in log_text
    assert "workspace_branch=feature/issue-811" in log_text
    assert "not merged into origin/main" in log_text


def test_before_remove_canonical_restart_uses_github_merge_signal_for_squash_merges(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, _ = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=False,
            github_merged=True,
            target_sha="main-sha-squash",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-squash"


def test_before_remove_canonical_restart_skips_main_workspace_cleanup(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, _ = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-main",
            workspace_branch="main",
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert "skip" in completed.stdout.lower()
    assert not hooks_log.exists()
    assert not marker_path.exists()


def test_before_remove_canonical_restart_restarts_canonical_main_once_per_target_sha(
    tmp_path: Path,
) -> None:
    first, canonical_root, hooks_log, marker_path, git_log, _ = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-1",
        )
    )

    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-1"
    assert f"pull:{canonical_root}" in git_log.read_text(encoding="utf-8")

    second, _, _, _, _, _ = _run_symphony_before_remove_hook(
        tmp_path=tmp_path,
        merged_by_git=True,
        github_merged=True,
        target_sha="main-sha-1",
    )

    assert second.returncode == 0, f"{second.stdout}\n{second.stderr}"
    output = f"{second.stdout}\n{second.stderr}"
    assert "already processed" in output
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-1"


def test_before_remove_canonical_restart_dry_run_reports_plan_without_mutation(
    tmp_path: Path,
) -> None:
    completed, canonical_root, hooks_log, marker_path, git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-2",
            dry_run=True,
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "dry-run" in output
    assert str(canonical_root) in output
    assert str(marker_path) in output
    assert not hooks_log.exists()
    assert not marker_path.exists()
    assert not git_log.exists()
    assert not restart_log.exists()


def test_before_remove_canonical_restart_falls_back_when_flock_missing(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-fallback-lock",
            disable_flock=True,
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-fallback-lock"
    log_text = restart_log.read_text(encoding="utf-8")
    assert "flock unavailable; using mkdir lock fallback" in log_text


def test_before_remove_canonical_restart_logs_pull_failures(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-pull-fail",
            pull_fails=True,
        )
    )

    assert completed.returncode != 0
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "canonical pull failed" in output
    assert not hooks_log.exists()
    assert not marker_path.exists()
    log_text = restart_log.read_text(encoding="utf-8")
    assert "pull --ff-only origin main failed" in log_text


def test_before_remove_canonical_restart_suppresses_fetch_noise(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, _ = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-noise",
            fetch_noise=True,
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "FAKE_FETCH_PROGRESS" not in output
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-noise"


def test_before_remove_canonical_restart_times_out_mkdir_lock_fallback(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-lock-timeout",
            disable_flock=True,
            precreate_lock_dir=True,
            lock_wait_seconds=1,
            timeout_sec=5.0,
        )
    )

    assert completed.returncode != 0
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "lock acquisition timed out" in output
    assert not hooks_log.exists()
    assert not marker_path.exists()
    log_text = restart_log.read_text(encoding="utf-8")
    assert "lock acquisition timed out" in log_text


def test_before_remove_canonical_restart_logs_critical_when_start_fails_after_stop(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-start-failure",
            start_fails=True,
        )
    )

    assert completed.returncode != 0
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop"]
    assert not marker_path.exists()
    log_text = restart_log.read_text(encoding="utf-8")
    assert "canonical runtime start failed after stop" in log_text
    assert "manual intervention required" in log_text


def test_before_remove_canonical_restart_ignores_stray_main_branch_after_bare_entry(
    tmp_path: Path,
) -> None:
    completed, _canonical_root, hooks_log, marker_path, _git_log, _restart_log = (
        _run_symphony_before_remove_hook(
            tmp_path=tmp_path,
            merged_by_git=True,
            github_merged=True,
            target_sha="main-sha-bare-parse",
            include_bare_entry=True,
            emit_stray_main_branch_after_bare=True,
        )
    )

    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert hooks_log.read_text(encoding="utf-8").splitlines() == ["stop", "start"]
    assert marker_path.read_text(encoding="utf-8").strip() == "main-sha-bare-parse"


def test_runtime_instance_defaults_isolate_non_main_branch(tmp_path: Path) -> None:
    state_root = tmp_path / "overnight"
    defaults = _resolve_runtime_defaults(
        state_root=state_root,
        branch="feature/worktree-runtime",
    )

    assert defaults["ROOT_DIR"] == str(REPO_ROOT)
    assert defaults["LOG_DIR"] == str(state_root / "feature-worktree-runtime")
    assert defaults["DASHBOARD_PORT"] != "8080"
    assert defaults["TMUX_SESSION_PREFIX"] == (
        "ouroboros_overnight_feature-worktree-runtime"
    )
    assert defaults["LIVE_RUNTIME_LOCK_PATH"] == str(
        state_root / "feature-worktree-runtime" / "live_runtime.lock"
    )


def test_runtime_verify_monitor_detects_live_process_without_pid_files(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    fake_live = subprocess.Popen(
        ["bash", "-lc", 'exec -a "src.main --mode=live" sleep 10'],
        cwd=REPO_ROOT,
    )
    try:
        env = os.environ.copy()
        env.update(
            {
                "ROOT_DIR": str(REPO_ROOT),
                "LOG_DIR": str(log_dir),
                "INTERVAL_SEC": "1",
                "MAX_HOURS": "1",
                "MAX_LOOPS": "1",
                "POLICY_TZ": "UTC",
            }
        )
        completed = subprocess.run(
            [str(BASH), str(RUNTIME_MONITOR)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

        log_text = _latest_runtime_log(log_dir)
        assert "app_alive=1" in log_text
        assert "[COVERAGE] LIVE_MODE=PASS source=process_liveness" in log_text
        assert "[ANOMALY]" not in log_text
    finally:
        fake_live.terminate()
        fake_live.wait(timeout=5)


def test_runtime_verify_monitor_restores_missing_app_pid_from_latest_run_log(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    current_pid = os.getpid()
    (log_dir / "run_20260330_000000.log").write_text(
        "\n".join(
            [
                "[2026-03-30T13:00:00Z] starting: python3 -m src.main --mode=live --dashboard",
                f"[2026-03-30T13:00:01Z] app pid={current_pid}",
                "Mode: live",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "ROOT_DIR": str(REPO_ROOT),
            "LOG_DIR": str(log_dir),
            "INTERVAL_SEC": "1",
            "MAX_HOURS": "1",
            "MAX_LOOPS": "1",
            "POLICY_TZ": "UTC",
            "PATH": "/bin:/usr/bin",
        }
    )
    completed = subprocess.run(
        [str(BASH), str(RUNTIME_MONITOR)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    assert (log_dir / "app.pid").read_text(encoding="utf-8").strip() == str(current_pid)
    assert "rg: command not found" not in completed.stderr
    log_text = _latest_runtime_log(log_dir)
    assert "restored app pid file" in log_text
    assert "app_alive=1" in log_text


def test_runtime_verify_monitor_restore_helper_supports_pid_file_override(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_log = log_dir / "run_20260330_000000.log"
    current_pid = os.getpid()
    run_log.write_text(
        "\n".join(
            [
                "[2026-03-30T13:00:00Z] starting: python3 -m src.main --mode=live --dashboard",
                f"[2026-03-30T13:00:01Z] app pid={current_pid}",
                "Mode: live",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    helper_log = log_dir / "helper.log"
    custom_pid_file = tmp_path / "custom-state" / "restored-app.pid"
    custom_pid_file.parent.mkdir(parents=True, exist_ok=True)

    shell_script = f"""
set -euo pipefail
LOG_DIR={shlex.quote(str(log_dir))}
OUT_LOG={shlex.quote(str(helper_log))}
{_runtime_monitor_helper_functions()}
restore_app_pid_file_from_run_log "" {shlex.quote(str(run_log))} {shlex.quote(str(custom_pid_file))}
"""
    completed = subprocess.run(
        [str(BASH), "-lc", shell_script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(current_pid)
    assert custom_pid_file.read_text(encoding="utf-8").strip() == str(current_pid)
    assert not (log_dir / "app.pid").exists()
    assert "restored app pid file" in helper_log.read_text(encoding="utf-8")


def test_runtime_verify_monitor_uses_branch_scoped_defaults_when_unset(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "overnight"
    env = os.environ.copy()
    env.update(
        {
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "RUNTIME_BRANCH_NAME": "feature/worktree-runtime",
            "INTERVAL_SEC": "1",
            "MAX_HOURS": "1",
            "MAX_LOOPS": "1",
            "POLICY_TZ": "UTC",
        }
    )
    completed = subprocess.run(
        [str(BASH), str(RUNTIME_MONITOR)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    log_dir = state_root / "feature-worktree-runtime"
    log_text = _latest_runtime_log(log_dir)
    assert "[INFO] runtime verify monitor started" in log_text
    assert "[HEARTBEAT]" in log_text
    assert "/home/agentson/repos/The-Ouroboros" not in log_text


def test_run_overnight_fails_fast_when_dashboard_port_in_use(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        env = os.environ.copy()
        env.update(
            {
                "LOG_DIR": str(log_dir),
                "TMUX_AUTO": "false",
                "DASHBOARD_PORT": str(port),
            }
        )
        completed = subprocess.run(
            [str(BASH), str(RUN_OVERNIGHT)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        output = f"{completed.stdout}\n{completed.stderr}"
        assert "already in use" in output
    finally:
        sock.close()


def test_run_overnight_writes_live_pid_and_watchdog_pid(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "LOG_DIR": str(log_dir),
            "TMUX_AUTO": "false",
            "STARTUP_GRACE_SEC": "1",
            "CHECK_INTERVAL": "2",
            "APP_CMD_BIN": "sleep",
            "APP_CMD_ARGS": "10",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUN_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"

    app_pid = int((log_dir / "app.pid").read_text(encoding="utf-8").strip())
    watchdog_pid = int((log_dir / "watchdog.pid").read_text(encoding="utf-8").strip())

    os.kill(app_pid, 0)
    os.kill(watchdog_pid, 0)

    for pid in (watchdog_pid, app_pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_run_overnight_uses_branch_scoped_defaults_when_log_dir_unset(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "overnight"
    env = os.environ.copy()
    env.update(
        {
            "OVERNIGHT_STATE_ROOT": str(state_root),
            "RUNTIME_BRANCH_NAME": "feature/worktree-runtime",
            "TMUX_AUTO": "false",
            "STARTUP_GRACE_SEC": "1",
            "CHECK_INTERVAL": "2",
            "APP_CMD_BIN": "sleep",
            "APP_CMD_ARGS": "10",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUN_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"

    log_dir = state_root / "feature-worktree-runtime"
    app_pid = int((log_dir / "app.pid").read_text(encoding="utf-8").strip())
    watchdog_pid = int((log_dir / "watchdog.pid").read_text(encoding="utf-8").strip())
    assert "- dashboard port: 8080" not in completed.stdout

    for pid in (watchdog_pid, app_pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_run_overnight_fails_when_process_exits_before_grace_period(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "LOG_DIR": str(log_dir),
            "TMUX_AUTO": "false",
            "STARTUP_GRACE_SEC": "1",
            "APP_CMD_BIN": "false",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUN_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    output = f"{completed.stdout}\n{completed.stderr}"
    assert "startup failed:" in output

    watchdog_pid_file = log_dir / "watchdog.pid"
    if watchdog_pid_file.exists():
        watchdog_pid = int(watchdog_pid_file.read_text(encoding="utf-8").strip())
        with pytest.raises(ProcessLookupError):
            os.kill(watchdog_pid, 0)


def test_runtime_verify_monitor_survives_when_no_live_pid(tmp_path: Path) -> None:
    """Regression test for #413: monitor loop must not exit when pgrep finds no live process.

    With set -euo pipefail, pgrep returning exit 1 (no match) would cause the
    whole script to abort via the pipefail mechanism. The fix captures pgrep
    output via a local variable with || true so pipefail is never triggered.

    Verifies that the script: (1) exits 0 after completing MAX_LOOPS=1, and
    (2) logs a HEARTBEAT entry. Whether live_pids is 'none' or not depends on
    what processes happen to be running; either way the script must not crash.
    """
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "ROOT_DIR": str(REPO_ROOT),
            "LOG_DIR": str(log_dir),
            "INTERVAL_SEC": "1",
            "MAX_HOURS": "1",
            "MAX_LOOPS": "1",
            "POLICY_TZ": "UTC",
        }
    )
    completed = subprocess.run(
        ["bash", str(RUNTIME_MONITOR)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"monitor exited non-zero (pipefail regression?): {completed.stderr}"
    )
    log_text = _latest_runtime_log(log_dir)
    assert "[INFO] runtime verify monitor started" in log_text
    assert "[HEARTBEAT]" in log_text, "monitor did not complete a heartbeat cycle"
    # live_pids may be 'none' (no match) or a pid (process found) — either is valid.
    # The critical invariant is that the script survived the loop without pipefail abort.


def test_runtime_verify_monitor_zero_max_hours_runs_until_stopped(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "ROOT_DIR": str(REPO_ROOT),
            "LOG_DIR": str(log_dir),
            "INTERVAL_SEC": "1",
            "MAX_HOURS": "0",
            "POLICY_TZ": "UTC",
            "BACKTEST_GATE_SYNC_ENABLED": "false",
        }
    )
    proc = subprocess.Popen(
        ["bash", str(RUNTIME_MONITOR)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        time.sleep(2)
        assert proc.poll() is None, proc.communicate(timeout=2)[1]
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    log_text = _latest_runtime_log(log_dir)
    assert "[INFO] runtime verify monitor started" in log_text


def test_run_overnight_starts_runtime_monitor_sidecar_and_syncs_backtest_gate_on_main(
    tmp_path: Path, fake_backtest_gate_gh_factory
) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)
    backtest_log_dir = tmp_path / "backtest-gate"
    marker_file = tmp_path / ".latest_backtest_gate_run"
    artifact_source = tmp_path / "backtest_gate_20260331_171626.log"
    artifact_source.write_text(
        "2026-03-31T17:16:32Z [PASS] full backtest gate passed\n",
        encoding="utf-8",
    )
    fake_gh = fake_backtest_gate_gh_factory(tmp_path / "fake_backtest_gate_gh.py")

    env = os.environ.copy()
    env.update(
        {
            "ROOT_DIR": str(REPO_ROOT),
            "LOG_DIR": str(log_dir),
            "TMUX_AUTO": "false",
            "STARTUP_GRACE_SEC": "1",
            "CHECK_INTERVAL": "2",
            "APP_CMD_BIN": "sleep",
            "APP_CMD_ARGS": "30",
            "RUNTIME_BRANCH_NAME": "main",
            "RUNTIME_MONITOR_INTERVAL_SEC": "1",
            "RUNTIME_MONITOR_MAX_HOURS": "0",
            "BACKTEST_GATE_GH_BIN": str(fake_gh),
            "BACKTEST_GATE_LOG_DIR": str(backtest_log_dir),
            "BACKTEST_GATE_SYNC_MARKER_FILE": str(marker_file),
            "BACKTEST_GATE_SYNC_INTERVAL_SEC": "1",
            "FAKE_BACKTEST_GATE_RUN_ID": "23810195275",
            "FAKE_BACKTEST_GATE_ARTIFACT_SOURCE": str(artifact_source),
        }
    )
    started = subprocess.run(
        ["bash", str(RUN_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert started.returncode == 0, f"{started.stdout}\n{started.stderr}"

    app_pid = int((log_dir / "app.pid").read_text(encoding="utf-8").strip())
    watchdog_pid = int((log_dir / "watchdog.pid").read_text(encoding="utf-8").strip())
    monitor_pid_path = log_dir / "runtime_verify.pid"
    assert _wait_until(monitor_pid_path.exists), "runtime monitor pid file not created"
    monitor_pid = int(monitor_pid_path.read_text(encoding="utf-8").strip())
    assert _wait_until(marker_file.exists), "backtest gate marker not written"
    assert marker_file.read_text(encoding="utf-8").strip() == "23810195275"
    assert _wait_until((backtest_log_dir / artifact_source.name).exists)

    for pid in (app_pid, watchdog_pid, monitor_pid):
        os.kill(pid, 0)

    log_text = _latest_runtime_log(log_dir)
    assert "backtest gate sync synced run_id=23810195275 files=1" in log_text

    stopped = subprocess.run(
        ["bash", str(STOP_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert stopped.returncode == 0, f"{stopped.stdout}\n{stopped.stderr}"

    for pid_file in ("app.pid", "watchdog.pid", "runtime_verify.pid"):
        assert not (log_dir / pid_file).exists()
    for pid in (monitor_pid, watchdog_pid, app_pid):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_stop_overnight_ignores_missing_tmux_server(tmp_path: Path) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_tmux = fake_bin / "tmux"
    fake_tmux.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = \"ls\" ]; then\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)

    pids: list[int] = []
    for name in ("runtime_verify.pid", "watchdog.pid", "app.pid"):
        launched = subprocess.run(
            ["bash", "-lc", "sleep 30 >/dev/null 2>&1 & echo $!"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        pid = int(launched.stdout.strip())
        pids.append(pid)
        (log_dir / name).write_text(str(pid), encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "LOG_DIR": str(log_dir),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )
    stopped = subprocess.run(
        ["bash", str(STOP_OVERNIGHT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert stopped.returncode == 0, f"{stopped.stdout}\n{stopped.stderr}"
    assert "종료할 tmux 세션 없음" in stopped.stdout
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_runtime_verify_monitor_syncs_backtest_gate_logs_on_main(
    tmp_path: Path, fake_backtest_gate_gh_factory
) -> None:
    log_dir = tmp_path / "overnight"
    log_dir.mkdir(parents=True, exist_ok=True)
    backtest_log_dir = tmp_path / "backtest-gate"
    marker_file = tmp_path / ".latest_backtest_gate_run"
    artifact_source = tmp_path / "backtest_gate_20260331_171626.log"
    artifact_source.write_text(
        "2026-03-31T17:16:32Z [PASS] full backtest gate passed\n",
        encoding="utf-8",
    )
    fake_gh = fake_backtest_gate_gh_factory(tmp_path / "fake_backtest_gate_gh.py")

    env = os.environ.copy()
    env.update(
        {
            "ROOT_DIR": str(REPO_ROOT),
            "LOG_DIR": str(log_dir),
            "INTERVAL_SEC": "1",
            "MAX_HOURS": "1",
            "MAX_LOOPS": "1",
            "POLICY_TZ": "UTC",
            "RUNTIME_BRANCH_NAME": "main",
            "BACKTEST_GATE_GH_BIN": str(fake_gh),
            "BACKTEST_GATE_LOG_DIR": str(backtest_log_dir),
            "BACKTEST_GATE_SYNC_MARKER_FILE": str(marker_file),
            "BACKTEST_GATE_SYNC_INTERVAL_SEC": "1",
            "FAKE_BACKTEST_GATE_RUN_ID": "23810195275",
            "FAKE_BACKTEST_GATE_ARTIFACT_SOURCE": str(artifact_source),
        }
    )
    completed = subprocess.run(
        ["bash", str(RUNTIME_MONITOR)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    mirrored = backtest_log_dir / artifact_source.name
    assert mirrored.exists()
    assert marker_file.read_text(encoding="utf-8").strip() == "23810195275"
    log_text = _latest_runtime_log(log_dir)
    assert "backtest gate sync synced run_id=23810195275 files=1" in log_text
