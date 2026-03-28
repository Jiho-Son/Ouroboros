# OOR-871 Market Close Composite Cache Cleanup Design

## Context

`OOR-858` 에서 realtime market close 처리 시 `market_states`, `playbooks`,
`pre_refresh_playbooks`, `MarketTrackingStore` 는 즉시 정리되도록 바뀌었다.
하지만 realtime loop 바깥의 runtime dict 인 `buy_cooldown` 과
`sell_resubmit_counts` 는 close helper 경로로 전달되지 않아 닫힌 마켓의 stale
엔트리가 남는다.

현재 key shape 는 다음과 같다.

- `buy_cooldown`: `{market_code}:{stock_code}`
- `sell_resubmit_counts`: `{exchange_code}:{stock_code}` 또는
  `BUY:{exchange_code}:{stock_code}`

즉, 기존 market-code 기반 runtime cleanup 만으로는 composite-key dict 를 정리할 수
없다.

## Goals

- realtime market close 처리 시 닫힌 마켓의 `buy_cooldown` stale 엔트리를 제거한다.
- 동일 close 처리에서 닫힌 마켓의 `sell_resubmit_counts` stale 엔트리를 제거한다.
- metadata 누락과 `_handle_market_close()` 예외가 발생해도 cleanup 결과를 일정하게
  유지한다.

## Non-Goals

- `buy_cooldown` / `sell_resubmit_counts` 의 key schema 자체를 재설계하지 않는다.
- session transition 시점의 cache 정책을 이번 티켓 범위로 넓히지 않는다.
- pending-order retry budget 의미를 변경하지 않는다.

## Options

### Option 1. key schema 를 market-scoped 구조로 전면 변경

- 장점: cleanup 기준이 단순해진다.
- 단점: `trading_cycle`, pending order helpers, 기존 테스트 전반을 건드려 범위가
  커진다. 이번 티켓은 stale cleanup 누락을 막는 것이므로 과하다.

### Option 2. close helper 에 market-aware composite-key cleanup 을 추가

- `buy_cooldown` 은 `market_code` prefix 로 지운다.
- `sell_resubmit_counts` 는 `market.exchange_code` 기반 SELL / `BUY:` prefix 둘 다
  지운다.
- 장점: 현재 key contract 를 유지하면서 close path 만 보강하면 된다.
- 단점: metadata 가 없으면 `sell_resubmit_counts` cleanup 은 exchange mapping 을
  알 수 없어 best-effort 처리만 가능하다.

### Option 3. metadata 누락 시 전체 composite cache 를 비운다

- 장점: stale 누수를 강하게 차단한다.
- 단점: 다른 열린 마켓의 retry/cooldown state 까지 잃을 수 있어 안전하지 않다.

## Decision

Option 2 를 채택한다.

이유:

- 문제는 cleanup 누락이지 key schema 부재 자체가 아니다.
- 닫힌 마켓에만 prefix-based pruning 을 적용하면 현재 주문/재시도 동작을 보존할 수
  있다.
- metadata 누락 시 전역 dict 를 비우는 것은 다른 열린 마켓 state 를 손상시킬 수
  있으므로 금지한다.

## Intended Behavior

- 정상 market close:
  `market_code` 와 `exchange_code` 로 식별 가능한 composite-key 엔트리를 모두
  제거한다.
- metadata 누락:
  `buy_cooldown` 의 `{market_code}:` prefix 는 제거한다.
  `sell_resubmit_counts` 는 `exchange_code` 를 알 수 없으므로 유지하고 warning 으로
  이유를 남긴다.
- `_handle_market_close()` 예외:
  close 후속 cleanup 은 계속 실행되어 정상 close 와 동일한 composite-key 정리
  결과를 남긴다.

## Proposed Changes

1. `src/main.py` 에 market close 전용 composite-key pruning helper 를 추가한다.
2. `_clear_realtime_market_runtime_state()` 가 기존 market-scoped runtime state 와
   함께 composite-key dict 도 정리하게 확장한다.
3. metadata 누락 시 `sell_resubmit_counts` 유지 이유를 helper 주석/로그로 명시한다.
4. `tests/test_main.py` 에 close / metadata-missing / close-failure 각각의 cleanup
   결과를 고정하는 회귀 테스트를 추가한다.

## Test Strategy

- test-first 로 `_handle_realtime_market_closures()` helper test 에
  `buy_cooldown` / `sell_resubmit_counts` 관측값을 추가해 현재 누락을 FAIL 로
  재현한다.
- metadata 누락 경로는 `buy_cooldown` cleanup 과
  `sell_resubmit_counts` intentional-retain warning 을 함께 검증한다.
- `_handle_market_close()` 예외 경로는 composite-key cleanup 이 계속 실행되는지
  확인한다.

## Approval Basis

무인 세션이므로 별도 human approval 을 기다리지 않고, Linear ticket `OOR-871`
본문과 Acceptance Criteria 를 설계 승인 기준으로 사용한다.
