# OOR-854 Dashboard Status Summary + Diagnostics Separation Design

## Context

- `main` 에는 `OOR-853` 결과가 이미 반영되어 있어서 결정 히스토리 필터와 LLM trace 조회는 사용할 수 있다.
- 현재 `/api/status` 는 시장별로 `trade_count`, `total_pnl`, `decision_count`, `playbook_status` 만 제공하고, 상단 카드는 `totals` 전역 합산만 사용한다.
- 현재 차트는 `fetch('/api/performance?market=all')`, `fetch('/api/pnl/history?days=...')` 고정 경로를 사용하고 있어 summary 와 시장 focus 를 공유하지 않는다.
- 현재 `Diagnostics` 는 접힘 구조지만 같은 페이지 본문에 있고, playbook/scorecard/scenario/context 가 decision history 와 별도 selector 집합으로만 나뉘어 있다.
- 이슈 요구는 “운영 상태를 한눈에 보는 메인 화면”과 “깊게 파고드는 진단 화면”을 분리하고, summary/차트/결정 히스토리 간 market 연동 규칙을 명시하는 것이다.

## Goals

- 상단 상태 요약을 시장별 운영 판단 중심으로 재정의한다.
- `Diagnostics` 를 메인 상태 화면과 명확히 분리된 surface 로 만든다.
- 메인 화면에서 `market` 차원의 focus 를 summary/chart/history 간 일관되게 공유한다.
- 이 연동 규칙을 코드, UI, 문서, 테스트로 고정한다.

## Non-Goals

- 새 SPA 프레임워크 도입
- 새로운 데이터 저장소/백엔드 서비스 추가
- playbook/scorecard/context 생성 파이프라인 자체 변경
- 이번 티켓에서 trace schema 또는 decision history API 범위 확장

## Options

### Option 1: Keep one page, strengthen section hierarchy only

- 현 HTML 한 페이지 안에서 summary 와 diagnostics 사이 시각적 경계만 더 강하게 준다.
- 장점: 마크업 변경이 가장 작다.
- 단점: “별도 진단 surface” 요구를 약하게만 충족하고, 운영자가 여전히 한 화면에서 고급 진단에 끌려 들어가게 된다.

### Option 2: Tabbed surfaces with shared overview market focus

- 같은 HTML 파일 안에서 `Overview` / `Diagnostics` surface 를 탭으로 분리한다.
- `/api/status` 를 풍부하게 만들어 시장별 운영 카드가 핵심 판단 기준을 보여주게 하고, 메인 surface 의 `market` focus 가 차트와 결정 히스토리에만 연동되게 한다.
- Diagnostics surface 는 독립 selector 를 유지하되 메인 focus 를 오염시키지 않는다.
- 장점: 요구 범위 안에서 분리가 명확하고, API/HTML/테스트 변경 폭이 제어 가능하다.
- 단점: 프런트엔드 상태 관리 코드가 늘어난다.

### Option 3: Separate `/diagnostics` route

- `/` 는 overview 전용, `/diagnostics` 는 고급 패널 전용으로 완전히 분리한다.
- 장점: 표면 분리가 가장 명확하다.
- 단점: 라우팅/내비게이션/테스트 범위가 불필요하게 커지고, 이번 티켓 요구에 비해 과하다.

## Recommendation

`Option 2` 를 선택한다.

이 티켓의 핵심은 데이터 저장 구조가 아니라 “운영자가 첫 화면에서 무엇을 보며, 언제 diagnostics 로 들어가는가”를 재구성하는 것이다. 탭형 surface 는 별도 진단 surface 요구를 충족하면서도 기존 정적 HTML + FastAPI 구조를 유지한다. 또한 `market` 차원의 linkage 만 메인 화면에서 공유하도록 규칙을 좁히면, summary/chart/history 는 함께 움직이되 diagnostics 독립성은 유지할 수 있다.

## Proposed Changes

### 1. Richer market status summary contract

- `src/dashboard/app.py`
  - `/api/status` 에 시장별 추가 필드를 제공한다.
  - 후보 필드:
    - `open_position_count`
    - `latest_decision_at`
    - `latest_decision_action`
    - `latest_session_id`
    - `current_pnl_pct`
    - `circuit_breaker_status`
    - `status_tone`
  - `status_tone` 는 시장 운영 판단용 요약 라벨이다.
    - `tripped`: circuit breaker tripped
    - `warning`: circuit breaker warning
    - `active`: open position 존재
    - `watching`: decision 은 있으나 포지션 없음
    - `ready`: playbook ready but 아직 활동 없음
    - `idle`: 신호 없음

### 2. Main surface information architecture

- `src/dashboard/static/index.html`
  - 상단에 `Overview` / `Diagnostics` surface switcher 를 추가한다.
  - 기존 전역 4카드를 시장별 operation cards + selected-market spotlight 구조로 재편한다.
  - 각 market card 는 다음을 보여준다:
    - status tone
    - playbook status
    - today trade/decision count
    - open positions
    - pnl / cb 상태
    - latest session/action
  - market card click 또는 main market selector 변경은 `activeOverviewMarket` 를 갱신한다.
  - `activeOverviewMarket` 는 아래 두 요소에만 연동된다.
    - P&L chart
    - decision history `market` filter

### 3. Diagnostics surface separation

- `Diagnostics` 는 별도 surface 로 옮긴다.
- playbook/scorecard/scenario/context 패널은 diagnostics 전용 설명문과 함께 유지한다.
- diagnostics surface 의 selector 는 기존처럼 독립적으로 동작하되 `activeOverviewMarket` 을 변경하지 않는다.

### 4. Filter linkage rules

- 메인 surface shared state:
  - `market` 만 summary/chart/history 가 공유한다.
- summary -> chart/history:
  - market card click 시 chart 와 decision history 의 `market` filter 가 함께 갱신된다.
- history -> summary/chart:
  - decision history `market` select 를 수동 변경하면 active market 이 같이 바뀐다.
- history 의 나머지 필터(`session_id`, `action`, `stock_code`, `min_confidence`, `from_date`, `to_date`, `matched_only`, `limit`)는 chart/summary 에 전파되지 않는다.
- diagnostics selectors:
  - playbook/scorecard/scenario/context selector 는 overview market state 와 독립이다.

## Documentation Plan

- `docs/architecture.md`
  - dashboard 섹션에 `Overview` / `Diagnostics` surface 분리와 filter linkage 규칙을 추가한다.
- `docs/commands.md`
  - `/api/status`, `/api/pnl/history`, `/api/decisions` 설명을 현재 linkage 모델에 맞게 보강한다.

## Testing Strategy

- `tests/test_dashboard.py`
  - `/api/status` 가 시장별 운영 판단 필드를 반환하는지 검증
  - index HTML 에 `Overview` / `Diagnostics` surface marker 와 market status 카드 구조가 노출되는지 검증
  - decision history market filter 와 overview focus 동기화를 위한 JS marker 를 검증
  - diagnostics surface 가 별도 안내 문구/selector 집합을 유지하는지 검증
- targeted regression
  - 기존 `decision history` rich filter, positions, status circuit breaker 테스트는 유지돼야 한다.

## Risks

- 프런트엔드 상태가 늘면서 selector 상호작용이 꼬일 수 있다.
  - `market` 만 shared state 로 제한하고, 나머지 필터는 directionality 를 끊어서 제어한다.
- status tone 계산이 과도하게 복잡해질 수 있다.
  - 기존 DB 에 이미 있는 신호만 사용해 단순한 우선순위 규칙으로 계산한다.
- diagnostics surface 를 별도 route 로 분리하지 않기 때문에 “분리 강도”가 약할 수 있다.
  - 탭 전환 + 전용 안내 문구 + 독립 selector 로 명확한 인지 경계를 만든다.
