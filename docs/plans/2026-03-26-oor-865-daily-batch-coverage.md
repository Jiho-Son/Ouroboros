# OOR-865 Daily Batch Coverage Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `TRADE_MODE=daily` 에서 `US_PRE` 시작 배치가 `US_REG` 추가 배치를 놓치지 않도록 다음 배치 시각을 동적으로 앞당긴다.

**Architecture:** 기존 6시간 cadence 자체는 유지하되, 현재 배치가 열린 시장의 regular session을 더 이상 커버하지 못하는 경우에만 catch-up 시각을 계산해 다음 wait 를 줄인다. 수정 범위는 `src/main.py` 의 daily loop 와 관련 helper, 그리고 해당 시나리오를 고정하는 `tests/test_main.py` 회귀 테스트로 제한한다.

**Tech Stack:** Python 3, pytest, asyncio, `zoneinfo`

---

### Task 1: US_REG 누락 재현 테스트 추가

**Files:**
- Modify: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

추가할 테스트:
- `2026-03-25 14:12 UTC` 시작, `14:13 UTC` 완료, `SESSION_INTERVAL_HOURS=6` 조건에서 다음 배치가 `2026-03-25 14:30 UTC` 근처의 US regular-session catch-up 으로 당겨져야 한다.
- daily `run()` 경로에서 `US_PRE` 시작 배치 후 `Daily mode has no additional regular-session batch before close` warning 이 남지 않고, `Next session in` 로그가 6시간보다 짧아져야 한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "daily_mode" -v`
Expected: 새 US catch-up 회귀 테스트가 FAIL 한다.

### Task 2: daily next-batch 보정 helper 구현

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write minimal implementation**

구현 항목:
- default next batch (`batch_completed_at + session_interval`) 계산은 유지한다.
- 현재 열린 US 시장이 `US_PRE` 로 시작했고 default cadence 로는 regular session 추가 배치가 없을 때만, regular-session 으로 전환되는 가장 이른 시각을 찾아 catch-up next batch 로 사용한다.
- daily loop 의 sleep/logging 은 보정된 next batch 시각을 기준으로 계산한다.

**Step 2: Run targeted tests**

Run: `pytest tests/test_main.py -k "daily_mode" -v`
Expected: 추가한 US 회귀 테스트와 기존 daily-mode 관련 테스트가 PASS 한다.

### Task 3: Repo verification and handoff

**Files:**
- Modify: `workflow/session-handover.md`
- Modify: `docs/plans/2026-03-26-oor-865-daily-batch-coverage.md`

**Step 1: Run verification**

Run: `ruff check src/ tests/`
Expected: PASS

**Step 2: Update tracking artifacts**

반영 항목:
- Linear workpad 에 재현/수정/검증 증적 업데이트
- 필요 시 PR/issue linkage 와 validation 결과 정리
