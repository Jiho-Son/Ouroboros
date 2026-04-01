# OOR-884 Daily Cycle Phase Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `TRADE_MODE=daily` 경로가 harness 가 기대하는 daily cycle phase `0..6` 를 실제 런타임 동작과 함께 관측 가능하게 만들고, market close 이후 cleanup/review 경로 누락을 없앤다.

**Architecture:** `src/main.py` 의 daily loop 에 phase 로그 helper 와 market lifecycle 추적을 추가한다. 기존 realtime close helper (`_handle_realtime_market_closures()` / `_handle_market_close()`) 를 daily mode 에도 재사용해 close 이후 cleanup/review 를 수행하고, scanner/playbook/evaluation/wait 단계에는 명시적인 phase 로그를 남긴다. 회귀 검증은 `tests/test_main.py` 에서 daily loop 를 2회 이상 시뮬레이션하는 방식으로 고정한다.

**Tech Stack:** Python 3.11, asyncio, pytest, logging, Linear workpad

---

### Task 1: close/review 누락을 failing test 로 고정

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

추가할 테스트:
- daily mode loop 가 한 번은 market open, 다음 번은 market closed 상태를 만나면 `_handle_market_close()` 또는 동등 close helper 를 정확히 1회 호출한다.
- same run 에서 cleanup/review phase 를 나타내는 로그가 남는다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "daily_cycle_phase and close_review" -v`

Expected: FAIL because current daily loop never routes through close handling.

**Step 3: Write minimal implementation**

- daily loop 에 previous/current market snapshot 비교를 추가한다.
- closed market 이 감지되면 existing close helper 를 호출한다.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "daily_cycle_phase and close_review" -v`

Expected: PASS

### Task 2: phase 0..4/5/6 observability contract 를 failing test 로 고정

**Files:**
- Modify: `tests/test_main.py`
- Modify: `src/main.py`

**Step 1: Write the failing test**

추가할 테스트:
- daily startup/open batch/idle wait/close-review 흐름을 시뮬레이션하고,
  `phase=0`, `phase=1`, `phase=2`, `phase=3`, `phase=4`, `phase=5`, `phase=6`
  토큰이 모두 로그에 남는지 검증한다.
- phase 2/3 는 scanner/playbook/evaluation 경계에서, phase 4 는 next-session scheduling 경계에서 기록되는지 확인한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "daily_cycle_phase and phase_logs" -v`

Expected: FAIL because current code has no standardized `phase=` logs.

**Step 3: Write minimal implementation**

- daily cycle phase logger/helper 를 추가한다.
- startup, idle wait, market preparation, stock evaluation, session scheduling, close cleanup, daily review 지점에 helper 를 배치한다.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "daily_cycle_phase and phase_logs" -v`

Expected: PASS

### Task 3: touched docs sync

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/plans/2026-04-01-oor-884-daily-cycle-phase-coverage.md`

**Step 1: Update docs**

- daily mode section 에 batch lifecycle 이 phase 로그와 close/review cleanup 을 포함한다는 운영 계약을 짧게 기록한다.

**Step 2: Run focused validation**

Run:
- `pytest tests/test_main.py -k "daily_cycle_phase" -v`
- `ruff check src/main.py tests/test_main.py docs/architecture.md`

Expected: PASS

### Task 4: broader verification and handoff artifacts

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `src/main.py`
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`

**Step 1: Run broader verification**

Run:
- `python3 scripts/session_handover_check.py --strict`
- `python3 scripts/validate_docs_sync.py`
- `pytest -v --cov=src --cov-report=term-missing`

Expected: PASS

**Step 2: Update tracking artifacts**

- Linear workpad 에 reproduction, pull evidence, validation, final handoff notes 를 반영한다.
