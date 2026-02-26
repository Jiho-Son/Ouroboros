<!--
Doc-ID: DOC-PM-001
Version: 1.0.0
Status: active
Owner: strategy
Updated: 2026-02-26
-->

# 실전 시나리오 매트릭스 + 이슈 분류 체계

목표: 운영에서 바로 사용할 수 있는 형태로 Happy Path / Failure Path / Ops Incident를 추적 가능한 ID 체계(`REQ-*`, `TASK-*`, `TEST-*`)에 매핑한다.

## 1) 시나리오 매트릭스

| Scenario ID | Type | Trigger | Expected System Behavior | Primary IDs (REQ/TASK/TEST) | Ticket Priority |
|---|---|---|---|---|---|
| `SCN-HAPPY-001` | Happy Path | KR 정규 세션에서 진입 신호 발생, 블랙아웃 아님 | 주문/로그에 `session_id` 저장 후 정책에 맞는 주문 전송 | `REQ-V3-001`, `TASK-V3-001`, `TASK-V3-003`, `TEST-ACC-015` | P1 |
| `SCN-HAPPY-002` | Happy Path | 보유 포지션에서 BE/ATR/Hard Stop 조건 순차 도달 | 상태가 즉시 상위 단계로 승격, `EXITED` 우선 평가 보장 | `REQ-V2-002`, `REQ-V2-003`, `TASK-V2-004`, `TEST-ACC-001`, `TEST-ACC-010` | P0 |
| `SCN-HAPPY-003` | Happy Path | 세션 전환(KR->US) 이벤트 발생 | 리스크 파라미터 자동 재로딩, 새 세션 정책으로 즉시 전환 | `REQ-V3-002`, `TASK-V3-002`, `TEST-ACC-016` | P0 |
| `SCN-HAPPY-004` | Happy Path | 백테스트 실행 요청 | 비용/슬리피지/체결실패 옵션 누락 시 실행 거부, 포함 시 실행 | `REQ-V2-007`, `TASK-V2-012`, `TEST-ACC-014` | P1 |
| `SCN-FAIL-001` | Failure Path | 블랙아웃 중 신규 주문 신호 발생 | 신규 주문 차단 + 주문 의도 큐 적재, API 직접 호출 금지 | `REQ-V3-003`, `REQ-V3-004`, `TASK-V3-005`, `TEST-ACC-003`, `TEST-ACC-017` | P0 |
| `SCN-FAIL-002` | Failure Path | 저유동 세션에 시장가 주문 요청 | 시장가 하드 거부, 지정가 대체 또는 주문 취소 | `REQ-V3-005`, `TASK-V3-007`, `TASK-V3-008`, `TEST-ACC-004` | P0 |
| `SCN-FAIL-003` | Failure Path | Kill Switch 트리거(손실/연결/리스크 한도) | 신규주문차단->미체결취소->재조회->리스크축소->스냅샷 순서 강제 | `REQ-V2-008`, `TASK-V2-013`, `TEST-ACC-002` | P0 |
| `SCN-FAIL-004` | Failure Path | FX 버퍼 부족 상태에서 US 진입 신호 | 전략 PnL/FX PnL 분리 집계 유지, 신규 진입 제한 | `REQ-V3-007`, `TASK-V3-013`, `TASK-V3-014`, `TEST-ACC-006` | P1 |
| `SCN-OPS-001` | Ops Incident | 브로커 점검/블랙아웃 종료 직후 | 잔고/미체결/체결 동기화 후 큐 재검증 통과 주문만 집행 | `REQ-V3-004`, `TASK-V3-006`, `TEST-ACC-017` | P0 |
| `SCN-OPS-002` | Ops Incident | 정책 수치가 코드에만 반영되고 원장 미수정 | 문서 검증에서 실패 처리, PR 병합 차단 | `REQ-OPS-002`, `TASK-OPS-002`, `TEST-ACC-008` | P0 |
| `SCN-OPS-003` | Ops Incident | 타임존 누락 로그/스케줄 데이터 유입 | KST/UTC 미표기 레코드 검증 실패 처리 | `REQ-OPS-001`, `TASK-OPS-001`, `TEST-ACC-007` | P1 |
| `SCN-OPS-004` | Ops Incident | 신규 REQ 추가 후 TASK/TEST 누락 | 추적성 게이트 실패, 구현 PR 병합 차단 | `REQ-OPS-003`, `TASK-OPS-003`, `TEST-ACC-009` | P0 |
| `SCN-OPS-005` | Ops Incident | 배포 후 런타임 이상 동작(주문오류/상태전이오류/정책위반) 탐지 | Runtime Verifier가 즉시 이슈 발행, Dev 수정 후 재관측으로 클로즈 판정 | `REQ-V2-008`, `REQ-V3-003`, `REQ-V3-005`, `TEST-ACC-002`, `TEST-ACC-003`, `TEST-ACC-004` | P0 |

## 2) 이슈 분류 체계 (Issue Taxonomy)

| Taxonomy | Definition | Typical Symptoms | Default Owner | Mapping Baseline |
|---|---|---|---|---|
| `EXEC-STATE` | 상태기계/청산 우선순위 위반 | EXIT 우선순위 깨짐, 상태 역행, 갭 대응 실패 | Strategy | `REQ-V2-001`~`REQ-V2-004`, `TASK-V2-004`~`TASK-V2-006`, `TEST-ACC-000`,`001`,`010`,`011` |
| `EXEC-POLICY` | 세션/주문 정책 위반 | 블랙아웃 주문 전송, 저유동 시장가 허용 | Broker/Execution | `REQ-V3-003`~`REQ-V3-005`, `TASK-V3-004`~`TASK-V3-009`, `TEST-ACC-003`,`004`,`017` |
| `BACKTEST-MODEL` | 백테스트 현실성/검증 무결성 위반 | 비용 옵션 off로 실행, 체결가 과낙관 | Research | `REQ-V2-006`,`REQ-V2-007`,`REQ-V3-006`, `TASK-V2-010`~`012`, `TASK-V3-010`~`012`, `TEST-ACC-013`,`014`,`005` |
| `RISK-EMERGENCY` | Kill Switch/리스크 비상 대응 실패 | 순서 위반, 차단 누락, 복구 절차 누락 | Risk | `REQ-V2-008`,`REQ-V3-008`, `TASK-V2-013`~`015`, `TASK-V3-015`, `TEST-ACC-002`,`018` |
| `FX-ACCOUNTING` | 환율/통화 버퍼 정책 위반 | 전략손익/환차손익 혼합 집계, 버퍼 미적용 | Risk + Data | `REQ-V3-007`, `TASK-V3-013`,`014`, `TEST-ACC-006` |
| `OPS-GOVERNANCE` | 문서/추적성/타임존 거버넌스 위반 | 원장 미수정, TEST 누락, 타임존 미표기 | PM + QA | `REQ-OPS-001`~`003`, `TASK-OPS-001`~`003`, `TEST-ACC-007`~`009` |
| `RUNTIME-VERIFY` | 실동작 모니터링 검증 | 배포 후 이상 현상, 간헐 오류, 테스트 미포착 회귀 | Runtime Verifier + TPM | 관련 `REQ/TASK/TEST`와 런타임 로그 증적 필수 |

## 3) 티켓 생성 규칙 (Implementable)

1. 모든 이슈는 `taxonomy + scenario_id`를 제목에 포함한다.  
   예: `[EXEC-POLICY][SCN-FAIL-001] blackout 주문 차단 누락`
2. 본문 필수 항목: 재현절차, 기대결과, 실제결과, 영향범위, 롤백/완화책.
3. 본문에 최소 1개 `REQ-*`, 1개 `TASK-*`, 1개 `TEST-*`를 명시한다.
4. 우선순위 기준:
- P0: 실주문 위험, Kill Switch, 블랙아웃/시장가 정책, 추적성 게이트 실패
- P1: 손익 왜곡 가능성(체결/FX/시간대), 운영 리스크 증가
- P2: 보고서/관측성 품질 이슈(거래 안전성 영향 없음)
5. Runtime Verifier가 발행한 `RUNTIME-VERIFY` 이슈는 Main Agent 확인 전 클로즈 금지.

## 4) 즉시 생성 권장 티켓 (초기 백로그)

- `TKT-P0-001`: `[EXEC-POLICY][SCN-FAIL-001]` 블랙아웃 차단 + 큐적재 + 복구 재검증 e2e 점검 (`REQ-V3-003`,`REQ-V3-004`)
- `TKT-P0-002`: `[RISK-EMERGENCY][SCN-FAIL-003]` Kill Switch 순서 강제 검증 자동화 (`REQ-V2-008`)
- `TKT-P0-003`: `[OPS-GOVERNANCE][SCN-OPS-004]` REQ/TASK/TEST 누락 시 PR 차단 게이트 상시 점검 (`REQ-OPS-003`)
- `TKT-P1-001`: `[FX-ACCOUNTING][SCN-FAIL-004]` FX 버퍼 위반 시 진입 제한 회귀 케이스 보강 (`REQ-V3-007`)
- `TKT-P1-002`: `[BACKTEST-MODEL][SCN-HAPPY-004]` 비용/슬리피지 미설정 백테스트 거부 UX 명확화 (`REQ-V2-007`)
- `TKT-P0-004`: `[RUNTIME-VERIFY][SCN-OPS-005]` 배포 후 런타임 이상 탐지/재현/클로즈 판정 절차 자동화

## 5) 운영 체크포인트

- 스프린트 계획 시 `P0` 시나리오 100% 테스트 통과를 출발 조건으로 둔다.
- 배포 승인 시 `SCN-FAIL-*`, `SCN-OPS-*` 관련 `TEST-ACC-*`를 우선 확인한다.
- 정책 변경 PR은 반드시 원장(`01_requirements_registry.md`) 선수정 후 진행한다.
