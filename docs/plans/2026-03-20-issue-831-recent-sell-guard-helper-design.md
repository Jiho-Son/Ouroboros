# Issue-831 Recent SELL Guard Helper Design

## Goal

`_evaluate_trading_cycle_decision` 과 `_process_daily_session_stock` 에 중복된
recent SELL guard 적용 블록을 하나의 공용 helper로 수렴해 로그/rationale
문구와 판단 계약을 한 곳에서 관리한다.

## Current Signal

- `src/main.py` 에 동일한 recent SELL guard 블록이 두 번 존재한다.
- 두 블록은 `get_latest_sell_trade()`, `_resolve_recent_sell_guard_window_seconds()`,
  `_should_block_buy_above_recent_sell()` 호출 순서와 rationale/log 포맷이 사실상 같다.
- 기존 회귀 테스트는 두 경로의 BUY 억제 동작만 확인하고, 중복 제거 후에도 유지돼야 한다.

## Options Considered

### Option 1. `src/main.py` 내부 공용 helper로 추출

- 장점: 중복된 블록 전체를 가장 직접적으로 제거할 수 있다.
- 장점: `TradeDecision`, DB 조회, 로깅 포맷을 현재 호출 맥락에서 그대로 유지할 수 있다.
- 단점: helper가 `src/main.py` 에 남아 pure helper 모듈까지는 내려가지 않는다.

### Option 2. `src/core/order_helpers.py` 로 상태 의존 helper를 이동

- 장점: recent SELL 관련 책임을 helper 모듈에 더 많이 모을 수 있다.
- 단점: DB 조회, `TradeDecision`, 로깅 포맷까지 함께 옮기면 수정면이 커지고 결합도가 올라간다.

### Option 3. 문자열/포맷만 공용화

- 장점: 변경량이 가장 작다.
- 단점: 핵심 중복인 DB 조회 + guard 적용 흐름이 남아 acceptance criteria를 충분히 만족하지 못한다.

## Chosen Design

Option 1을 사용한다.

- `src/main.py` 에 recent SELL guard 전용 helper `_apply_recent_sell_guard()` 를 추가한다.
- helper는 BUY action, 현재가, `db_conn`, `stock_code`, `market`, `settings`,
  현재 `TradeDecision` 을 입력으로 받아 block 여부를 평가한다.
- guard가 발동하면 HOLD `TradeDecision` 에 필요한 rationale 문자열과 로깅에 필요한
  수치 payload를 함께 돌려준다.
- 두 BUY 경로는 같은 helper 반환값을 사용해 동일한 rationale/log 포맷을 적용한다.

## Testing Strategy

- helper 단위 테스트를 먼저 추가해 block/non-block 계약을 고정한다.
- 기존 realtime BUY / daily BUY 억제 테스트를 유지하고 rationale 문자열을 더 엄격하게 확인한다.
- targeted `pytest` 와 수정 파일 대상 `ruff` 로 회귀를 확인한다.
