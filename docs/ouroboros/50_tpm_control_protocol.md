<!--
Doc-ID: DOC-TPM-001
Version: 1.0.0
Status: active
Owner: tpm
Updated: 2026-02-26
-->

# TPM Control Protocol (PM <-> Dev <-> Verifier)

목적:
- PM 시나리오가 구현 가능한 단위로 분해되고, 개발/검증이 동일 ID 체계(`REQ-*`, `TASK-*`, `TEST-*`)로 닫히도록 강제한다.
- 각 단계는 Entry/Exit gate를 통과해야 다음 단계로 이동 가능하다.

## Phase Control Gates

### Phase 0: Scenario Intake and Scope Lock

Entry criteria:
- PM 시나리오가 사용자 가치, 실패 모드, 우선순위를 포함해 제출됨
- 영향 범위(모듈/세션/KR-US 시장)가 명시됨

Exit criteria:
- 시나리오가 `REQ-*` 후보에 1:1 또는 1:N 매핑됨
- 모호한 표현("개선", "최적화")은 측정 가능한 조건으로 치환됨
- 비범위 항목(out-of-scope) 명시

Control checks:
- PM/TPM 합의 완료
- 산출물: 시나리오 카드, 초기 매핑 메모

### Phase 1: Requirement Registry Gate

Entry criteria:
- Phase 0 산출물 승인
- 변경 대상 요구사항 문서 식별 완료

Exit criteria:
- [01_requirements_registry.md](./01_requirements_registry.md)에 `REQ-*` 정의/수정 반영
- 각 `REQ-*`가 최소 1개 `TASK-*`, 1개 `TEST-*`와 연결 가능 상태
- 시간/정책 수치는 원장 단일 소스로 확정(`REQ-OPS-001`,`REQ-OPS-002`)

Control checks:
- `python3 scripts/validate_ouroboros_docs.py` 통과
- 산출물: 업데이트된 요구사항 원장

### Phase 2: Design and Work-Order Gate

Entry criteria:
- 요구사항 원장 갱신 완료
- 영향 모듈 분석 완료(상태기계, 주문정책, 백테스트, 세션)

Exit criteria:
- [10_phase_v2_execution.md](./10_phase_v2_execution.md), [20_phase_v3_execution.md](./20_phase_v3_execution.md), [30_code_level_work_orders.md](./30_code_level_work_orders.md)에 작업 분해 완료
- 각 작업은 구현 위치/제약/완료 조건을 가짐
- 위험 작업(Kill Switch, blackout, session transition)은 별도 롤백 절차 포함

Control checks:
- TPM이 `REQ -> TASK` 누락 여부 검토
- 산출물: 승인된 Work Order 세트

### Phase 3: Implementation Gate

Entry criteria:
- 승인된 `TASK-*`가 브랜치 작업 단위로 분리됨
- 변경 범위별 테스트 계획이 PR 본문에 링크됨

Exit criteria:
- 코드 변경이 `TASK-*`에 대응되어 추적 가능
- 제약 준수(`src/core/risk_manager.py` 직접 수정 금지 등) 확인
- 신규 로직마다 최소 1개 테스트 추가 또는 기존 테스트 확장

Control checks:
- PR 템플릿 내 `REQ-*`/`TASK-*`/`TEST-*` 매핑 확인
- 산출물: 리뷰 가능한 PR

### Phase 4: Verification and Acceptance Gate

Entry criteria:
- 구현 PR ready 상태
- 테스트 케이스/픽스처 준비 완료

Exit criteria:
- [40_acceptance_and_test_plan.md](./40_acceptance_and_test_plan.md)의 해당 `TEST-ACC-*` 전부 통과
- 회귀 테스트 통과(`pytest -q`)
- 문서 검증 통과(`python3 scripts/validate_ouroboros_docs.py`)

Control checks:
- Verifier가 테스트 증적(로그/리포트/실행 커맨드) 첨부
- 산출물: 수용 승인 레코드

### Phase 5: Release and Post-Release Control

Entry criteria:
- Phase 4 승인
- 운영 체크리스트 준비(세션 전환, 블랙아웃, Kill Switch)

Exit criteria:
- 배포 후 초기 관찰 윈도우에서 치명 경보 없음
- 신규 시나리오/회귀 이슈는 다음 Cycle의 Phase 0 입력으로 환류
- 요구사항/테스트 문서 버전 동기화 완료

Control checks:
- PM/TPM/Dev 3자 종료 확인
- 산출물: 릴리즈 노트 + 후속 액션 목록

## Acceptance Matrix (PM Scenario -> Dev Tasks -> Verifier Checks)

| PM Scenario | Requirement Coverage | Dev Tasks (Primary) | Verifier Checks (Must Pass) |
|---|---|---|---|
| 갭 급락/급등에서 청산 우선 처리 필요 | `REQ-V2-001`,`REQ-V2-002`,`REQ-V2-003` | `TASK-V2-004`,`TASK-CODE-001` | `TEST-ACC-000`,`TEST-ACC-001`,`TEST-ACC-010`,`TEST-CODE-001`,`TEST-CODE-002` |
| 하드스탑 + BE락 + ATR + 모델보조를 한 엔진으로 통합 | `REQ-V2-004` | `TASK-V2-005`,`TASK-V2-006`,`TASK-CODE-002` | `TEST-ACC-011` |
| 라벨 누수 없는 학습데이터 생성 | `REQ-V2-005` | `TASK-V2-007`,`TASK-CODE-004` | `TEST-ACC-012`,`TEST-CODE-003` |
| 검증 프레임워크를 시계열 누수 방지 구조로 강제 | `REQ-V2-006` | `TASK-V2-010`,`TASK-CODE-005` | `TEST-ACC-013`,`TEST-CODE-004` |
| 과낙관 백테스트 방지(비용/슬리피지/실패 강제) | `REQ-V2-007` | `TASK-V2-012`,`TASK-CODE-006` | `TEST-ACC-014` |
| 장애 시 Kill Switch 실행 순서 고정 | `REQ-V2-008` | `TASK-V2-013`,`TASK-V2-014`,`TASK-V2-015`,`TASK-CODE-003` | `TEST-ACC-002`,`TEST-ACC-018` |
| 세션 전환 단위 리스크/로그 추적 일관화 | `REQ-V3-001`,`REQ-V3-002` | `TASK-V3-001`,`TASK-V3-002`,`TASK-V3-003`,`TASK-CODE-007` | `TEST-ACC-015`,`TEST-ACC-016` |
| 블랙아웃 중 주문 차단 + 복구 후 재검증 실행 | `REQ-V3-003`,`REQ-V3-004` | `TASK-V3-004`,`TASK-V3-005`,`TASK-V3-006`,`TASK-CODE-008` | `TEST-ACC-003`,`TEST-ACC-017`,`TEST-CODE-005` |
| 저유동 세션 시장가 주문 금지 | `REQ-V3-005` | `TASK-V3-007`,`TASK-V3-008`,`TASK-CODE-009` | `TEST-ACC-004`,`TEST-CODE-006` |
| 보수적 체결 모델을 백테스트 기본으로 설정 | `REQ-V3-006` | `TASK-V3-010`,`TASK-V3-011`,`TASK-V3-012`,`TASK-CODE-010` | `TEST-ACC-005`,`TEST-CODE-007` |
| 전략손익/환율손익 분리 + 통화 버퍼 통제 | `REQ-V3-007` | `TASK-V3-013`,`TASK-V3-014`,`TASK-CODE-011` | `TEST-ACC-006`,`TEST-CODE-008` |
| 오버나잇 규칙과 Kill Switch 충돌 방지 | `REQ-V3-008` | `TASK-V3-015`,`TASK-CODE-012` | `TEST-ACC-018` |
| 타임존/정책변경/추적성 문서 거버넌스 | `REQ-OPS-001`,`REQ-OPS-002`,`REQ-OPS-003` | `TASK-OPS-001`,`TASK-OPS-002`,`TASK-OPS-003` | `TEST-ACC-007`,`TEST-ACC-008`,`TEST-ACC-009` |

## 운영 규율 (TPM Enforcement Rules)

- 어떤 PM 시나리오도 `REQ-*` 없는 구현 착수 금지.
- 어떤 `REQ-*`도 `TASK-*`,`TEST-*` 없는 승인 금지.
- Verifier는 "코드 리뷰 통과"만으로 승인 불가, 반드시 `TEST-ACC-*` 증적 필요.
- 배포 승인권자는 Phase 4 체크리스트 미충족 시 릴리즈 보류 권한을 행사해야 한다.
