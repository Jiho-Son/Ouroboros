# OOR-844 PnL USD Settlement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** KR/US SELL 결산 손익을 USD 기준으로 정규화하고, KR 결산 시 KIS 환율 소스를 사용해 `pnl`/scorecard 단위를 일관되게 만든다.

**Architecture:** KR SELL 경로에 결산 환율 조회 helper 를 추가하고, SELL 경로 공통 `pnl` 값을 USD 기준으로 저장한다. US SELL 의 기존 USD/`fx_pnl` 분리 로직은 유지하고, planner 표기와 문서를 USD 기준으로 동기화한다.

**Tech Stack:** Python, asyncio, sqlite, pytest, KIS broker wrapper

---

### Task 1: 환율 소스/정규화 helper 고정

**Files:**
- Modify: `src/broker/overseas.py`
- Modify: `src/broker/balance_utils.py`
- Modify: `src/analysis/atr_helpers.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing tests**

- `tests/test_main.py` 에 아래 성격의 테스트를 추가한다.
  - `test_settlement_fx_rate_extraction_scans_present_balance_payload`
  - `test_normalize_trade_pnl_to_usd_converts_domestic_krw_using_settlement_fx_rate`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "settlement_fx_rate_extraction or normalize_trade_pnl_to_usd" -v`

Expected: FAIL because helper/function is missing or current behavior does not convert KRW -> USD.

**Step 3: Write minimal implementation**

- `src/broker/balance_utils.py` 에 present-balance payload 에서 `bass_exrt`/`frst_bltn_exrt`/기존 alias 를 찾는 helper 를 추가한다.
- `src/broker/overseas.py` 에 `inquire-present-balance` 기반 USD settlement rate 조회 method 를 추가한다.
- `src/analysis/atr_helpers.py` 에 domestic raw PnL 을 settlement FX rate 로 USD 정규화하는 helper 를 추가한다.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "settlement_fx_rate_extraction or normalize_trade_pnl_to_usd" -v`

Expected: PASS

### Task 2: SELL 경로를 USD 결산으로 전환

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing tests**

- `tests/test_main.py` 에 KR SELL 경로 회귀 테스트를 추가/수정한다.
  - `trading_cycle` KR SELL 이 mocked settlement FX rate 로 `pnl`/`strategy_pnl`/`decision_logs.outcome_pnl` 을 USD 로 저장하는지 검증
  - `selection_context` 에 settlement `fx_rate` 가 남는지 검증
- US SELL 기존 테스트는 USD 결과가 유지되는지 그대로 통과해야 한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -k "sell_order_uses_broker_balance_qty_not_db or settlement_fx_rate" -v`

Expected: FAIL because KR SELL currently persists `-25.0` raw KRW-equivalent value.

**Step 3: Write minimal implementation**

- `src/main.py` 의 SELL 처리 3개 경로(`realtime hard-stop`, 일반 `trading_cycle`, `run_daily_session_market`)에서 raw 손익 계산 직후 settlement USD PnL 로 정규화한다.
- KR 시장만 신규 FX lookup 을 사용하고, lookup 실패 시 fail-open warning + raw fallback 을 유지한다.
- US 경로의 `strategy_pnl`/`fx_pnl` 분리 합은 기존처럼 total `pnl` 과 같아야 한다.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -k "sell_order_uses_broker_balance_qty_not_db or handle_realtime_hard_stop_trigger_submits_overseas_sell_and_logs_trade" -v`

Expected: PASS

### Task 3: Planner/unit 표기와 문서 동기화

**Files:**
- Modify: `src/strategy/pre_market_planner.py`
- Modify: `tests/test_pre_market_planner.py`
- Modify: `docs/architecture.md`
- Modify: `docs/ouroboros/80_implementation_audit.md`

**Step 1: Write the failing test**

- `tests/test_pre_market_planner.py` 의 raw PnL unit 기대값을 KR=`USD`, US=`USD` 로 바꾸는 테스트를 먼저 작성한다.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_pre_market_planner.py -k "RawPnlUnitForMarket" -v`

Expected: FAIL because KR is currently `KRW`.

**Step 3: Write minimal implementation**

- planner raw PnL unit mapping 을 USD 기준으로 갱신한다.
- 아키텍처/감사 문서에서 `pnl` 의 기준 통화와 KR 결산 환율 사용을 명시한다.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_pre_market_planner.py -k "RawPnlUnitForMarket" -v`

Expected: PASS

### Task 4: 검증 및 정리

**Files:**
- Modify: `workflow/session-handover.md`
- Verify: `tests/test_main.py`
- Verify: `tests/test_pre_market_planner.py`
- Verify: `tests/test_daily_review.py`
- Verify: `tests/test_dashboard.py`
- Verify: `tests/test_context.py`

**Step 1: Run targeted regression suite**

Run: `pytest tests/test_main.py -k "sell_order_uses_broker_balance_qty_not_db or handle_realtime_hard_stop_trigger_submits_overseas_sell_and_logs_trade" -v`

Expected: PASS

**Step 2: Run downstream PnL consumer tests**

Run: `pytest tests/test_pre_market_planner.py -k "RawPnlUnitForMarket" -v`
Run: `pytest tests/test_daily_review.py -v`
Run: `pytest tests/test_dashboard.py -v`
Run: `pytest tests/test_context.py -v`

Expected: PASS

**Step 3: Run repo checks**

Run: `ruff check src tests/`
Run: `python3 scripts/validate_docs_sync.py`

Expected: PASS

**Step 4: Commit**

```bash
git add src/broker/overseas.py src/broker/balance_utils.py src/analysis/atr_helpers.py src/main.py src/strategy/pre_market_planner.py tests/test_main.py tests/test_pre_market_planner.py tests/test_daily_review.py tests/test_dashboard.py tests/test_context.py docs/architecture.md docs/ouroboros/80_implementation_audit.md docs/plans/2026-03-23-issue-844-pnl-usd-settlement-design.md docs/plans/2026-03-23-issue-844-pnl-usd-settlement.md workflow/session-handover.md
git commit -m "fix: normalize settled pnl to usd"
```
