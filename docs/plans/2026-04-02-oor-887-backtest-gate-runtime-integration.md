# OOR-887 Backtest Gate Runtime Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 표준 canonical runtime start/stop 경로만으로 `backtest_gate` freshness mirror가 지속 동작하도록 연결한다.

**Architecture:** `scripts/run_overnight.sh` 가 `runtime_verify_monitor.sh` sidecar를 함께 기동하고, `scripts/stop_overnight.sh` 가 해당 sidecar를 함께 종료한다. sidecar는 자동 기동 시 종료 제한 없이 실행되도록 하되, 수동 실행 기본값(24h)은 유지한다.

**Tech Stack:** Bash, pytest, GitHub CLI artifact sync, repo runtime scripts

---

### Task 1: 고장 재현을 테스트로 고정

**Files:**
- Modify: `tests/test_runtime_overnight_scripts.py`

**Step 1: Write the failing test**

```python
def test_run_overnight_starts_runtime_monitor_sidecar_and_syncs_backtest_gate(...):
    ...
    assert (log_dir / "runtime_verify.pid").exists()
    assert marker_file.read_text(...).strip() == "23810195275"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime_overnight_scripts.py -k "runtime_monitor_sidecar" -v`
Expected: FAIL because `run_overnight.sh` does not create/manage the monitor sidecar yet

**Step 3: Write minimal implementation**

```bash
# run_overnight.sh
# - spawn runtime_verify_monitor.sh in background
# - write runtime_verify.pid

# stop_overnight.sh
# - stop runtime verify pid when present
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime_overnight_scripts.py -k "runtime_monitor_sidecar" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_runtime_overnight_scripts.py scripts/run_overnight.sh scripts/stop_overnight.sh scripts/runtime_verify_monitor.sh
git commit -m "fix: connect backtest gate mirror to standard runtime path"
```

### Task 2: sidecar 수명과 문서를 정합화

**Files:**
- Modify: `scripts/runtime_verify_monitor.sh`
- Modify: `docs/commands.md`
- Modify: `docs/testing.md`

**Step 1: Write the failing test**

```python
def test_runtime_verify_monitor_zero_max_hours_runs_until_stopped(...):
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_runtime_overnight_scripts.py -k "zero_max_hours" -v`
Expected: FAIL because `MAX_HOURS=0` exits immediately today

**Step 3: Write minimal implementation**

```bash
# runtime_verify_monitor.sh
# - treat MAX_HOURS=0 as no time limit
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_runtime_overnight_scripts.py -k "zero_max_hours" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/runtime_verify_monitor.sh tests/test_runtime_overnight_scripts.py docs/commands.md docs/testing.md
git commit -m "docs: align runtime monitor sidecar behavior"
```

### Task 3: 범위 검증

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `tests/test_runtime_overnight_scripts.py`
- Modify: `scripts/run_overnight.sh`
- Modify: `scripts/stop_overnight.sh`
- Modify: `scripts/runtime_verify_monitor.sh`
- Modify: `docs/commands.md`
- Modify: `docs/testing.md`

**Step 1: Run targeted tests**

Run: `pytest tests/test_runtime_overnight_scripts.py -k "runtime_monitor_sidecar or zero_max_hours or backtest_gate" -v`
Expected: PASS

**Step 2: Run lint and docs validation**

Run: `ruff check scripts tests`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 3: Record evidence**

Run: `git status --short`
Expected: only intended files changed
