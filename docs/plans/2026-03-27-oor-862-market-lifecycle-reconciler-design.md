# OOR-862 Market Lifecycle Reconciler Design

## Context

현재 realtime loop 는 lifecycle 판단 책임이 분산되어 있다.

- close 는 `_handle_realtime_market_closures()` 에서 `current_open_markets` 와 `_market_states` diff 로 처리한다.
- session transition 은 `_has_market_session_transition()` 반환값 하나로 처리한다.
- `_has_market_session_transition()` 는 이전 상태가 없는 첫 open 과 기존 open market 의 session 전환을 모두 `True` 로 취급한다.

그 결과 `opened`, `closed`, `session_changed` 가 서로 다른 lifecycle event 임에도
하나의 boolean 과 분산된 side-effect 로 묶여 있어, 로그/알림/테스트에서 per-market diff 결과를
일관되게 관측하기 어렵다.

재현 신호:

```bash
python3 - <<'PY'
from src.main import _has_market_session_transition
print('open_event_as_session_changed=', _has_market_session_transition({}, 'US_NASDAQ', 'US_PRE'))
print('session_transition=', _has_market_session_transition({'US_NASDAQ': 'US_PRE'}, 'US_NASDAQ', 'US_REG'))
PY
```

두 경우 모두 `True` 가 출력되어 첫 open 과 session transition 이 구분되지 않는다.

## Approaches

### 1. 기존 helper 를 유지하고 분기만 추가

- 장점: diff 범위가 작다.
- 단점: close 는 별도 helper, session/open 은 inline 분기인 구조가 유지된다.
- 단점: 이후 lifecycle callback 이 더 늘어나면 책임 분산이 계속 심해진다.

### 2. per-market lifecycle reconciler 를 도입해 loop 시작 시 diff snapshot 을 계산한다

- 장점: `opened`, `closed`, `session_changed` 를 같은 입력/출력 contract 로 계산할 수 있다.
- 장점: close callback, session transition callback, 로그/알림 포맷을 event 기반으로 일관화할 수 있다.
- 장점: 다중 마켓 동시 운영 테스트에서 diff 결과를 직접 검증하기 쉽다.
- 단점: `get_session_info()` 를 market 처리 전에도 계산해야 하므로 loop 구조를 약간 재배치해야 한다.

### 3. session-aware runtime cache 전체를 별도 state object 로 재구성한다

- 장점: long-term 으로는 가장 깔끔한 구조가 될 수 있다.
- 단점: 이번 티켓 acceptance criteria 대비 범위가 과하다.
- 단점: trading cycle, scanner, playbook cache 전부를 더 크게 건드리게 된다.

## Recommendation

접근 2를 채택한다. 이번 티켓은 개별 마켓 lifecycle event 를 독립적으로 계산하고, 그 결과를
후속 callback 과 관측 포맷에 연결하는 것이 핵심이다. 별도 lifecycle reconciler 를 두면
OOR-858 close handling 과 OOR-861 session transition cleanup 을 한 contract 아래로 정리할 수 있다.

## Lifecycle Contract

- 입력:
  - previous snapshot: `dict[str, str]` (`market_code -> previous_session_id`)
  - current snapshot: `dict[str, str]` (`market_code -> current_session_id`)
  - current market metadata: `dict[str, MarketInfo]`
- 출력:
  - `opened`: 이전 snapshot 에 없고 현재 snapshot 에 있는 market
  - `closed`: 이전 snapshot 에 있고 현재 snapshot 에 없는 market
  - `session_changed`: 이전/현재 snapshot 모두 존재하며 session_id 가 달라진 market

정책:

- `opened` 는 첫 활성화 이벤트다. `session_changed` 에 포함하지 않는다.
- `session_changed` 는 기존 활성 market 의 session identity 변경만 의미한다.
- `closed` 는 해당 market 의 close callback 과 runtime cleanup 을 즉시 트리거한다.
- 동일 loop 에서 여러 market 의 event 는 서로 독립적으로 계산/적용한다.

## Callback / Observability Policy

- open:
  - `notify_market_open()` 유지
  - `logger.info("Market lifecycle opened: ...")`
- close:
  - 기존 `_handle_market_close()` 유지
  - `logger.info("Market lifecycle closed: ...")`
- session transition:
  - 별도 session transition callback helper 로 playbook refresh, tracking cache cleanup, 알림/로그를 처리
  - `logger.info("Market lifecycle session_changed: ... previous=... current=...")`
  - Telegram 에도 session transition 전용 메시지를 보낸다

## Test Strategy

- helper 단위 테스트:
  - 첫 open 이 `opened` 로만 분류되고 `session_changed` 로는 분류되지 않는지
  - close/open/session change 가 같은 diff 에서 동시에 독립적으로 계산되는지
- realtime loop 회귀 테스트:
  - 일부 market close 와 다른 market session transition 이 같은 loop 에서 함께 처리되는지
  - session transition 에서 tracking cache cleanup 과 fresh playbook policy 가 유지되는지
- Telegram formatting 테스트:
  - session transition 알림 포맷이 open/close 와 구분되는지
