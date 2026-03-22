# Issue-835 Cumulative Loss BUY Guard Design

## Goal

최근 N일 self-market scorecard 기준으로 누적 손실/연속 손실/저승률 구간을
판단하고, 해당 시장의 신규 BUY를 deterministic 하게 차단한다. 동시에 이
상황을 `PreMarketPlanner` 프롬프트에도 명시해 LLM 이 SELL/HOLD 중심으로
계획하도록 유도한다.

## Current Signal

- 재현 스니펫 기준 현재 `PreMarketPlanner.generate_playbook()` 는 연속 손실
  scorecard가 있어도 `actions=['BUY']` 를 유지한다.
- 동일 재현에서 `ContextStore.get_context()` 호출은 `scorecard_KR` 이전 하루와
  cross-market 하루만 조회해, 최근 N일 누적 손실 구간을 직접 평가하지 않는다.
- `src/main.py` 의 BUY 억제 가드들은 recent SELL, 보유 중복, stop-loss cooldown,
  intraday chase, kill switch 등 단일 세션/단일 주문 수준에 집중되어 있다.

## Options Considered

### Option 1. `src/main.py` 실행 직전 BUY 가드 추가

- 장점: 실행 직전 차단이라 가장 강한 deterministic gate 다.
- 장점: 저장된 playbook 이 있더라도 최종 BUY 억제를 보장한다.
- 단점: `ContextStore` 또는 scorecard 조회 경로를 runtime 호출 체인에 추가로
  전달해야 해 수정면이 커진다.
- 단점: LLM 프롬프트에는 누적 손실 상태가 계속 반영되지 않는다.

### Option 2. planner 프롬프트에만 최근 손실 요약 추가

- 장점: 수정량이 가장 작다.
- 장점: 사용자 코멘트처럼 LLM 이 최근 손실 상황을 직접 인지한다.
- 단점: 프롬프트-only 이므로 BUY 차단이 deterministic 하지 않다.
- 단점: acceptance criteria 의 실행 게이트 요구를 충족하지 못한다.

### Option 3. `PreMarketPlanner` 에 최근 scorecard guard를 추가하고 결과를 prompt + playbook 양쪽에 반영

- 장점: 최근 손실 상태를 LLM 에 전달하면서도 결과 playbook 에서 BUY 제거를
  deterministic 하게 강제할 수 있다.
- 장점: `tests/test_pre_market_planner.py` 중심으로 회귀를 고정할 수 있어 수정면이
  `src/strategy/pre_market_planner.py` + `src/config.py` 로 좁다.
- 장점: SELL/HOLD 시나리오와 기존 `src/main.py` kill switch/circuit breaker
  경로를 건드리지 않는다.
- 단점: playbook 외부에서 만들어진 별도 BUY 경로가 있다면 추가 runtime guard 가
  필요할 수 있다. 현재 티켓 범위에서는 playbook 기반 BUY 경로를 우선 고정한다.

## Chosen Design

Option 3을 사용한다.

- `src/config.py` 에 최근 scorecard BUY guard 설정을 추가한다.
  - `SCORECARD_BUY_GUARD_LOOKBACK_DAYS`
  - `SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL`
  - `SCORECARD_BUY_GUARD_MIN_WIN_RATE`
  - `SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS`
  - `SCORECARD_BUY_GUARD_ACTION` (`block_buy` | `defensive`)
- `PreMarketPlanner` 는 최근 N일 self-market scorecard window 를 읽어 guard 상태를
  계산한다.
- prompt 에 최근 scorecard window 요약과 guard status/reasons 를 추가한다.
- LLM 응답을 parse 한 뒤 guard 가 active 면 playbook post-processing 을 수행한다.
  - `block_buy`: BUY scenario 만 제거하고 SELL/HOLD/global rule 은 유지한다.
  - `defensive`: BUY scenario 제거 + `market_outlook` 을 최소
    `NEUTRAL_TO_BEARISH` 로 내리고 defensive global rule 을 보강한다.
- 기존 `_defensive_playbook()` 은 Gemini failure fallback 용으로 유지한다.

## Guard Contract

- guard 평가 대상은 같은 시장의 최근 `lookback_days` 일자 `scorecard_<MARKET>` 다.
- 최근 window 에서 아래 조건 중 하나라도 충족하면 guard 를 active 로 본다.
  - cumulative realized PnL `<= SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL`
  - average win rate `< SCORECARD_BUY_GUARD_MIN_WIN_RATE`
  - trailing consecutive loss days `>= SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS`
- BUY 차단은 playbook post-processing 으로 강제한다. 즉 LLM 이 BUY 를 반환해도
  최종 playbook 에는 BUY 가 남지 않는다.
- SELL/HOLD/global rule 은 제거하지 않는다. 기존 risk-reduction 경로를 약화하지
  않는 것이 우선이다.

## Testing Strategy

- `tests/test_pre_market_planner.py` 에 failing test 를 먼저 추가한다.
  - 연속 손실 scorecard window 에서 BUY scenario 가 제거되는지
  - `defensive` mode 에서 outlook/global rule 이 defensive 쪽으로 이동하는지
  - prompt 에 recent scorecard guard section 이 포함되는지
- `tests/test_config.py` 에 새 설정 validation/default 테스트를 추가한다.
- 구현 후 targeted pytest -> config pytest -> 관련 파일 `ruff` -> docs sync 순서로
  확인한다.
