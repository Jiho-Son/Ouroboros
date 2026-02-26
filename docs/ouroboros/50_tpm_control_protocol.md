<!--
Doc-ID: DOC-TPM-001
Version: 1.0.0
Status: active
Owner: tpm
Updated: 2026-02-26
-->

# TPM Control Protocol (Main <-> PM <-> TPM <-> Dev <-> Verifier <-> Runtime Verifier)

목적:
- PM 시나리오가 구현 가능한 단위로 분해되고, 개발/검증이 동일 ID 체계(`REQ-*`, `TASK-*`, `TEST-*`)로 닫히도록 강제한다.
- 각 단계는 Entry/Exit gate를 통과해야 다음 단계로 이동 가능하다.
- 주요 의사결정 포인트마다 Main Agent의 승인/의견 확인을 강제한다.

## Team Roles

- Main Agent: 최종 취합/우선순위/승인 게이트 오너
- PM Agent: 시나리오/요구사항/티켓 관리
- TPM Agent: PM-Dev-검증 간 구현 가능성/달성률 통제, 티켓 등록 및 구현 우선순위 지정 오너
- Dev Agent: 구현 수행, 블로커 발생 시 재계획 요청
- Verifier Agent: 문서/코드/테스트 산출물 검증
- Runtime Verifier Agent: 실제 동작 모니터링, 이상 징후 이슈 발행, 수정 후 이슈 클로즈 판정

Main Agent 아이디에이션 책임:
- 진행 중 신규 구현 아이디어를 별도 문서에 누적 기록한다.
- 기록 위치: [70_main_agent_ideation.md](./70_main_agent_ideation.md)
- 각 항목은 `IDEA-*` 식별자, 배경, 기대효과, 리스크, 후속 티켓 후보를 포함해야 한다.

## Main Decision Checkpoints (Mandatory)

- DCP-01 범위 확정: Phase 0 종료 전 Main Agent 승인 필수
- DCP-02 요구사항 확정: Phase 1 종료 전 Main Agent 승인 필수
- DCP-03 구현 착수: Phase 2 종료 전 Main Agent 승인 필수
- DCP-04 배포 승인: Phase 4 종료 후 Main Agent 최종 승인 필수

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
- Main Agent 승인(DCP-01)
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
- Main Agent 승인(DCP-02)
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
- Main Agent 승인(DCP-03)
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
- Runtime Verifier가 스테이징/실운영 모니터링 계획 승인
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
- Runtime Verifier가 운영 모니터링 이슈 상태(신규/진행/해결)를 리포트
- Main Agent 최종 승인(DCP-04)
- 산출물: 릴리즈 노트 + 후속 액션 목록

## Replan Protocol (Dev -> TPM)

- 트리거:
  - 구현 불가능(기술적 제약/외부 API 제약)
  - 예상 대비 개발 리소스 과다(공수/인력/의존성 급증)
- 절차:
  1) Dev Agent가 `REPLAN-REQUEST` 발행(영향 REQ/TASK, 원인, 대안, 추가 공수 포함)
  2) TPM Agent가 1차 심사(범위 축소/단계 분할/요구사항 조정안)
  3) Verifier/PM 의견 수렴 후 Main Agent 승인으로 재계획 확정
- 규칙:
  - Main Agent 승인 없는 재계획은 실행 금지
  - 재계획 반영 시 문서(`REQ/TASK/TEST`) 동시 갱신 필수

TPM 티켓 운영 규칙:
- TPM은 합의된 변경을 이슈로 등록하고 우선순위(`P0/P1/P2`)를 지정한다.
- PR 본문에는 TPM이 지정한 우선순위와 범위가 그대로 반영되어야 한다.
- 우선순위 변경은 TPM 제안 + Main Agent 승인으로만 가능하다.
- PM/TPM/Dev/Reviewer/Verifier/Runtime Verifier는 주요 의사결정 시점마다 PR 코멘트를 남겨 결정 근거를 추적 가능 상태로 유지한다.

브랜치 운영 규칙:
- TPM은 각 티켓에 대해 `ticket temp branch -> program feature branch` PR 경로를 지정한다.
- 티켓 머지 대상은 항상 program feature branch이며, `main`은 최종 통합 단계에서만 사용한다.

## Runtime Verification Protocol

- Runtime Verifier는 테스트 통과 이후 실제 동작(스테이징/실운영)을 모니터링한다.
- 이상 동작/현상 발견 시 즉시 이슈 발행:
  - 제목 규칙: `[RUNTIME-VERIFY][SCN-*] ...`
  - 본문 필수: 재현조건, 관측 로그, 영향 범위, 임시 완화책, 관련 `REQ/TASK/TEST`
- 이슈 클로즈 규칙:
  - Dev 수정 완료 + Verifier 재검증 통과 + Runtime Verifier 재관측 정상
  - 최종 클로즈 승인자는 Main Agent
- 개발 완료 필수 절차:
  - 시스템 실제 구동(스테이징/로컬 실운영 모드) 실행
  - 모니터링 체크리스트(핵심 경보/주문 경로/예외 로그) 수행
  - 결과를 티켓/PR 코멘트에 증적으로 첨부하지 않으면 완료로 간주하지 않음

## Server Reflection Rule

- `ticket temp branch -> program feature branch` 머지는 검증 승인 후 자동/수동 진행 가능하다.
- `program feature branch -> main` 머지는 사용자 명시 승인 시에만 허용한다.
- Main 병합 시 Main Agent가 승인 근거를 PR 코멘트에 기록한다.

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
