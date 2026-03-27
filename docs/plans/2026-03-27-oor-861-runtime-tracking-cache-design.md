# OOR-861 Runtime Tracking Cache Design

## Context

`src/main.py`의 realtime loop는 market close 경로에서 per-market runtime state를 정리하지만,
session transition에서는 `playbooks`만 비우고 `active_stocks`, `scan_candidates`,
`last_scan_time`을 유지한다. 해외 market은 다음 scan에서
`build_overseas_symbol_universe()`가 기존 `active_stocks`를 fallback 1순위로 사용하므로,
`US_PRE -> US_REG` 같은 session reset 직후에도 이전 세션 종목 universe가 재주입될 수 있다.

## Approaches

### 1. `build_overseas_symbol_universe()`에서 이전 `active_stocks` 사용 제거

- 장점: stale carry-over를 직접 차단한다.
- 단점: 같은 session 안에서 scanner ranking API가 비거나 일시 실패할 때의 안정적인 fallback도 함께 사라진다.

### 2. session transition에서 tracking cache 전체를 정리

- 장점: cache lifecycle을 session 경계와 정합화할 수 있다.
- 장점: 해외 fallback은 같은 session 안에서만 유지되고, 다음 session은 DB/holdings 기반 carry-over만 사용한다.
- 단점: transition 직후 첫 scan은 이전 runtime 종목 리스트를 재사용하지 못한다.

### 3. session-aware 키로 cache를 분리

- 장점: session별 cache 보존과 추적성을 동시에 얻는다.
- 단점: 현재 코드 구조와 테스트 표면에 비해 과하다. `active_stocks`, `scan_candidates`,
  `last_scan_time`, `_market_states`, playbook cache 전반을 더 크게 재구성해야 한다.

## Recommendation

접근 2를 채택한다. 이 이슈의 핵심은 stale session state 누수 방지이며, 현재 scanner fallback의
의도된 carry-over는 DB 최근 종목과 broker holdings로 충분히 유지된다.

## Policy

- market close에서는 기존 `_clear_realtime_market_runtime_state()`가 계속 담당한다.
- session transition에서는 `active_stocks`, `scan_candidates`, `last_scan_time`을 비운다.
- 동일 session 내 rescan에서는 기존 runtime cache carry-over를 계속 허용한다.
- 다음 session의 해외 universe carry-over는 DB recent symbols + current holdings에 한정한다.

## Test Strategy

- helper 단위 테스트로 session transition 시 tracking cache reset 조건을 고정한다.
- realtime `run()` 회귀 테스트로 `US_PRE -> US_REG` 전환 시
  `build_overseas_symbol_universe()`가 빈 `active_stocks`를 받는지 검증한다.
- 기존 market close cleanup 테스트가 계속 green인지 확인해 close policy 회귀를 막는다.
