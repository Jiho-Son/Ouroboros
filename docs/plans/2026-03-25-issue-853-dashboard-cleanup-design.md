# OOR-853 Dashboard Cleanup Design

## Context

- 현재 대시보드는 상태 파악용 핵심 정보와 진단용 raw 정보가 한 화면에 함께 노출된다.
- `src/dashboard/static/index.html` 는 결정 로그를 시장 탭 + 최근 50건 수준으로만 좁혀 보여준다.
- `src/dashboard/app.py` 의 `/api/decisions` 는 `market` 과 `limit` 외 필터가 없고, `decision_logs` 스키마에는 `llm_prompt` / `llm_response` 가 없다.
- 따라서 "상태를 알기 쉬운 대시보드", "더 풍부한 필터", "LLM request/response 확인" 요구를 동시에 만족하려면 UI, API, 로그 저장 경로를 함께 손봐야 한다.

## Goals

- 메인 화면을 상태 파악 중심으로 재구성한다.
- 결정 히스토리에 시장, 세션, 액션, 심볼, 신뢰도, 날짜, 시나리오 매칭 기준 필터를 제공한다.
- 개별 결정에서 LLM request/response trace 를 볼 수 있게 한다.
- 기존 playbook/scorecard/scenario/context 패널은 진단 용도로만 유지하고 기본 노출 강도를 낮춘다.

## Non-Goals

- 별도 라우트 추가 또는 다중 페이지 구조 도입
- 결정 히스토리 편집/리뷰 워크플로우 추가
- playbook/scorecard 데이터 모델 자체 개편

## Options

### Option 1: UI-only cleanup

- 기존 `/api/decisions` 응답만 사용해 프런트엔드 필터와 detail panel 만 추가한다.
- 장점: 변경 범위가 가장 작다.
- 단점: 실제 raw LLM request/response 는 저장되지 않아 요구를 정확히 만족하지 못한다.

### Option 2: Full audit slice in the current dashboard

- `decision_logs` 에 `llm_prompt` / `llm_response` 를 추가한다.
- `DecisionEngine` 가 생성한 prompt/raw response 를 `DecisionLogger` 로 저장한다.
- `/api/decisions` 에 풍부한 필터와 메타데이터를 추가하고, UI는 결정 히스토리 중심으로 재구성한다.
- 장점: 이번 티켓 요구를 한 번에 맞춘다.
- 단점: DB migration, 런타임 로깅, UI를 함께 수정해야 한다.

### Option 3: Separate trace explorer surface

- 현재 대시보드는 최소 수정만 하고, trace 전용 API/페이지를 새로 만든다.
- 장점: 책임 분리가 명확하다.
- 단점: 현재 티켓 범위를 불필요하게 키우고 "대시보드 정비" 흐름과 분리된다.

## Recommendation

`Option 2` 를 선택한다.

이 티켓의 핵심은 대시보드에서 상태를 빠르게 읽고, 필요할 때 결정 근거와 LLM trace 를 바로 파고들 수 있게 만드는 것이다. UI-only 접근은 request/response 요구를 남기고, 별도 explorer 는 과도하다. 현재 코드 구조에서는 `TradeDecision` -> `DecisionLogger` -> `/api/decisions` 경로가 이미 있으므로 여기에 trace 필드를 추가하는 편이 가장 직선적이다.

## Proposed Changes

### 1. Decision audit schema

- `decision_logs` 에 `llm_prompt TEXT` 와 `llm_response TEXT` 를 추가한다.
- `DecisionLog` dataclass 와 `DecisionLogger.log_decision()` / 조회 메서드에 두 필드를 반영한다.
- `TradeDecision` 에 optional `llm_prompt` / `llm_response` 필드를 추가한다.
- `DecisionEngine.decide()` 는 실제 provider 호출에 사용한 prompt 와 raw response 를 결과에 담는다.
- `src/main.py` 의 decision logging 호출부는 해당 필드를 함께 기록한다.

### 2. Decision history API

- `/api/decisions` query params:
  - `market=all|<market>`
  - `session_id=all|<session>`
  - `action=all|BUY|SELL|HOLD`
  - `stock_code=<substring>`
  - `min_confidence=<int>`
  - `from_date=<YYYY-MM-DD>`
  - `to_date=<YYYY-MM-DD>`
  - `matched_only=<bool>`
  - `limit=<int>`
- 응답은 `decisions` 외에 현재 필터와 distinct option metadata (`markets`, `sessions`) 및 `count` 를 포함한다.
- `matched_only` 는 `context_snapshot.scenario_match` 가 비어 있지 않은 row 만 남긴다.

### 3. Dashboard information architecture

- 상단 summary / positions / P&L chart 는 유지한다.
- "최근 결정 로그" 는 카드형 timeline + 필터 바로 교체한다.
- 각 결정 카드에는:
  - 시각, 시장/세션, 액션, 심볼, 신뢰도, outcome
  - 짧은 rationale preview
  - expandable `LLM request`, `LLM response`, `Decision context` block
- 기존 playbook/scorecard/scenario/context 패널은 `Diagnostics` details 섹션 내부로 이동해 기본 접힘 상태로 둔다.

## Testing Strategy

- `tests/test_decision_engine.py`
  - provider 호출 후 `TradeDecision` 가 prompt/raw response 를 보존하는지 검증
- `tests/test_decision_logger.py`
  - `llm_prompt` / `llm_response` 저장 및 조회 검증
- `tests/test_dashboard.py`
  - `/api/decisions` 필터 조합
  - trace 필드 노출
  - HTML 에 새 필터/trace 진입점/diagnostics 섹션 노출 검증

## Risks

- prompt/response 저장으로 `decision_logs` row 크기가 커질 수 있다.
  - 1차는 SQLite TEXT 저장으로 충분하고, UI는 접힌 상태로만 노출해 초기 렌더 비용을 낮춘다.
- 기존 runtime path 가 `TradeDecision` 추가 필드를 기대하지 않는지 확인이 필요하다.
  - optional default 필드로 추가해 call site break 를 피한다.
