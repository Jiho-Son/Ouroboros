<!--
Doc-ID: DOC-CODE-001
Version: 1.0.0
Status: active
Owner: strategy
Updated: 2026-03-15
-->

# 코드 레벨 작업 지시서

본 문서는 파일 단위 구현 지시서다. 모든 작업은 요구사항 ID와 테스트 ID를 포함해야 한다.

제약:
- `src/core/risk_manager.py`는 READ-ONLY로 간주하고 수정하지 않는다.
- Kill Switch는 별도 모듈(예: `src/core/kill_switch.py`)로 추가하고 상위 실행 루프에서 연동한다.

## 구현 단위 A: 상태기계/청산

- `TASK-CODE-001` (`REQ-V2-001`,`REQ-V2-002`,`REQ-V2-003`,`TEST-CODE-001`,`TEST-CODE-002`): `src/strategy/`에 상태기계 모듈 추가
- `TASK-CODE-002` (`REQ-V2-004`,`TEST-ACC-011`): ATR/BE/Hard Stop 결합 청산 함수 추가
- `TASK-CODE-003` (`REQ-V2-008`,`TEST-ACC-002`): Kill Switch 오케스트레이터를 `src/core/kill_switch.py`에 추가
- `TEST-CODE-001`: 갭 점프 시 최고상태 승격 테스트
- `TEST-CODE-002`: EXIT 우선순위 테스트

## 구현 단위 B: 라벨링/검증

- `TASK-CODE-004` (`REQ-V2-005`,`TEST-CODE-003`,`TEST-ACC-012`): Triple Barrier 라벨러 모듈 추가(`src/analysis/` 또는 `src/strategy/`)
- `TASK-CODE-005` (`REQ-V2-006`,`TEST-CODE-004`,`TEST-ACC-013`): Walk-forward + Purge/Embargo 분할 유틸 추가
- `TASK-CODE-006` (`REQ-V2-007`,`TEST-ACC-014`): 백테스트 실행기에서 비용/슬리피지 옵션 필수화
- `TEST-CODE-003`: 라벨 선터치 우선 테스트
- `TEST-CODE-004`: 누수 차단 테스트

## 구현 단위 C: 세션/주문 정책

- `TASK-CODE-007` (`REQ-V3-001`,`REQ-V3-002`,`TEST-ACC-015`,`TEST-ACC-016`): 세션 분류/전환 훅을 `src/markets/schedule.py` 연동
- `TASK-CODE-008` (`REQ-V3-003`,`REQ-V3-004`,`TEST-CODE-005`,`TEST-ACC-017`): 블랙아웃 큐 처리기를 `src/broker/`에 추가
- `TASK-CODE-009` (`REQ-V3-005`,`TEST-CODE-006`,`TEST-ACC-004`): 세션별 주문 타입 검증기 추가
- `TEST-CODE-005`: 블랙아웃 신규주문 차단 테스트
- `TEST-CODE-006`: 저유동 세션 시장가 거부 테스트

## 구현 단위 D: 체결/환율/오버나잇

- `TASK-CODE-010` (`REQ-V3-006`,`TEST-CODE-007`,`TEST-ACC-005`): 불리한 체결가 모델을 백테스트 체결기로 구현
- `TASK-CODE-011` (`REQ-V3-007`,`TEST-CODE-008`,`TEST-ACC-006`): FX PnL 분리 회계 테이블/컬럼 추가
- `TASK-CODE-012` (`REQ-V3-008`,`TEST-ACC-018`): 오버나잇 예외와 Kill Switch 충돌 해소 로직 구현
- `TEST-CODE-007`: 불리한 체결가 모델 테스트
- `TEST-CODE-008`: FX 버퍼 위반 시 신규진입 제한 테스트

## 구현 단위 E: 운영/문서 거버넌스

- `TASK-OPS-001` (`REQ-OPS-001`,`TEST-ACC-007`): 시간 필드/로그 스키마의 타임존(KST/UTC) 표기 강제 규칙 구현
- `TASK-OPS-002` (`REQ-OPS-002`,`TEST-ACC-008`): 정책 수치 변경 시 `01_requirements_registry.md` 선수정 CI 체크 추가
- `TASK-OPS-003` (`REQ-OPS-003`,`TEST-ACC-009`): `TASK-*` 없는 `REQ-*` 또는 `TEST-*` 없는 `REQ-*`를 차단하는 문서 검증 게이트 유지
- `TASK-OPS-004` (`REQ-OPS-004`,`TEST-ACC-019`): v2/v3 원본 계획 문서 위치를 `docs/ouroboros/source/`로 표준화하고 링크 일관성 검증
- `TASK-OPS-005` (`REQ-OPS-005`,`TEST-ACC-020`): `WORKFLOW.md` `hooks.before_remove` 에 repo-owned canonical restart hook를 연결하고, merged worktree 삭제 시 canonical `main` checkout만 pull/restart 하도록 구현

## 커밋 규칙

- 커밋 메시지에 `TASK-*` 포함
- PR 본문에 `REQ-*`, `TEST-*` 매핑 표 포함
- 변경 파일마다 최소 1개 테스트 연결
