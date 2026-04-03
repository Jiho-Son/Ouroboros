# OOR-890 Runtime Monitor Pane Log Race Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** runtime monitor tmux pane가 `runtime_verify_*.log` 생성 지연에 의해 빠지지 않도록 discovery 경계를 안정화하고, pane skip 이유를 운영 로그/테스트에서 명확히 보이게 만든다.

**Architecture:** `scripts/run_overnight.sh` 의 one-shot `ls -t "$LOG_DIR"/runtime_verify_*.log` 를 helper 기반 bounded retry로 바꾸고, 성공/실패 모두 `RUN_LOG` 에 남긴다. 회귀 테스트는 fake `tee` 지연으로 "PID alive, log file delayed" 경계를 결정적으로 재현해 red-green 사이클을 고정한다.

**Tech Stack:** Bash shell scripts, `pytest` subprocess regression tests, fake PATH shims, Markdown docs.

---

### Task 1: Race boundary red tests

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`
- Read: `scripts/run_overnight.sh`
- Read: `scripts/runtime_verify_monitor.sh`

**Step 1: Write the failing tests**

추가할 테스트:

```python
def test_run_overnight_waits_for_runtime_monitor_log_before_tmux_split(...):
    ...

def test_run_overnight_logs_reason_when_runtime_monitor_log_never_appears_in_wait_window(...):
    ...
```

핵심 기대값:
- runtime monitor PID가 살아 있고 log 생성만 늦더라도 retry window 안이면 tmux pane이 추가된다.
- retry window 밖이면 pane은 생략되지만 `run_*.log` 에 skip 이유가 남는다.

**Step 2: Run tests to verify they fail**

Run:

```bash
TMPDIR=$PWD/.tmp/tmp pytest tests/test_runtime_overnight_scripts.py -k 'runtime_monitor_log_before_tmux_split or runtime_monitor_log_never_appears' -v
```

Expected:
- 첫 번째 테스트는 현재 one-shot discovery 때문에 실패한다.
- 두 번째 테스트는 skip reason 로그 부재 때문에 실패한다.

**Step 3: Record red evidence**

- workpad `Notes` 와 `Validation` 에 failing test 이름과 핵심 assertion을 남긴다.

### Task 2: Minimal shell fix

**Files:**
- Modify: `scripts/run_overnight.sh`
- Test: `tests/test_runtime_overnight_scripts.py`

**Step 1: Implement bounded discovery helper**

필수 수정:

```bash
# scripts/run_overnight.sh
# - add latest runtime monitor log helper
# - add bounded wait/poll env defaults
# - log discovery success-after-wait or explicit skip reason
# - use helper before tmux runtime monitor split-window
```

**Step 2: Run focused tests**

Run:

```bash
TMPDIR=$PWD/.tmp/tmp pytest tests/test_runtime_overnight_scripts.py -k 'runtime_monitor_log_before_tmux_split or runtime_monitor_log_never_appears or adds_runtime_monitor_log_to_tmux_when_enabled' -v
```

Expected:
- 새 red tests와 기존 tmux pane test가 모두 PASS 한다.

**Step 3: Refactor only if needed**

- helper/local 변수명 정리 정도만 수행한다.
- wait/skip 로그는 테스트와 운영 관측에 필요한 최소 수준만 남긴다.

### Task 3: Validation and publish readiness

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-04-03-oor-890-runtime-monitor-pane-race-design.md`
- Modify: `docs/plans/2026-04-03-oor-890-runtime-monitor-pane-race.md`
- Test: `tests/test_runtime_overnight_scripts.py`

**Step 1: Run validation**

Run:

```bash
python3 scripts/session_handover_check.py --strict
TMPDIR=$PWD/.tmp/tmp pytest tests/test_runtime_overnight_scripts.py -k 'runtime_monitor_log_before_tmux_split or runtime_monitor_log_never_appears or adds_runtime_monitor_log_to_tmux_when_enabled' -v
ruff check scripts tests
```

Expected:
- strict handover gate PASS
- targeted runtime/tmux regression PASS
- `ruff` PASS for touched surface

**Step 2: Commit and publish**

```bash
git add scripts/run_overnight.sh tests/test_runtime_overnight_scripts.py docs/plans/2026-04-03-oor-890-runtime-monitor-pane-race-design.md docs/plans/2026-04-03-oor-890-runtime-monitor-pane-race.md workflow/session-handover.md
git commit -m "fix(runtime): harden runtime monitor tmux pane log discovery"
git push -u origin feature/issue-890-harness-runtime-monitor-tmux-pane-log-race-follow-up
```

After push:
- PR 생성 및 issue attachment 연결
- `symphony` label 확인
- workpad를 validation evidence와 commit/PR 정보로 최종 갱신
