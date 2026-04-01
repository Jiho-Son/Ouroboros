# OOR-885 Backtest Gate Local Mirror Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** GitHub `Backtest Gate` scheduled run이 성공하면 canonical/main repo의 local `data/backtest-gate` 도 최신 artifact로 자동 갱신되어 harness freshness 경보가 오탐을 내지 않게 만든다.

**Architecture:** 새 shell helper가 latest successful scheduled `Backtest Gate` artifact를 local log dir로 내려받고, `runtime_verify_monitor.sh` 가 main branch에서만 이를 주기적으로 호출한다. 테스트는 fake `gh` 를 사용한 shell regression으로 helper sync와 monitor integration을 고정한다.

**Tech Stack:** Bash, GitHub CLI (`gh`), Python 3.11, pytest, runtime monitor scripts

---

### Task 1: failing test로 local mirror 결손을 고정

**Files:**
- Modify: `tests/test_backtest_gate.py`
- Modify: `tests/test_runtime_overnight_scripts.py`

**Step 1: Write the failing tests**

- helper regression:
  - fake `gh` 가 latest schedule run id와 artifact file 하나를 제공할 때, sync helper가 지정된 local log dir에 artifact를 복사하고 marker를 남기는지 검증한다.
- monitor regression:
  - main branch runtime monitor를 1 loop 실행했을 때 helper가 호출되어 local `data/backtest-gate` mirror가 생성되는지 검증한다.

**Step 2: Run tests to verify they fail**

Run:
- `pytest tests/test_backtest_gate.py -k sync -v`
- `pytest tests/test_runtime_overnight_scripts.py -k backtest_gate_sync -v`

Expected: FAIL because helper와 monitor integration이 아직 존재하지 않는다.

### Task 2: minimal implementation으로 artifact mirror 자동 경로 추가

**Files:**
- Create: `scripts/sync_backtest_gate_artifact.sh`
- Modify: `scripts/runtime_verify_monitor.sh`

**Step 1: Write minimal implementation**

- helper script를 추가해 latest successful schedule run 조회, artifact download, local copy, marker update를 수행한다.
- `runtime_verify_monitor.sh` 에 main-only, interval-based helper 호출을 추가하고 결과를 runtime log에 남긴다.

**Step 2: Run focused tests to verify they pass**

Run:
- `pytest tests/test_backtest_gate.py -k sync -v`
- `pytest tests/test_runtime_overnight_scripts.py -k backtest_gate_sync -v`

Expected: PASS

### Task 3: docs sync

**Files:**
- Modify: `docs/testing.md`
- Modify: `docs/commands.md`

**Step 1: Update docs**

- `Backtest Gate` 설명에 local `data/backtest-gate` freshness가 GitHub scheduled artifact mirror로 유지된다는 점을 짧게 기록한다.
- runtime monitor 운영 섹션에 main branch monitor가 backtest gate artifact를 mirror한다는 점과 관련 env knobs를 기록한다.

**Step 2: Run focused validation**

Run:
- `python3 scripts/validate_docs_sync.py`

Expected: PASS

### Task 4: broader verification and handoff artifacts

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `scripts/sync_backtest_gate_artifact.sh`
- Modify: `scripts/runtime_verify_monitor.sh`
- Modify: `tests/test_backtest_gate.py`
- Modify: `tests/test_runtime_overnight_scripts.py`
- Modify: `docs/testing.md`
- Modify: `docs/commands.md`

**Step 1: Run broader verification**

Run:
- `python3 scripts/session_handover_check.py --strict`
- `pytest tests/test_backtest_gate.py tests/test_runtime_overnight_scripts.py -k "backtest_gate or sync" -v`
- `ruff check scripts tests`
- `python3 scripts/validate_docs_sync.py`

Expected: PASS

**Step 2: Update tracking artifacts**

- Linear workpad에 reproduction, pull evidence, targeted validation, final handoff notes를 반영한다.
