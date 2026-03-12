<!--
Doc-ID: DOC-TEST-001
Version: 1.0.0
Status: active
Owner: strategy
Updated: 2026-02-26
-->

# 수용 기준 및 테스트 계획

## 수용 기준

- `TEST-ACC-000` (`REQ-V2-001`): 상태 enum은 4개(`HOLDING`,`BE_LOCK`,`ARMED`,`EXITED`)만 허용한다.
- `TEST-ACC-001` (`REQ-V2-002`): 상태 전이는 순차 if-else가 아닌 우선순위 승격으로 동작한다.
- `TEST-ACC-010` (`REQ-V2-003`): `EXITED` 조건은 어떤 상태보다 먼저 평가된다.
- `TEST-ACC-011` (`REQ-V2-004`): 청산 판단은 Hard Stop/BE Lock/ATR/모델보조 4요소를 모두 포함한다.
- `TEST-ACC-012` (`REQ-V2-005`): Triple Barrier 라벨은 first-touch 규칙으로 결정된다.
- `TEST-ACC-013` (`REQ-V2-006`): 학습/검증 분할은 Walk-forward + Purge/Embargo를 적용한다.
- `TEST-ACC-014` (`REQ-V2-007`): 비용/슬리피지/체결실패 옵션 비활성 시 백테스트 실행을 거부한다.
- `TEST-ACC-002` (`REQ-V2-008`): Kill Switch 실행 순서가 고정 순서를 위반하지 않는다.
- `TEST-ACC-015` (`REQ-V3-001`): 모든 주문/로그 레코드에 `session_id`가 저장된다.
- `TEST-ACC-016` (`REQ-V3-002`): 세션 전환 이벤트 시 리스크 파라미터가 재로딩된다.
- `TEST-ACC-003` (`REQ-V3-003`): 블랙아웃 중 신규 주문 API 호출이 발생하지 않는다.
- `TEST-ACC-017` (`REQ-V3-004`): 블랙아웃 큐는 복구 후 재검증을 통과한 주문만 실행한다.
- `TEST-ACC-004` (`REQ-V3-005`): 저유동 세션 시장가 주문은 항상 거부된다.
- `TEST-ACC-005` (`REQ-V3-006`): 백테스트 체결가가 단순 종가 체결보다 보수적 손익을 낸다.
- `TEST-ACC-006` (`REQ-V3-007`): 전략 손익과 환율 손익이 별도 집계된다.
- `TEST-ACC-018` (`REQ-V3-008`): 오버나잇 예외 상태에서도 Kill Switch 우선순위가 유지된다.
- `TEST-ACC-007` (`REQ-OPS-001`): 시간 관련 필드는 타임존(KST/UTC)이 누락되면 검증 실패한다.
- `TEST-ACC-008` (`REQ-OPS-002`): 정책 수치 변경이 원장 미반영이면 검증 실패한다.
- `TEST-ACC-009` (`REQ-OPS-003`): `REQ-*`가 `TASK-*`/`TEST-*` 매핑 없이 존재하면 검증 실패한다.
- `TEST-ACC-019` (`REQ-OPS-004`): v2/v3 원본 계획 문서 링크는 `docs/ouroboros/source/` 경로 기준으로만 통과한다.
- `TEST-ACC-020` (`REQ-OPS-005`): canonical restart automation은 non-`main` checkout 을 거부하고, merge SHA dedupe를 유지하며, `push` to `main` workflow 에서 host-side restart path를 호출한다.

## 테스트 계층

1. 단위 테스트
- 상태 전이, 주문타입 검증, 큐 복구 로직, 체결가 모델

2. 통합 테스트
- 세션 전환 -> 주문 정책 -> 리스크 엔진 연동
- 블랙아웃 시작/해제 이벤트 연동

3. 회귀 테스트
- 기존 `tests/` 스위트 전량 실행
- 신규 기능 플래그 ON/OFF 비교

4. 구동/모니터링 검증 (필수)
- 개발 완료 후 시스템을 실제 구동해 핵심 경로를 관찰
- 필수 관찰 항목: 주문 차단 정책, Kill Switch 동작, 경보/예외 로그, 세션 전환 로그
- Runtime Verifier 코멘트로 증적(실행 명령/요약 로그) 첨부

## 실행 명령

```bash
pytest -q
python3 scripts/validate_ouroboros_docs.py
```

## 실패 처리 규칙

- 문서 검증 실패 시 구현 PR 병합 금지
- `REQ-*` 변경 후 테스트 매핑 누락 시 병합 금지
- 회귀 실패 시 원인 모듈 분리 후 재검증
- 구동/모니터링 증적 누락 시 검증 승인 금지
