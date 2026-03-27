# OOR-859 US Session DST Design

## Context

`src/core/order_policy.py` 의 US 세션 분류는 `Asia/Seoul` 고정 시각표를 직접 비교한다. 이 방식은 겨울철에는 우연히 맞지만, 미국 DST 적용 후에는 `US_PRE`, `US_REG`, `US_AFTER` 경계가 1시간 늦게 판정된다.

현재 재현 신호:

- `2026-03-09T13:30:00Z` (`2026-03-09 09:30 EDT`) -> 현재 `US_PRE`, 기대 `US_REG`
- `2026-03-09T20:00:00Z` (`2026-03-09 16:00 EDT`) -> 현재 `US_REG`, 기대 `US_AFTER`
- `2026-03-09T21:30:00Z` (`2026-03-09 17:30 EDT`) -> 현재 extended session active, 기대 closed

## Approaches

### 1. KST 윈도우를 DST 시즌마다 동적으로 보정

- 장점: 현재 구현 구조를 거의 유지한다.
- 단점: 핵심 기준축이 여전히 `Asia/Seoul` 이라서 `schedule.py` 의 `America/New_York` 와 계속 어긋날 여지가 있다.

### 2. US 세션을 `America/New_York` 현지 시각으로 직접 분류

- 장점: regular market schedule (`09:30-16:00`) 와 같은 시간축을 사용한다.
- 장점: DST 보정이 `zoneinfo` 에 의해 자동 처리된다.
- 단점: 기존 KST 설명/주석 일부를 현지 시각 기준으로 바꿔야 한다.

### 3. 세션 분류를 `src/markets/schedule.py` 로 완전히 이동

- 장점: 스케줄 관련 책임을 한 곳으로 더 강하게 모은다.
- 단점: 이번 버그 수정 범위를 넘는 구조 변경이며, 호출부와 테스트 수정 폭이 커진다.

## Decision

Approach 2를 채택한다.

이번 티켓의 핵심은 "regular schedule 과 extended-session classifier 가 동일한 거래소 시간축을 사용" 하도록 만드는 것이다. `MarketInfo.timezone`, `open_time`, `close_time` 가 이미 `schedule.py` 에 있으므로, US classifier 는 이를 기준으로 현지 시각을 직접 해석하는 편이 가장 단순하고 안전하다.

## Session Model

US 세션은 `America/New_York` 현지 시각 기준으로 아래처럼 분류한다.

- `US_DAY`: `20:00 <= local < 04:00`
- `US_PRE`: `04:00 <= local < market.open_time` (`09:30`)
- `US_REG`: `market.open_time <= local < market.close_time` (`16:00`)
- `US_AFTER`: `market.close_time <= local < 17:00`
- 그 외: `US_OFF`

이 모델은 기존 겨울철 KST 분류가 암묵적으로 표현하던 현지 세션 의미를 그대로 유지하면서, DST 전환 시 `zoneinfo` 가 UTC 오프셋만 자동 조정하도록 만든다.

## Scope

- 수정 대상
  - `src/core/order_policy.py`
  - `tests/test_order_policy.py`
  - `tests/test_market_schedule.py`
  - 필요 시 `docs/architecture.md`
- 비대상
  - 세션 ID 체계 변경
  - `US_AFTER` 길이 재정의
  - runtime/risk 정책 완화

## Validation Strategy

- TDD로 DST 시작/종료 경계 regression test 를 먼저 추가한다.
- `get_open_markets(..., include_extended_sessions=True)` 가 `2026-03-09 17:30 EDT` 를 closed 로 판정하는지 확인한다.
- 관련 타깃 테스트와 `ruff check src/ tests/` 를 실행한다.
