# OOR-886 Daily Cycle Phase Harness Contract Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `daily_cycle` 관측이 짧은 startup/open-market 창에만 머물러도 harness 가 phase `0..6` 계약을 모두 읽을 수 있게 해서 `missing_phases=[1,2,5,6]` 오탐을 없앤다.

**Architecture:** 실제 실행 경계 로그(`phase=2/3/5/6`)는 유지하고, daily-mode startup 직후 `daily_cycle` phase contract 로그를 한 번 더 남겨 downstream 관측기가 각 phase 카테고리와 의미를 즉시 볼 수 있게 한다. 회귀는 `tests/test_main.py` 에 단일 open-market run 기준 failing test 를 추가해 고정하고, `docs/architecture.md` 에 startup contract semantics 를 명시한다.

**Tech Stack:** Python 3.12, asyncio, pytest, logging, Markdown

---

### Task 1: 단일 관측 창에서 phase contract 누락을 failing test 로 고정

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

추가할 테스트:
- daily-mode `run()` 이 startup 후 open market batch 1회만 수행하는 시나리오를 시뮬레이션한다.
- 해당 단일 관측 창에서 `daily_cycle phase=0..6` 토큰이 모두 로그에 남는지 검증한다.
- 실제 batch phase 가 아직 실행되지 않은 경우에도 startup contract 로그로 phase category 가 노출되는지 확인한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "single_observation_window_logs_full_daily_cycle_contract" -v`

Expected: FAIL because current daily startup path only emits the phases that actually execute in that window.

### Task 2: startup phase contract helper 구현

**Files:**
- Modify: `src/main.py`
- Modify: `tests/test_main.py`

**Step 1: Write minimal implementation**

구현 항목:
- daily-cycle phase definition/contract helper 를 추가한다.
- `TRADE_MODE=daily` startup 직후 helper 를 호출해 `phase 1..6` 의미와 관측 조건을 로그로 남긴다.
- 기존 actual phase execution logs (`phase=2/3/5/6`) 는 유지한다.

**Step 2: Run targeted tests**

Run:
- `pytest tests/test_main.py -k "single_observation_window_logs_full_daily_cycle_contract or run_daily_mode_waits_for_next_market_open or run_daily_mode_handles_market_close_review_and_logs_phases or run_daily_session_logs_phase_prepare_and_process" -v`

Expected: PASS

### Task 3: observability 문서 동기화

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/plans/2026-04-02-oor-886-daily-cycle-phase-harness-contract.md`

**Step 1: Update docs**

- daily-mode observability 섹션에 startup contract 로그가 짧은 관측 창에서도 phase category `0..6` 을 보장한다는 점을 추가한다.

**Step 2: Run scoped validation**

Run:
- `ruff check src/main.py tests/test_main.py docs/architecture.md docs/plans/2026-04-02-oor-886-daily-cycle-phase-harness-contract.md`

Expected: PASS

### Task 4: broader verification and handoff

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `src/main.py`
- Modify: `tests/test_main.py`
- Modify: `docs/architecture.md`

**Step 1: Run broader verification**

Run:
- `python3 scripts/session_handover_check.py --strict`
- `python3 scripts/validate_docs_sync.py`

Expected: PASS

**Step 2: Update tracking artifacts**

- Linear workpad 에 reproduction, pull evidence, validation, final handoff notes 를 반영한다.
