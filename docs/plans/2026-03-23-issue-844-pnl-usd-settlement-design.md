# OOR-844 PnL USD Settlement Design

## Context

- 현재 `trades.pnl` 과 `decision_logs.outcome_pnl` 은 SELL 체결 시점의 quote currency 그대로 기록된다.
- 기본 활성 시장은 `KR,US` 이고, `US_*` 는 이미 USD 가격으로 체결되지만 `KR` 은 KRW raw 손익이 저장된다.
- `ContextAggregator`, `DailyReviewer`, dashboard, pre-market planner 는 모두 `SUM(pnl)` 을 그대로 소비하므로, 현재는 시장별 단위가 섞인다.
- 재현 신호:
  - `python3 - <<'PY' ... trading_cycle(...) ... print(row) ... PY`
  - 출력: `(-25.0, -25.0, 0.0)`
  - 의미: KR SELL 경로가 USD 환산 없이 raw KRW 손익을 `pnl`/`strategy_pnl` 에 적재한다.

## FX Source Investigation

### Option 1: 보고/집계 시점에만 USD 환산

- 장점: SELL 로깅 경로 수정이 적다.
- 단점: `decision_logs.outcome_pnl`, `trades.pnl`, scorecard/context/dashboard 가 계속 혼합 단위를 저장하므로 근본 문제가 남는다.

### Option 2: SELL 결산 시 `pnl` 을 USD 기준으로 정규화

- 장점: 저장 시점부터 단위가 일관돼서 하위 집계기가 그대로 USD 합계를 사용한다.
- 장점: 기존 `strategy_pnl`/`fx_pnl` 분리 경로와도 맞는다. US 는 이미 USD 기준이므로 행동 변화가 작다.
- 단점: KR SELL 시 결산 시점 USD/KRW 환율을 추가 조회해야 한다.

### Option 3: 별도 `pnl_usd` 컬럼 추가

- 장점: 기존 raw 값 보존 가능.
- 단점: 스키마/집계기/dashboard/planner 전면 수정이 필요해 범위가 커진다.

## Recommendation

Option 2.

- 공식 KIS 샘플에서 `해외주식 체결기준현재잔고 [v1_해외주식-008]` 를 사용해
  `/uapi/overseas-stock/v1/trading/inquire-present-balance`
  (`CTRP6504R` / `VTRP6504R`) 호출 예시를 제공한다.
- 같은 공식 샘플의 컬럼 매핑에는 `bass_exrt` 와 `frst_bltn_exrt` 가 포함돼 있어, 결산 시점 환율 소스로 사용할 근거가 있다.
- 따라서 “환율 정보를 얻을 수 있는 방법이 없다” 상태는 아니며, 구현 보류 사유는 해소된다.

## Intended Behavior

- KR SELL:
  - raw KRW 손익을 계산한 뒤 결산 시점 USD/KRW 환율로 나눠 `pnl` 과 `decision_logs.outcome_pnl` 을 USD 로 저장한다.
  - `strategy_pnl` 도 동일한 USD 값을 저장하고 `fx_pnl` 은 `0.0` 으로 둔다.
  - `selection_context` 에 결산 환율을 남겨 감사 추적 가능하게 한다.
- US SELL:
  - 기존처럼 `pnl` 은 USD 기준을 유지한다.
  - 기존 `strategy_pnl` / `fx_pnl` 분리 로직은 유지한다.
- Planner/raw unit 표기:
  - KR 와 US scorecard/raw PnL 표시는 모두 `USD` 로 본다.

## Scope

- in scope:
  - KR/US SELL 결산 경로
  - scorecard/planner 표기와 관련 문서
  - 테스트 보강
- out of scope:
  - 비활성 해외 시장(`JP`, `HK`, `CN_*`, `VN_*`)의 local currency -> USD 정규화
  - 과거 거래 데이터 backfill

## Risk Notes

- `inquire-present-balance` 응답 스키마는 top-level 만이 아니라 `output1/2/3` 중 어디에 환율이 올지 변동 가능성이 있다. 추출 helper 는 여러 후보 위치를 스캔해야 한다.
- 결산 이후 환율 조회 실패 시 주문 성공 로그를 잃어버리면 안 된다. 따라서 런타임 실패 시에는 경고를 남기고 기존 raw 값을 유지하는 fail-open 이 필요하다.
