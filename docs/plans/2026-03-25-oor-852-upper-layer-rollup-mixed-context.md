# OOR-852 Upper-Layer Rollup Mixed Context Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** mixed global-only + market-scoped monthly context 에서 `quarterly_pnl` / `annual_pnl` / `total_pnl` 이 global total 을 유지하면서 market-scoped rollup 키 계약도 그대로 보존되게 한다.

**Architecture:** `_collect_rollup_from_timeframes()` 는 각 timeframe 별로 `base_key` 와 `base_key_<market>` 들을 함께 읽는다. market-scoped 키가 있더라도 global total 계산은 `base_key` 를 우선 사용하고, `base_key` 가 없을 때만 market 합계를 fallback total 로 사용하게 바꾼다. 회귀 테스트는 mixed source context 를 직접 주입해서 `L3_QUARTERLY`, `L2_ANNUAL`, `L1_LEGACY` 각 레이어가 global total 과 market totals 를 동시에 유지하는지 개별로 검증한다.

**Tech Stack:** Python, sqlite-backed `ContextStore`, pytest, ruff

---

### Task 1: failing regression tests로 mixed upper-layer 누락 재현

**Files:**
- Modify: `tests/test_context.py`

**Step 1: Write the failing tests**

- mixed `L4_MONTHLY` source 에서 `monthly_pnl=350.0`, `monthly_pnl_KR=200.0`, `monthly_pnl_US=50.0` 를 주입하고 `quarterly_pnl=350.0` 이어야 한다는 `L3_QUARTERLY` 회귀 테스트를 추가한다.
- mixed `L3_QUARTERLY` source 에서 `quarterly_pnl=350.0`, `quarterly_pnl_KR=200.0`, `quarterly_pnl_US=50.0` 를 주입하고 `annual_pnl=350.0` 이어야 한다는 `L2_ANNUAL` 회귀 테스트를 추가한다.
- mixed `L2_ANNUAL` source 에서 `annual_pnl=350.0`, `annual_pnl_KR=200.0`, `annual_pnl_US=50.0` 를 주입하고 `total_pnl=350.0` 이어야 한다는 `L1_LEGACY` 회귀 테스트를 추가한다.

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_context.py -k 'mixed_global_only_portion' -v`
Expected: FAIL because `_collect_rollup_from_timeframes()` currently ignores the base global key whenever any market-scoped key exists.

### Task 2: 최소 수정으로 mixed global total 보존

**Files:**
- Modify: `src/context/aggregator.py`
- Test: `tests/test_context.py`

**Step 1: Write minimal implementation**

- `_collect_rollup_from_timeframes()` 에서 timeframe 별 `base_value` 를 먼저 읽고, global total 계산은 `base_value` 우선 / market 합계 fallback 규칙으로 바꾼다.
- market-specific totals 누적은 기존처럼 `base_key_<market>` 값만 사용한다.
- monthly-only, market-only, mixed context 모두에서 total 이 중복합산되지 않도록 로직을 단일 분기 구조로 정리한다.

**Step 2: Run focused tests**

Run: `pytest tests/test_context.py -k 'mixed_global_only_portion or upper_layers_store_market_scoped_pnl_keys' -v`
Expected: PASS

### Task 3: 범위 검증과 문서 동기화 확인

**Files:**
- Modify: `tests/test_context.py`
- Modify: `src/context/aggregator.py`
- Create: `docs/plans/2026-03-25-oor-852-upper-layer-rollup-mixed-context.md`

**Step 1: Run validation**

Run: `pytest tests/test_context.py -v`
Expected: PASS

Run: `ruff check src/context/aggregator.py tests/test_context.py`
Expected: PASS

Run: `python3 scripts/validate_docs_sync.py`
Expected: PASS

**Step 2: Run required broad checks before push**

Run: `ruff check src/ tests/`
Expected: PASS

Run: `pytest -v --cov=src --cov-report=term-missing`
Expected: PASS
