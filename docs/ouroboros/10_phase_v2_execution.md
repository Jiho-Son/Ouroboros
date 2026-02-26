<!--
Doc-ID: DOC-PHASE-V2-001
Version: 1.0.0
Status: active
Owner: strategy
Updated: 2026-02-26
-->

# v2 실행 지시서 (설계 -> 코드)

참조 요구사항: `REQ-V2-001` `REQ-V2-002` `REQ-V2-003` `REQ-V2-004` `REQ-V2-005` `REQ-V2-006` `REQ-V2-007` `REQ-V2-008` `REQ-OPS-001` `REQ-OPS-002` `REQ-OPS-003`

## 단계 1: 도메인 모델 확정

- `TASK-V2-001`: 상태머신 enum/전이 이벤트/전이 사유 스키마 설계
- `TASK-V2-002`: `position_state` 스냅샷 구조(현재상태, peak, stops, last_reason) 정의
- `TASK-V2-003`: 청산 판단 입력 DTO(가격, ATR, pred_prob, liquidity_signal) 정의

완료 기준:
- 상태와 전이 사유가 로그/DB에서 재현 가능
- `REQ-V2-001`~`003`을 코드 타입 수준에서 강제

## 단계 2: 청산 엔진 구현

- `TASK-V2-004`: 우선순위 기반 전이 함수 구현(`evaluate_exit_first` -> `promote_state`)
- `TASK-V2-005`: Hard Stop/BE Lock/ATR Trailing 결합 로직 구현
- `TASK-V2-006`: 모델 확률 신호를 보조 트리거로 결합(단독 청산 금지)

완료 기준:
- 갭 상황에서 다중 조건 동시 충족 시 최상위 상태로 단번 전이
- `REQ-V2-004` 준수

## 단계 3: 라벨링/학습 데이터 파이프라인

- `TASK-V2-007`: Triple Barrier 라벨러 구현(장벽 선터치 우선)
- `TASK-V2-008`: 피처 구간/라벨 구간 분리 검증 유틸 구현
- `TASK-V2-009`: 라벨 생성 로그(진입시각, 터치장벽, 만기장벽) 기록

완료 기준:
- look-ahead 차단 증빙 로그 확보
- `REQ-V2-005` 충족

## 단계 4: 검증 프레임워크

- `TASK-V2-010`: Walk-forward split + Purge/Embargo 분할기 구현
- `TASK-V2-011`: 베이스라인(`B0`,`B1`,`M1`) 비교 리포트 포맷 구현
- `TASK-V2-012`: 체결 비용/슬리피지/실패 반영 백테스트 옵션 강제

완료 기준:
- `REQ-V2-006`, `REQ-V2-007` 충족

## 단계 5: Kill Switch 통합

- `TASK-V2-013`: Kill Switch 순차 실행 오케스트레이터 구현 (`src/core/risk_manager.py` 수정 금지)
- `TASK-V2-014`: 주문 차단 플래그/미체결 취소/재조회 재시도 로직 구현
- `TASK-V2-015`: 스냅샷/알림/복구 진입 절차 구현

완료 기준:
- `REQ-V2-008` 순서 일치

라우팅:
- 코드 지시 상세: [30_code_level_work_orders.md](./30_code_level_work_orders.md)
- 테스트 상세: [40_acceptance_and_test_plan.md](./40_acceptance_and_test_plan.md)
