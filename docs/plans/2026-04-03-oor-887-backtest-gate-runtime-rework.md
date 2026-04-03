# OOR-887 Backtest Gate Runtime Rework Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** canonical `main`의 표준 overnight start/stop 경로가 `runtime_verify_monitor.sh` sidecar를 직접 관리해서 `data/backtest-gate` freshness mirror가 unattended runtime에서 계속 유지되게 만든다.

**Architecture:** `origin/main@d9977f0`에는 `scripts/runtime_verify_monitor.sh` 내부의 backtest-gate sync 로직이 이미 있지만, `scripts/run_overnight.sh`는 monitor를 기동하지 않아 표준 운영 경로에서 sync가 발생하지 않는다. `run_overnight.sh`에 main-only sidecar lifecycle을 추가하고 `scripts/stop_overnight.sh`가 같은 PID를 정리하게 만들며, sidecar 모드에서는 `MAX_HOURS=0`을 무기한 실행으로 해석한다. 테스트는 먼저 현재 main에서 sidecar 부재를 실패로 고정한 뒤 최소 shell 변경으로 green 상태를 만든다.

**Tech Stack:** Bash shell scripts, `pytest` subprocess regression tests, GitHub `gh` artifact helper, Markdown docs.

---

### Task 1: Sidecar lifecycle regression tests

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Read: `scripts/run_overnight.sh`
- Read: `scripts/stop_overnight.sh`
- Read: `scripts/runtime_verify_monitor.sh`

**Step 1: Write the failing tests**

추가할 테스트:

```python
def test_runtime_verify_monitor_zero_max_hours_runs_until_stopped(...):
    ...

def test_run_overnight_starts_runtime_monitor_sidecar_and_syncs_backtest_gate_on_main(...):
    ...

def test_stop_overnight_ignores_missing_tmux_server(...):
    ...
```

핵심 기대값:
- `MAX_HOURS=0` monitor가 즉시 종료되지 않는다.
- `run_overnight.sh` 실행 후 `runtime_verify.pid` 와 `runtime_verify_*.log` 가 생긴다.
- `stop_overnight.sh` 는 `runtime_verify.pid` 를 함께 정리한다.
- `tmux ls` 가 non-zero 여도 shutdown 전체는 성공한다.

**Step 2: Run tests to verify they fail**

Run:

```bash
TMPDIR=$PWD/.tmp/tmp pytest tests/test_runtime_overnight_scripts.py -k 'runtime_monitor_sidecar or zero_max_hours or missing_tmux_server' -v
```

Expected:
- sidecar 관련 테스트가 실패한다.
- 실패 이유가 현재 `run_overnight.sh` / `stop_overnight.sh`의 monitor lifecycle 부재여야 한다.

**Step 3: Commit the red-state evidence to notes**

workpad `Notes` / `Validation` 에 실패한 테스트 이름과 핵심 failure message를 남긴다.

### Task 2: Minimal shell changes for standard runtime path

**Files:**
- Modify: `scripts/run_overnight.sh`
- Modify: `scripts/runtime_verify_monitor.sh`
- Modify: `scripts/stop_overnight.sh`
- Test: `tests/test_runtime_overnight_scripts.py`

**Step 1: Implement the minimal behavior**

필수 수정:

```bash
# scripts/run_overnight.sh
# - add runtime monitor env defaults
# - decide auto-start on canonical main
# - write runtime_verify.pid
# - log sidecar PID / early exit

# scripts/runtime_verify_monitor.sh
# - treat MAX_HOURS=0 as no deadline

# scripts/stop_overnight.sh
# - stop runtime monitor before watchdog/app
# - tolerate tmux ls no-server exit status
```

**Step 2: Run the focused tests**

Run:

```bash
TMPDIR=$PWD/.tmp/tmp pytest tests/test_runtime_overnight_scripts.py -k 'runtime_monitor_sidecar or zero_max_hours or missing_tmux_server' -v
```

Expected:
- 새 regression tests가 모두 PASS 한다.

**Step 3: Refactor only if needed**

- PID file names / env defaults에 중복이 생기면 최소 정리만 한다.
- shell 로그 문구는 테스트와 운영자 관측에 필요한 범위만 유지한다.

### Task 3: Documentation and end-to-end validation

**Files:**
- Modify: `docs/commands.md`
- Modify: `docs/testing.md`
- Modify: `workflow/session-handover.md`
- Test: `tests/test_backtest_gate.py`
- Test: `tests/test_runtime_overnight_scripts.py`

**Step 1: Update docs to match actual runtime path**

- `docs/commands.md` 에 canonical `main`의 sidecar start/stop 책임을 명시한다.
- `docs/testing.md` 에 local freshness signal이 `run_overnight.sh` 경유로 유지된다는 점을 명시한다.

**Step 2: Run validation**

Run:

```bash
python3 scripts/session_handover_check.py --strict
TMPDIR=$PWD/.tmp/tmp pytest tests/test_runtime_overnight_scripts.py tests/test_backtest_gate.py -v
TMPDIR=$PWD/.tmp/tmp pytest -v --cov=src --cov-report=term-missing --cov-fail-under=80
ruff check scripts tests docs
python3 scripts/validate_docs_sync.py
```

Expected:
- targeted shell regressions PASS
- full pytest/coverage gate PASS
- `ruff` 와 docs sync PASS

**Step 3: Commit and publish**

```bash
git add tests/test_runtime_overnight_scripts.py scripts/run_overnight.sh scripts/runtime_verify_monitor.sh scripts/stop_overnight.sh docs/commands.md docs/testing.md docs/plans/2026-04-03-oor-887-backtest-gate-runtime-rework.md workflow/session-handover.md
git commit -m "fix(runtime): restore backtest gate monitor sidecar"
git push -u origin feature/issue-887-backtest-gate-freshness-rework
```

After push:
- 새 PR 생성
- issue attachment 연결
- `symphony` label 확인
- workpad를 최신 validation evidence로 갱신
