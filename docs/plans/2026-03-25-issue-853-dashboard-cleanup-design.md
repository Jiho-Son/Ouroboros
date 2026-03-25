# OOR-853 Dashboard Cleanup Design

## Context

- 이슈 요구는 세 가지다: 잘못되거나 불필요한 정보 노출 축소, 더 풍부한 필터, LLM request/response 조회.
- reviewer 코멘트는 이전 시도를 그대로 이어가지 말고 latest `origin/main` 기준으로 다시 구현하라는 의미다.
- 현재 `origin/main@af9cd97` 의 대시보드는 `현재 보유 포지션`, `최근 결정 로그`, `프리마켓 플레이북`, `일간 스코어카드`, `활성 시나리오 매칭`, `컨텍스트 트리` 를 모두 기본 노출한다.
- 현재 `/api/decisions` 는 `market` + `limit` 외 필터가 없고, `decision_logs` 스키마/`DecisionLogger`/`TradeDecision` 어디에도 `llm_prompt` / `llm_response` 가 없다.
- `origin/main` 에는 `4b7c880`, `af9cd97` 로 daily held-position / live hard-stop monitoring 변경이 새로 들어갔으므로, 이번 구현은 `src/main.py` 최신 동작을 유지한 채 대시보드·로그 확장만 얹어야 한다.

## Goals

- 대시보드 기본 화면을 상태 파악 중심으로 다시 정렬한다.
- 결정 히스토리에 시장, 세션, 액션, 심볼, 신뢰도, 날짜, 시나리오 매칭 기준 필터를 제공한다.
- 개별 결정에서 LLM request/response trace 와 핵심 판단 근거를 확인할 수 있게 한다.
- raw diagnostics 는 유지하되 기본 노출 강도를 낮춘다.

## Non-Goals

- 별도 SPA/다중 페이지 구조 도입
- 결정 review workflow 추가
- playbook/scorecard/context 저장 구조 자체 개편
- 이번 티켓 안에서 diagnostics 전면 재설계까지 확장

## Options

### Option 1: UI-only cleanup

- 프런트엔드만 바꿔 기존 `/api/decisions` 응답 위에 필터/상세 패널을 얹는다.
- 장점: 변경 범위가 가장 작다.
- 단점: 진짜 LLM request/response 는 저장되지 않아 요구를 충족하지 못한다.

### Option 2: Full audit slice in the current dashboard

- `TradeDecision` -> `DecisionLogger` -> `decision_logs` -> `/api/decisions` -> dashboard UI 경로를 함께 확장한다.
- 장점: 이슈 요구를 한 번에 충족하고, 최신 `src/main.py` 의 decision logging call site 에도 자연스럽게 맞는다.
- 단점: DB migration, API, UI, 테스트를 모두 수정해야 한다.

### Option 3: Separate trace explorer surface

- 현재 대시보드는 최소 수정만 하고, trace 전용 API/페이지를 새로 만든다.
- 장점: 책임 분리가 명확하다.
- 단점: 현재 티켓을 불필요하게 키우고, “대시보드 정비”라는 요구에서 오히려 멀어진다.

## Recommendation

`Option 2` 를 선택한다.

이 티켓은 “대시보드에서 상태를 읽기 쉽고, 필요한 경우 trace 를 바로 파고들 수 있게” 만드는 작업이다. UI-only 접근은 request/response 요구를 남기고, 별도 explorer 는 구조를 과도하게 늘린다. 현재 코드에는 이미 `DecisionLogger.log_decision()` 과 `/api/decisions` 라는 직선 경로가 있으므로, 최신 `main` 의 runtime 변경을 건드리지 않으면서 audit slice 를 다시 얹는 편이 가장 안전하다.

## Proposed Changes

### 1. Decision audit persistence

- `src/brain/decision_engine.py`
  - `TradeDecision` 에 optional `llm_prompt`, `llm_response` 필드를 추가한다.
  - provider 호출 성공 경로에서 prompt/raw response 를 결과에 남긴다.
- `src/decision_logging/decision_logger.py`
  - `DecisionLog` dataclass 와 insert/select 경로에 trace 필드를 추가한다.
- `src/db.py`
  - `decision_logs` 에 `llm_prompt TEXT`, `llm_response TEXT` migration 을 추가한다.
- `src/main.py`
  - regular/daily decision logging call site 에 `decision.llm_prompt`, `decision.llm_response` 를 함께 저장한다.
  - realtime hard-stop 처럼 LLM trace 가 없는 경로는 `None` 으로 유지한다.

### 2. Decision history API

- `src/dashboard/app.py` 의 `/api/decisions` 에 아래 query params 를 추가한다.
  - `market=all|<market>`
  - `session_id=all|<session>`
  - `action=all|BUY|SELL|HOLD`
  - `stock_code=<substring>`
  - `min_confidence=<int>`
  - `from_date=<YYYY-MM-DD>`
  - `to_date=<YYYY-MM-DD>`
  - `matched_only=<bool>`
  - `limit=<int>`
- 응답에는 `decisions`, `count` 외에 현재 필터와 distinct option metadata (`markets`, `sessions`) 를 포함한다.
- `matched_only` 는 `context_snapshot.scenario_match` 가 비어 있지 않은 row 만 남긴다.

### 3. Dashboard information architecture

- 상단 summary / positions / P&L chart 는 유지한다.
- “최근 결정 로그” 는 필터 바 + 결과 요약 + expandable trace 상세 구조로 재구성한다.
- 각 결정은 다음 정보를 제공한다.
  - 시각, 시장/세션, 액션, 심볼, 신뢰도
  - 짧은 rationale preview
  - `LLM request`, `LLM response`, `Decision context` block
- playbook/scorecard/scenario/context 패널은 `Diagnostics` details 섹션으로 내려 기본 접힘 상태로 둔다.

## Rework Delta

- 이전 시도는 `4b97525` 기준이었다.
- 이번 시도는 `af9cd97` 기준으로 다시 작성하며, 새로 추가된 daily held-position / live hard-stop monitoring 동작을 절대 약화하지 않는다.
- 따라서 `src/main.py` 변경은 trace 전달에 필요한 최소 인자 추가로 제한하고, 새 runtime 정책 분기나 monitor 흐름은 건드리지 않는다.

## Testing Strategy

- `tests/test_decision_engine.py`
  - `DecisionEngine.decide()` 가 prompt/raw response 를 `TradeDecision` 에 보존하는지 검증
- `tests/test_decision_logger.py`
  - `DecisionLogger.log_decision()` 이 trace 를 저장·조회하는지 검증
- `tests/test_dashboard.py`
  - `/api/decisions` 필터 조합, metadata, trace 필드 검증
  - HTML 에 filter bar, `LLM request` / `LLM response`, collapsed `Diagnostics` 노출 검증
- `tests/test_main.py`
  - 최신 runtime logging call site 가 trace 필드를 logger 로 전달하는지 최소 회귀를 추가한다

## Risks

- raw prompt/response 저장으로 `decision_logs` row 크기가 커질 수 있다.
  - 초기 대응은 SQLite TEXT 저장 + UI 접힌 상태 유지로 충분하다.
- `TradeDecision` 필드 확장은 wide call site 를 건드릴 수 있다.
  - optional default 필드로 추가해 기존 생성자 호출을 깨지 않도록 한다.
- `src/main.py` 최신 runtime changes 와 충돌할 수 있다.
  - 변경을 trace 인자 전달에만 국한하고, 관련 `tests/test_main.py` 회귀로 고정한다.
