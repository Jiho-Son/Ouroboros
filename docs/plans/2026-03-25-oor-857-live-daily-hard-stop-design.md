# OOR-857 Live Daily Hard-Stop Design

## Context

`src/main.py` 는 realtime websocket hard-stop client startup 을
`settings.TRADE_MODE == "realtime"` 로 제한한다. 그 결과 live +
`TRADE_MODE=daily` 런타임은 batch cadence 사이에 held position 을 websocket
hard-stop 으로 보호하지 못한다.

추가로 daily path 는 `_process_daily_session_stock()` 까지
`RealtimeHardStopMonitor` / `KISWebSocketClient` 를 전달하지 않아서, startup
guard 만 풀어도 held position tracking register / subscribe 가 daily batch 에서
실행되지 않는다.

## Goals

- live + `TRADE_MODE=daily` 에서 supported held position 이 있으면 realtime
  hard-stop websocket monitoring 이 startup 되고 subscription sync 까지 이어지게
  한다.
- entry cadence(`daily` vs `realtime`)와 hard-stop safety coverage 를 분리한다.
- 기존 realtime mode 동작과 unsupported market behavior 는 유지한다.

## Non-Goals

- hard-stop threshold 계산 로직 변경
- realtime entry cadence 도입
- unsupported market 을 websocket 지원 시장으로 확장

## Options

### Option 1. Startup guard 만 완화

- `MODE=live` + `REALTIME_HARD_STOP_ENABLED` 일 때 client/task 를 시작한다.
- 장점: 변경면이 가장 좁다.
- 단점: daily path 는 monitor/client 를 사용하지 않으므로 held position sync 가
  되지 않는다. startup 로그만 생기고 실제 protection 은 비어 있을 수 있다.

### Option 2. Startup + daily path plumbing 공유

- live + enabled 조건에서 websocket client/task 를 시작한다.
- `run_daily_session()` -> `_run_daily_session_market()` ->
  `_process_daily_session_stock()` 에 monitor/client 를 전달한다.
- daily HOLD evaluation 에서도 `_sync_realtime_hard_stop_monitor()` 를 호출해
  held position register / subscribe / remove 계약을 realtime path 와 맞춘다.
- 장점: ticket이 요구하는 실제 보호 경로를 만족한다.
- 단점: daily path 시그니처와 테스트 수정이 필요하다.

### Option 3. daily mode 금지 정책으로 문서화

- daily mode 에서는 realtime hard-stop 을 명시적으로 금지하고 강한 warning 을
  추가한다.
- 장점: 구현 범위가 작다.
- 단점: 현재 production incident 와 acceptance criteria 를 해결하지 못한다.
  safety baseline 도 후퇴한다.

## Decision

Option 2 를 채택한다.

이유:

- root cause 가 startup guard 하나가 아니라 daily path wiring 부재까지 포함한다.
- realtime hard-stop 은 entry cadence 와 독립된 risk control 이어야 한다.
- 기존 `_sync_realtime_hard_stop_monitor()` 계약을 재사용하면 새로운 분기 대신
  기존 behavior 를 daily path 에도 동일하게 적용할 수 있다.

## Proposed Changes

1. websocket startup 조건을 helper 로 추출해 live runtime 에서
   `REALTIME_HARD_STOP_ENABLED` 이고 supported market 이 하나라도 있으면
   daily/realtime 모두 client/task 를 시작하게 한다.
2. daily session functions 에 `realtime_hard_stop_monitor` 와
   `realtime_hard_stop_client` optional parameter 를 추가한다.
3. `_process_daily_session_stock()` 의 HOLD branch 에서 staged-exit evidence 적용
   후 `_sync_realtime_hard_stop_monitor()` 를 호출한다.
4. live-trading checklist / architecture 문서에 live daily mode 의 operator
   expectation 과 observed log evidence 를 추가한다.

## Testing Strategy

- test-first 로 live + daily + enabled settings 에서 websocket startup predicate 가
  참이어야 함을 고정한다.
- daily stock/session test 에서 held position 이 realtime hard-stop sync/register 를
  수행하는지 확인한다.
- 기존 realtime startup log contract 는 유지한다.

## Approval Basis

무인 세션이므로 별도 human approval 을 기다리지 않고, Linear ticket의
Acceptance Criteria 를 설계 승인 기준으로 사용한다.
