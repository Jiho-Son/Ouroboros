# OOR-851 Monthly Rollup Month Boundary Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `aggregate_monthly_from_weekly()` 가 target month 에 실제로 속하는 ISO week 만 집계하도록 보정하고, global / market-scoped monthly rollup 회귀를 고정한다.

**Architecture:** `L4_MONTHLY` 집계는 `timeframe LIKE 'YYYY-W%'` 쿼리 대신 전달받은 `YYYY-MM` 의 모든 일자를 순회해 실제로 겹치는 ISO week 목록을 계산한 뒤, 그 목록만 `L5_WEEKLY` source 로 사용한다. 회귀 테스트는 (1) month 밖 weekly timeframe 배제, (2) `L4_MONTHLY` global + market-scoped key 유지, (3) 필요 최소한의 상위 레이어 전파 확인으로 나눈다.

**Tech Stack:** Python, sqlite-backed `ContextStore`, pytest, ruff

---

### Task 1: 월 경계 red 테스트 추가

**Files:**
- Modify: `tests/test_context.py`

**Step 1: Write the failing test**

- `2026-02` 집계에 `2026-W10` 같은 month 밖 weekly context 가 섞여도 `W05-W09` 만 반영돼야 한다는 테스트를 추가한다.
- 같은 테스트에서 `monthly_pnl`, `monthly_pnl_KR`, `monthly_pnl_US` 를 함께 검증해 global / market-scoped 계약을 동시에 고정한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_context.py -k 'monthly_from_weekly and outside_target_month' -v`
Expected: FAIL because current implementation uses `timeframe LIKE '2026-W%'` and includes `2026-W10`.

### Task 2: 최소 구현으로 month-to-week 매핑 보정

**Files:**
- Modify: `src/context/aggregator.py`
- Test: `tests/test_context.py`

**Step 1: Write minimal implementation**

- `YYYY-MM` 의 모든 날짜를 순회해 실제로 겹치는 ISO week 목록을 생성하는 private helper 를 추가한다.
- `aggregate_monthly_from_weekly()` 는 계산된 ISO week 목록만 `_collect_rollup_from_timeframes()` 에 전달한다.
- 기존 quarterly / annual / legacy aggregation 계약은 건드리지 않는다.

**Step 2: Run focused tests**

Run: `pytest tests/test_context.py -k 'monthly_from_weekly or upper_layers_store_market_scoped_pnl_keys' -v`
Expected: PASS

### Task 3: 연쇄 회귀와 품질 게이트 확인

**Files:**
- Modify: `tests/test_context.py`

**Step 1: Add minimal chain regression if needed**

- month boundary fix 가 `L4_MONTHLY` global / market-scoped 합계에 남고, 선택한 상위 rollup regression 이 그 수치를 그대로 본다는 최소 시나리오를 추가한다.
- mixed global-only + market-scoped upper-layer 합산 버그는 이번 티켓 범위를 넘으므로 follow-up Linear issue 로 분리한다.

**Step 2: Run validation**

Run: `pytest tests/test_context.py -v`
Expected: PASS

Run: `ruff check src/context/aggregator.py tests/test_context.py`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS when the plan doc is added.
