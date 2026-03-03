<!--
Doc-ID: DOC-REQ-001
Version: 1.0.12
Status: active
Owner: strategy
Updated: 2026-03-02
-->

# 요구사항 원장 (Single Source of Truth)

이 문서의 ID가 계획/구현/테스트 전 문서에서 참조되는 유일한 요구사항 집합이다.

## v2 핵심 요구사항

- `REQ-V2-001`: 상태는 `HOLDING`, `BE_LOCK`, `ARMED`, `EXITED` 4단계여야 한다.
- `REQ-V2-002`: 상태 전이는 매 틱/바 평가 시 최상위 상태로 즉시 승격되어야 한다.
- `REQ-V2-003`: `EXITED` 조건은 모든 상태보다 우선 평가되어야 한다.
- `REQ-V2-004`: 청산 로직은 Hard Stop, BE Lock, ATR Trailing, 모델 확률 보조 트리거를 포함해야 한다.
- `REQ-V2-005`: 라벨링은 Triple Barrier(Upper/Lower/Time) 방식이어야 한다.
- `REQ-V2-006`: 검증은 Walk-forward + Purge/Embargo를 강제한다.
- `REQ-V2-007`: 백테스트는 비용/슬리피지/체결실패를 반영하지 않으면 채택 불가다.
- `REQ-V2-008`: Kill Switch는 신규주문차단 -> 미체결취소 -> 재조회(실패 시 최대 3회, 1s/2s backoff 재시도, 성공 시 즉시 중단) -> 리스크축소 -> 스냅샷 순서다.

## v3 핵심 요구사항

- `REQ-V3-001`: 모든 신호/주문/로그는 `session_id`를 포함해야 한다.
- `REQ-V3-002`: 세션 전환 시 리스크 파라미터 재로딩이 수행되어야 한다.
- `REQ-V3-003`: 브로커 블랙아웃 시간대에는 신규 주문이 금지되어야 한다.
- `REQ-V3-004`: 블랙아웃 중 신호는 bounded Queue에 적재되며, 포화 시 oldest-drop 정책으로 최신 intent를 보존하고 복구 후 유효성 재검증을 거친다.
- `REQ-V3-005`: 저유동 세션(`NXT_AFTER`, `US_PRE`, `US_DAY`, `US_AFTER`)은 시장가 주문 금지다.
- `REQ-V3-006`: 백테스트 체결가는 불리한 방향 체결 가정을 기본으로 한다.
- `REQ-V3-007`: US 운용은 환율 손익 분리 추적과 통화 버퍼 정책을 포함해야 한다.
- `REQ-V3-008`: 마감/오버나잇 규칙은 Kill Switch와 충돌 없이 연동되어야 한다.

## 공통 운영 요구사항

- `REQ-OPS-001`: 타임존은 모든 시간 필드에 명시(KST/UTC)되어야 한다.
- `REQ-OPS-002`: 문서의 수치 정책은 원장에서만 변경한다.
- `REQ-OPS-003`: 구현 태스크는 반드시 테스트 태스크를 동반한다.
- `REQ-OPS-004`: 원본 계획 문서(`v2`, `v3`)는 `docs/ouroboros/source/` 경로를 단일 기준으로 사용한다.

## 변경 이력

- 2026-03-02: `v1.0.12` 문서 검증 게이트 강화(#390) 반영에 따라 정책 문서 동기화 체크를 수행했다. (`REQ-OPS-002`)
