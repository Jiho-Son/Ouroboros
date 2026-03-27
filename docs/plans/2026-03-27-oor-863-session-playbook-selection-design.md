# OOR-863 Session-Scoped Playbook Selection Design

## Context

`OOR-860` 이후 `DayPlaybook.session_id` 와 `playbooks(date, market, session_id, slot)`
저장은 가능해졌지만, runtime selection policy 는 아직 `main.py` 의 세션 타입별
hard-coded rule 에 묶여 있다.

현재 관측 신호:

- realtime restart helper `_load_stored_playbook_for_session()` 는
  `US_REG` / `KRX_REG` 에서 stored current-session playbook 이 있어도 무조건
  `None` 을 반환한다.
- daily helper `_load_or_generate_daily_playbook()` 는 `slot="open"` 만 읽어서
  같은 session 안에서 더 최신인 mid refresh playbook 이 있어도 재시작 시
  재사용하지 못한다.
- `slot` 은 refresh timing metadata 여야 하는데, selection branch 가 여전히
  “어떤 상황에서 DB playbook 을 써도 되는가” 판단까지 함께 떠안고 있다.

이 결과로 “같은 session 재시작”과 “새 session 진입”이 구분되지 않는다.

## Approaches

### 1. 세션 타입별 allow/deny 분기 추가

- `_should_reuse_stored_playbook()` 에 분기를 더 추가해 `US_REG`, `KRX_REG`,
  pre-market 를 계속 예외 처리한다.
- 장점: 변경 범위가 작다.
- 단점: restart 와 transition 을 끝내 분리하지 못하고, session 종류가 늘수록
  규칙이 다시 `market + session` 하드코딩으로 비대해진다.

### 2. Selection intent 를 명시적으로 도입한다

- store 는 “현재 session 에서 최신 playbook 과 refresh timing metadata” 를
  반환하고, runtime 은 “resume current session” vs “force fresh on live
  transition” intent 로 reuse 여부를 결정한다.
- 장점: ticket 요구사항인 fresh generation / stored reuse policy 를 명시적 API
  로 표현할 수 있다.
- 장점: `slot` 을 identity 가 아니라 refresh timing metadata 로 축소할 수 있다.
- 장점: realtime restart 와 live session transition 을 서로 다른 규칙으로 다룰 수 있다.
- 단점: store/runtime/test 를 함께 건드려야 한다.

### 3. `slot` 을 새 컬럼으로 완전히 분리한다

- `slot` 을 폐기하고 `refresh_phase` 같은 별도 컬럼으로 전면 migration 한다.
- 장점: 모델 의미가 가장 명확하다.
- 단점: 현재 acceptance criteria 대비 migration 비용이 크고 dashboard/telegram/query
  surface 전체를 같이 바꿔야 해서 scope 가 커진다.

## Recommendation

접근 2를 채택한다.

이번 티켓의 핵심은 “session-aware persistence 가 이미 있는 상태에서 selection
policy 를 명시적으로 만드는 것”이다. 따라서 schema 대수술보다 runtime intent 를
분리하는 편이 ticket scope 와 회귀 위험에 가장 맞다.

## Design

### Data Model / Store

- `DayPlaybook.session_id` 는 유지한다.
- DB unique key `UNIQUE(date, market, session_id, slot)` 는 유지한다.
- `slot` 은 “open generation / mid refresh” 를 나타내는 refresh timing metadata 로
  문서화한다. selection identity 는 `date + market + session_id` 다.
- `PlaybookStore` 에 metadata-aware read API 를 추가한다.
  - 예: `load_entry(...)`, `load_latest_entry(...)`
  - 반환값에는 `playbook`, `slot`, `generated_at` 가 함께 들어간다.
- 기존 `load(...)`, `load_latest(...)` 는 호환성 유지용 thin wrapper 로 남긴다.

### Selection Policy

- runtime 에서 아래 두 intent 를 명시적으로 구분한다.
  - `resume_current_session`: 재시작/재진입. current session row 가 있으면 latest
    current-session playbook 재사용, 없으면 fresh generation.
  - `force_fresh_on_transition`: live session transition 직후. current session row 가
    DB 에 이미 있더라도 fresh generation.
- selection helper 는 store metadata entry 와 intent 를 받아 최종 결정을 만든다.
- decision 결과는 최소한 아래 정보를 포함한다.
  - `action`: `reuse_stored` | `generate_fresh`
  - `reason`
  - `stored_entry` (있을 때)

### Realtime Flow

- 프로세스 기동 직후 cached playbook 이 없을 때는 `resume_current_session` 으로
  current session row 를 찾는다.
- live session transition event 발생 시에는 해당 market 을 `force_fresh` 대상으로
  표시하고, 캐시를 비운다.
- 다음 루프에서 해당 market 은 store 를 보더라도 fresh generation 을 수행한다.
- fresh generation 또는 current-session fallback cache 구축이 끝나면 force flag 를 제거한다.
- `mid_refreshed` 복구는 store entry 의 `slot == "mid"` 로 판단한다.

### Daily Flow

- `_load_or_generate_daily_playbook()` 는 `slot="open"` 고정 lookup 대신
  current session 기준 `load_latest_entry()` 를 사용한다.
- 같은 session 안에서 mid refresh 가 이미 저장돼 있으면 restart 후 그 playbook 을 재사용한다.

### Migration Path

- 기존 migration(`session_id` backfill, unique rebuild)은 유지한다.
- 추가 schema breaking change 는 없다.
- 새 store metadata API 는 기존 row 포맷을 그대로 사용하므로 legacy-compatible 하다.

## Testing Strategy

- `tests/test_playbook_store.py`
  - `load_latest_entry()` 가 같은 session 에서 mid 를 우선하고 slot metadata 를 함께 반환하는지 검증
- `tests/test_main.py`
  - realtime restart 에서 stored `US_REG` playbook 을 current session 재사용하는지 검증
  - live session transition 에서 stored current-session playbook 이 있어도 fresh generation 하는지 검증
  - daily helper 가 same-session latest(mid 우선)를 재사용하는지 검증
- 회귀 기준
  - 다른 session row 는 어떤 경로에서도 current session selection 에 섞이면 안 된다
  - mid refresh 는 여전히 “정오 이후 1회” semantics 를 유지해야 한다
