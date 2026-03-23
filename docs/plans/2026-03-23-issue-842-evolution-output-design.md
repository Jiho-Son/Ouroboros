# OOR-842 Evolution Output Design

## Context

- `src/main.py` 의 `_handle_market_close()` 는 미국장 마감 시 `EvolutionOptimizer.evolve()` 를 호출한다.
- 현재 `src/evolution/optimizer.py` 는 LLM 응답으로 Python method body 를 생성하고, 이를 `src/strategies/v*_evolved.py` 로 저장한다.
- 재현 결과 `optimizer.evolve()` 1회 호출만으로 `src/strategies/v20260323_010607_evolved.py` 가 생성되어 `git status` 에 `??` 로 나타났고, 이 파일은 어떤 런타임 경로에서도 import 되지 않았다.
- 따라서 현재 구현은 main 브랜치를 더럽히지만 실제 기능에는 연결되지 않는 산출물을 만든다.

## Goals

- 진화 루프가 더 이상 `.py` 파일을 생성하지 않게 한다.
- 진화 결과는 운영 중 참고 가능한 구조화된 데이터로 남긴다.
- 미국장 마감 알림과 기존 진화 루프 호출 구조는 유지한다.
- 회귀 테스트로 “브랜치를 더럽히지 않는다”는 동작을 고정한다.

## Options

### Option A: L6 context 에 구조화된 evolution report 저장

- 장점: 기존 `ContextStore` / `contexts` 테이블을 그대로 활용할 수 있다.
- 장점: git worktree 와 분리되어 운영 중 산출물이 저장소를 오염시키지 않는다.
- 장점: daily scorecard 와 같은 날짜/시장 단위로 조회하기 쉽다.
- 단점: 실제 코드 패치 아티팩트는 남지 않는다.

### Option B: `data/` 아래 JSON/Markdown 파일 저장

- 장점: 구현이 단순하다.
- 장점: 사람이 직접 파일을 열어 확인하기 쉽다.
- 단점: 파일 기반 산출물이 또 늘어나고, 정리 정책이 따로 필요하다.
- 단점: 기존 context tree 와 중복된다.

### Option C: Python 파일은 유지하되 git-ignore 경로로 이동

- 장점: 수정량이 가장 적다.
- 단점: “진화를 위해서 Python 을 생성하지 말라”는 요구와 충돌한다.
- 단점: 사용되지 않는 코드 생성이라는 핵심 문제가 그대로 남는다.

## Decision

Option A 를 채택한다.

`EvolutionOptimizer` 는 더 이상 Python 코드를 생성하지 않고, 실패 패턴을 바탕으로 구조화된 JSON recommendation 을 LLM 에 요청한다. 생성된 recommendation 은 `contexts` 테이블의 `L6_DAILY` 레이어에 `evolution_<market>` 키로 저장하고, 호출자는 저장된 context key 를 포함한 report 를 받아 Telegram 알림에 사용한다.

## Proposed Shape

LLM 출력은 아래 JSON object 를 목표로 한다.

```json
{
  "summary": "One-sentence diagnosis",
  "adjustments": [
    "Actionable recommendation 1",
    "Actionable recommendation 2"
  ],
  "risk_notes": [
    "Risk note 1"
  ]
}
```

저장 report 는 아래 필드를 포함한다.

```json
{
  "title": "[Evolution] Daily recommendation: US_NASDAQ 2026-03-23",
  "status": "recorded",
  "context_key": "evolution_US_NASDAQ",
  "market": "US_NASDAQ",
  "date": "2026-03-23",
  "summary": "...",
  "adjustments": ["..."],
  "risk_notes": ["..."],
  "failure_patterns": {
    "...": "..."
  }
}
```

## Testing Strategy

- `tests/test_evolution.py`
  - failing test first: `generate_strategy()` 기반 파일 생성 계약을 recommendation report 저장 계약으로 교체
  - 진화 report 가 context 에 저장되는지 검증
  - invalid JSON / API error 에서 안전하게 `None` 을 반환하는지 검증
  - 전체 `evolve()` 가 `.py` 파일을 만들지 않고 report dict 를 반환하는지 검증
- `tests/test_main.py`
  - `_run_evolution_loop()` 알림이 `branch` 대신 `context_key` 를 사용하도록 조정
- 문서 동기화
  - `docs/architecture.md`, `docs/skills.md` 에서 “Python strategy file 생성” 설명을 context-based recommendation 으로 갱신

## Non-Goals

- 진화 recommendation 을 실제 주문 로직이나 playbook 에 자동 반영하지 않는다.
- dashboard 나 Telegram command 에 evolution report 조회 UI 를 추가하지 않는다.
