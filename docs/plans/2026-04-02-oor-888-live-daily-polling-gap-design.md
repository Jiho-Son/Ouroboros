# OOR-888 Live Daily Polling Gap Design

## Context

현재 `src/main.py` 의 daily loop 는 batch 하나를 실행한 뒤 다음 batch 를
`SESSION_INTERVAL_HOURS` 뒤로 잡는다. `OOR-840` 과 `OOR-886` 은 이 cadence 가
late-start regular session 에서 마지막 regular-session 기회를 놓칠 수 있다는 점을
warning/log 로 드러냈고, `OOR-865` 는 `US_PRE -> US_REG` 전환처럼 아직 regular
session 에 진입하지 않은 경우에만 catch-up next batch 를 앞당겼다.

하지만 live + `TRADE_MODE=daily` 가 이미 regular session 내부에서 시작한 경우에는
entry/staged-exit 평가가 여전히 다음 6시간 batch 까지 기다린다. `OOR-857` 로
추가된 websocket hard-stop 은 held-position 급락 방어만 담당하므로, 일반 entry 와
polling-based staged exit responsiveness gap 은 그대로 남는다.

이 무인 세션은 별도 human approval 을 기다리지 않고, Linear issue body 의
Acceptance Criteria 를 설계 승인 기준으로 사용한다.

## Goals

- `MODE=live` + `TRADE_MODE=daily` 에서 현재 열린 시장이 이미 regular session 이면
  다음 entry/staged-exit 평가 간격을 `RESCAN_INTERVAL_SECONDS` 이내로 제한한다.
- hard-stop websocket ownership 과 semantics 는 그대로 유지한다.
- paper daily mode 와 non-regular-session cadence, 그리고 기존
  `US_PRE -> US_REG` catch-up behavior 는 유지한다.

## Non-Goals

- websocket callback 에서 direct BUY/SELL 을 실행하는 realtime entry/staged-exit 도입
- paper daily mode cadence 변경
- `src/core/risk_manager.py` 또는 hard-stop threshold 계산 정책 변경
- unsupported market 을 새 websocket/polling 특례 대상으로 확장

## Options

### Option 1. Warning-only / stronger operator restriction

- 현재처럼 startup-anchored cadence 를 유지하고, live daily mode 에서 late-start
  regular session 위험을 더 강한 warning/체크리스트로만 문서화한다.
- 장점: 구현 범위가 가장 작다.
- 단점: issue 가 요구한 entry/staged-exit responsiveness gap 을 실제로 줄이지
  못한다. 이미 `OOR-840`/`OOR-886` 에서 observability 는 충분히 보강된 상태라
  추가 warning 만으로는 이번 티켓의 의미가 약하다.

### Option 2. Live-daily regular-session poll cap using `RESCAN_INTERVAL_SECONDS`

- 기존 daily batch loop 를 유지하되, `MODE=live` 이고 현재 열린 시장 중 하나라도
  regular session 이면 다음 batch 시각을 아래 후보의 최소값으로 계산한다.
  - 기본 cadence: `batch_completed_at + SESSION_INTERVAL_HOURS`
  - 기존 regular-session catch-up: `US_PRE -> US_REG` 류의 첫 regular-session 진입
  - live regular-session poll cap: `batch_completed_at + RESCAN_INTERVAL_SECONDS`
- 장점: entry/staged-exit 평가를 기존 polling path 안에서 더 자주 수행하므로
  scenario/playbook/decision logging ownership 이 한 곳에 남는다.
- 장점: existing websocket hard-stop path 와 책임이 섞이지 않는다.
- 단점: live daily mode 에서 scanner/playbook 재평가 빈도가 올라가므로 API/LLM
  사용량이 paper daily 보다 커진다.

### Option 3. Websocket-driven entry/staged-exit evaluation

- hard-stop websocket price event 마다 staged-exit/entry 를 바로 계산하고 주문까지
  실행한다.
- 장점: 가장 빠른 반응성을 줄 수 있다.
- 단점: ATR/model/liquidity 입력과 playbook/scenario semantics 가 websocket cadence 와
  자연스럽게 맞지 않는다. `OOR-459` 에서 이미 rejected 되었던 "full staged-exit
  evaluation on every websocket event" 문제를 다시 가져오게 된다.

## Decision

Option 2 를 채택한다.

이유:

- 이번 티켓의 요구는 "warning 강화"가 아니라 live daily mode 의 실제 polling gap
  축소다.
- 기존 `run_daily_session()` path 는 scanner, playbook reuse, staged-exit evidence,
  order policy, decision/trade logging 을 한 곳에서 관리한다. regular-session poll
  cap 은 이 단일 ownership 을 유지한다.
- `RESCAN_INTERVAL_SECONDS` 는 이미 live trading 중 scanner 재평가 cadence 로 쓰이고
  있어, live daily mode regular-session poll cap 의 운영 의미와도 맞다.
- websocket path 를 건드리지 않으므로 hard-stop 과 favorable exit/entry semantics 가
  뒤섞이지 않는다.

## Proposed Changes

1. `src/main.py` 에 live-daily regular-session poll 후보 시각을 계산하는 helper 를
   추가하거나 `_resolve_daily_mode_next_batch_at()` 을 확장한다.
2. daily next-batch resolver 가 기본 cadence / 기존 catch-up / live regular-session
   poll cap 의 최소값을 반환하게 한다.
3. late-start regular-session live daily regression 을 `tests/test_main.py` 에 추가해
   `Next session in` 이 6시간이 아니라 `RESCAN_INTERVAL_SECONDS` 기준으로 줄어드는
   동작을 고정한다.
4. `docs/architecture.md` 와 `docs/live-trading-checklist.md` 에 live daily mode 의
   regular-session polling contract 와 hard-stop responsibility boundary 를 반영한다.

## Testing Strategy

- helper-level regression:
  late-start `KR` regular session 에서 live daily poll cap 이
  `batch_completed_at + RESCAN_INTERVAL_SECONDS` 를 선택하는지 확인
- run loop regression:
  live + daily + `KR` late-start runtime 이 close 전 두 번째 batch 를 실제로 예약하고,
  `last_regular_batch_markets=KR` warning 계약 대신 더 짧은 `phase=4` next batch 를
  남기는지 확인
- existing baseline regression:
  paper daily warning/deferred close phase tests 와 live daily hard-stop startup test 가
  그대로 PASS 해야 함

## Documentation Impact

- `docs/architecture.md`:
  live daily mode regular-session entry/staged-exit cadence 가
  `RESCAN_INTERVAL_SECONDS` 로 capped 된다는 점을 명시
- `docs/live-trading-checklist.md`:
  operator 가 live daily mode 에서 hard-stop websocket 과 별도로
  regular-session polling cadence 단축을 확인해야 한다는 점을 추가
