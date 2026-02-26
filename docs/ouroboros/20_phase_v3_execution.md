<!--
Doc-ID: DOC-PHASE-V3-001
Version: 1.0.0
Status: active
Owner: strategy
Updated: 2026-02-26
-->

# v3 실행 지시서 (세션 확장)

참조 요구사항: `REQ-V3-001` `REQ-V3-002` `REQ-V3-003` `REQ-V3-004` `REQ-V3-005` `REQ-V3-006` `REQ-V3-007` `REQ-V3-008` `REQ-OPS-001` `REQ-OPS-002` `REQ-OPS-003`

## 단계 1: 세션 엔진

- `TASK-V3-001`: `session_id` 분류기 구현(KR/US 확장 세션)
- `TASK-V3-002`: 세션 전환 훅에서 리스크 파라미터 재로딩 구현
- `TASK-V3-003`: 로그/DB 스키마에 `session_id` 필드 강제

완료 기준:
- `REQ-V3-001`, `REQ-V3-002` 충족

## 단계 2: 블랙아웃/복구 제어

- `TASK-V3-004`: 블랙아웃 윈도우 정책 로더 구현(설정 기반)
- `TASK-V3-005`: 블랙아웃 중 신규 주문 차단 + 의도 큐 적재 구현
- `TASK-V3-006`: 복구 시 동기화(잔고/미체결/체결) 후 큐 재검증 실행

완료 기준:
- `REQ-V3-003`, `REQ-V3-004` 충족

## 단계 3: 주문 정책 강화

- `TASK-V3-007`: 세션별 주문 타입 매트릭스 구현
- `TASK-V3-008`: 저유동 세션 시장가 주문 하드 차단
- `TASK-V3-009`: 재호가 간격/횟수 제한 및 주문 철회 조건 구현

완료 기준:
- `REQ-V3-005` 충족

## 단계 4: 비용/체결 모델 정교화

- `TASK-V3-010`: 세션별 슬리피지/비용 테이블 엔진 반영
- `TASK-V3-011`: 불리한 체결 가정(상대 호가 방향) 체결기 구현
- `TASK-V3-012`: 시나리오별 체결 실패/부분체결 모델 반영

완료 기준:
- `REQ-V3-006` 충족

## 단계 5: 환율/오버나잇/Kill Switch 연동

- `TASK-V3-013`: 전략 PnL과 FX PnL 분리 회계 구현
- `TASK-V3-014`: USD/KRW 버퍼 규칙 위반 시 신규 진입 제한 구현
- `TASK-V3-015`: 오버나잇 예외와 Kill Switch 우선순위 통합

완료 기준:
- `REQ-V3-007`, `REQ-V3-008` 충족

라우팅:
- 코드 지시 상세: [30_code_level_work_orders.md](./30_code_level_work_orders.md)
- 테스트 상세: [40_acceptance_and_test_plan.md](./40_acceptance_and_test_plan.md)
