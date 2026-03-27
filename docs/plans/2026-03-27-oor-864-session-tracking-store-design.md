# OOR-864 Per-Market Session Tracking Store Design

## Context

현재 realtime runtime tracking 은 `src/main.py` 안의 세 개의 parallel dict 로 유지된다.

- `active_stocks: dict[str, list[str]]`
- `scan_candidates: dict[str, dict[str, ScanCandidate]]`
- `last_scan_time: dict[str, float]`

`OOR-861`, `OOR-862` 덕분에 market close 와 session transition 시 stale cache clear 는
추가됐지만, policy 와 diagnostics 계약은 아직 dict 조합과 helper 호출 순서에 암묵적으로
퍼져 있다.

현재 관측 신호:

- `pytest tests/test_main.py -k "tracking_cache or session_transition_clears_tracking_cache" -v`
  는 green 이다. 즉 현재 코드는 “transition 시 stale cache clear” 까지는 보장한다.
- 그러나 `/api/status` market payload 는
  `trade_count`, `decision_count`, `latest_session_id` 등 DB 기반 요약만 제공하고,
  runtime tracking/session state 는 전혀 노출하지 않는다.
- `build_overseas_symbol_universe()` 는 여전히 runtime active universe 를 fallback 1순위로
  받는 구조라, carry-over 허용 범위가 “같은 session”인지 “같은 market”인지 함수 시그니처만
  봐서는 드러나지 않는다.

이번 티켓의 목적은 stale clear 자체를 한 번 더 억지로 추가하는 것이 아니라,
runtime tracking lifecycle 을 market + session 경계에 맞는 store contract 로 승격하고,
carry-over / discard 정책과 diagnostics 를 코드 레벨에서 명시하는 것이다.

## Approaches

### 1. 기존 dict 구조를 유지하고 로그만 더 추가한다

- 장점: 변경 범위가 작다.
- 단점: reset/rollover 정책이 여전히 dict 세트와 helper 순서에 암묵적으로 남는다.
- 단점: fallback guard 가 “session-scoped” 임을 타입/계약 수준에서 표현하지 못한다.

### 2. per-market session-scoped tracking store 를 도입한다

- `active_stocks`, `scan_candidates`, `last_scan_time` 을 `MarketTrackingStore` 아래로 모은다.
- 각 market 의 current session state 를 하나의 state object 로 관리한다.
- close / session transition / same-session rescan 정책을 store method 로 캡슐화한다.
- 장점: acceptance criteria 인 reset/rollover policy 와 diagnostics 를 같은 contract 로 묶을 수 있다.
- 장점: dashboard/status/log surface 에서 관측할 payload 를 한 곳에서 생성할 수 있다.
- 단점: realtime loop, dashboard status API, 테스트 표면을 함께 수정해야 한다.

### 3. runtime tracking 을 SQLite 로 영속화한다

- 장점: 재시작 후에도 tracking diagnostics 를 조회할 수 있다.
- 단점: ticket scope 를 넘어선다. ephemeral runtime state 를 굳이 DB schema 로 승격하면
  cleanup/migration 부담이 커진다.
- 단점: “종료 시 폐기해야 할 state” 라는 요구와도 맞지 않는다.

## Recommendation

접근 2를 채택한다.

이번 티켓의 핵심은 runtime tracking 을 “same-session carry-over 는 허용하되,
market close 와 new session 에서는 폐기” 되는 session-scoped state 로 재정의하는 것이다.
store contract 를 도입하면 reset/rollover policy, fallback guard, diagnostics 를 모두
같은 자료구조 위에서 설명할 수 있다.

## Design

### 1. State Model

`src/core/market_tracking.py` 에 아래 구조를 둔다.

- `MarketTrackingSessionState`
  - `market_code`
  - `session_id`
  - `active_stocks`
  - `scan_candidates`
  - `last_scan_monotonic`
- `MarketTrackingSnapshot`
  - dashboard/log 로 내보낼 immutable summary
  - `active_count`, `active_stocks`, `candidate_count`, `candidate_codes`,
    `last_scan_age_seconds` 등을 포함
- `MarketTrackingStore`
  - thread-safe (`threading.Lock`) runtime store
  - key 는 `market_code`
  - value 는 “현재 활성 session 의 tracking state”

정책:

- market close:
  - 해당 market state 를 store 에서 완전히 제거한다.
- new session:
  - 같은 market 이더라도 기존 state 를 버리고 새 session state 로 rollover 한다.
- same-session rescan:
  - 기존 active universe / scan candidates / last scan time 을 유지·갱신한다.

### 2. Store API

store 는 최소한 아래 동작을 제공한다.

- `ensure_market_session(market_code, session_id)`
  - state 가 없으면 생성
  - 같은 session 이면 재사용
  - 다른 session 이면 rollover
- `clear_market(market_code)`
  - close 시 완전 폐기
- `record_scan_result(market_code, session_id, candidates, scanned_at)`
  - `active_stocks`, `scan_candidates`, `last_scan_monotonic` 동시 갱신
- `record_empty_scan(market_code, session_id, scanned_at)`
  - empty universe 도 같은 session state 안에서 명시적으로 기록
- `runtime_fallback_stocks(market_code, session_id)`
  - session mismatch 면 빈 리스트를 반환해 carry-over 를 차단
- `scan_candidates_snapshot()`
  - 기존 `trading_cycle()` 호출과 호환되는 read-only snapshot 제공
- `dashboard_status_payload()`
  - market별 diagnostics dict 반환

핵심은 “세 필드가 따로 움직이지 않는다”는 점이다.
scan 결과 기록은 반드시 같은 store method 에서 함께 갱신한다.

### 3. Realtime Loop Integration

`src/main.py` realtime loop 는 store 를 single source of truth 로 사용한다.

- run 시작 시 `MarketTrackingStore` 생성
- `_start_dashboard_server()` 에 diagnostics provider 를 넘긴다
- 각 market 처리 직전 `ensure_market_session(market.code, session_info.session_id)` 호출
- rescan 여부 판단은 store 의 `last_scan_monotonic` 기준으로 읽는다
- scanner 성공/empty 결과는 store method 로 기록한다
- close handler 는 `clear_market()` 호출
- session transition handler 는 `ensure_market_session(...new session...)` 또는
  dedicated rollover method 를 통해 기존 session state 를 폐기한다

### 4. Scanner Fallback Carry-Over Guard

`build_overseas_symbol_universe()` 는 더 이상 raw `active_stocks` dict 를 직접 받지 않는다.

- 입력은 `tracking_store` + `market_code` + `session_id` 또는
  그에 준하는 session-aware accessor 로 바꾼다.
- runtime universe carry-over 는 `runtime_fallback_stocks()` 가
  same-session 일 때만 허용한다.
- session mismatch / missing state / close 이후에는
  DB recent symbols + broker holdings 만 fallback source 로 남는다.

이로써 “fallback carry-over 는 session-scoped” 라는 정책이 함수 호출에서 드러난다.

### 5. Diagnostics

관측 surface 는 두 군데다.

- runtime summary log
  - scan 완료 직후
  - session rollover 직후
  - market close cleanup 직후
- dashboard `/api/status`
  - 각 market payload 에 `runtime_tracking` 필드 추가
  - 예: `session_id`, `active_count`, `active_stocks`, `candidate_count`,
    `candidate_codes`, `last_scan_age_seconds`

provider 가 없을 때 dashboard 는 기존처럼 동작하되 `runtime_tracking` 는 `null` 또는
빈 payload 로 둔다. 즉 diagnostics 는 optional extension 이고 기존 DB 요약 contract 를
깨지 않는다.

## Testing Strategy

- `tests/test_market_tracking.py`
  - same-session reuse
  - new-session rollover reset
  - market close clear
  - fallback guard 가 session mismatch 를 차단하는지
- `tests/test_main.py`
  - realtime loop 가 session transition 뒤 이전 runtime universe 를 다시 쓰지 않는지
  - close cleanup 이 다른 market state 를 건드리지 않는지
- `tests/test_dashboard.py`
  - `/api/status` 가 runtime tracking diagnostics 를 노출하는지
  - provider 미주입 시 기존 status contract 가 유지되는지

## Non-Goals

- runtime tracking state 를 DB 에 저장하지 않는다.
- scanner ranking/selection 알고리즘 자체는 바꾸지 않는다.
- playbook selection policy 는 `OOR-863` 범위를 넘겨 재작업하지 않는다.
