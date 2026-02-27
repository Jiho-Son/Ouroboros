<!--
Doc-ID: DOC-OPS-002
Version: 1.0.0
Status: active
Owner: tpm
Updated: 2026-02-27
-->

# 저장소 강제 설정 체크리스트

목표: "엄격 검증 운영"을 문서가 아니라 저장소 설정으로 강제한다.

## 1) main 브랜치 보호 (필수)

적용 항목:
- direct push 금지
- force push 금지
- branch 삭제 금지
- merge는 PR 경로만 허용

검증:
- `main`에 대해 직접 `git push origin main` 시 거부되는지 확인

## 2) 필수 상태 체크 (필수)

필수 CI 항목:
- `validate_ouroboros_docs` (명령: `python3 scripts/validate_ouroboros_docs.py`)
- `test` (명령: `pytest -q`)

설정 기준:
- 위 2개 체크가 `success` 아니면 머지 금지
- 체크 스킵/중립 상태 허용 금지

## 3) 필수 리뷰어 규칙 (권장 -> 필수)

역할 기반 승인:
- Verifier 1명 승인 필수
- TPM 또는 PM 1명 승인 필수
- Runtime Verifier 관련 변경(PR 본문에 runtime 영향 있음) 시 Runtime Verifier 승인 필수

설정 기준:
- 최소 승인 수: 2
- 작성자 self-approval 불가
- 새 커밋 푸시 시 기존 승인 재검토 요구

## 4) 워크플로우 게이트

병합 전 체크리스트:
- 이슈 연결(`Closes #N`) 존재
- PR 본문에 `REQ-*`, `TASK-*`, `TEST-*` 매핑 표 존재
- Main -> Verifier Directive Contract(범위/방법/합격/실패/미관측/증적 형식) 기재
- process-change-first 대상이면 process 티켓 PR이 선머지됨
- `src/core/risk_manager.py` 변경 없음
- 주요 의사결정 체크포인트(DCP-01~04) 중 해당 단계 Main Agent 확인 기록 존재
- 주요 의사결정(리뷰 지적/수정 합의/검증 승인)에 대한 에이전트 PR 코멘트 존재
- 티켓 PR의 base가 `main`이 아닌 program feature branch인지 확인

자동 점검:
- 문서 검증 스크립트 통과
- 테스트 통과
- `python3 scripts/session_handover_check.py --strict` 통과
- 개발 완료 시 시스템 구동/모니터링 증적 코멘트 존재
- 이슈/PR 조작 전에 `docs/commands.md` 및 `docs/workflow.md` 트러블슈팅 확인 코멘트 존재
- `gh` CLI 미사용, `tea` 사용 증적 존재
- Verifier `Coverage Matrix` 첨부(PASS/FAIL/NOT_OBSERVED)
- `NOT_OBSERVED` 항목 0 확인(0이 아니면 머지 금지)
- 티켓 단계 기록(`Implemented` -> `Integrated` -> `Observed` -> `Accepted`) 존재
- 정적 Verifier 승인 + Runtime Verifier 승인 2개 확인

## 5) 감사 추적

필수 보존 증적:
- CI 실행 로그 링크
- 검증 실패/복구 기록
- 머지 승인 코멘트(Verifier/TPM)

분기별 점검:
- 브랜치 보호 규칙 drift 여부
- 필수 CI 이름 변경/누락 여부

## 6) 적용 순서 (운영 절차)

1. 브랜치 보호 활성화
2. 필수 CI 체크 연결
3. 리뷰어 규칙 적용
4. 샘플 PR로 거부 시나리오 테스트
5. 정상 머지 시나리오 테스트

## 7) 실패 시 조치

- 브랜치 보호 미적용 발견 시: 즉시 릴리즈 중지
- 필수 CI 우회 발견 시: 관리자 권한 점검 및 감사 이슈 발행
- 리뷰 규칙 무효화 발견 시: 규칙 복구 후 재머지 정책 시행
- Runtime 이상 이슈 미해결 상태에서 클로즈 시도 발견 시: 즉시 이슈 재오픈 + 릴리즈 중지

## 8) 재계획(Dev Replan) 운영 규칙

- Dev가 `REPLAN-REQUEST` 발행 시 TPM 심사 없이는 스코프/일정 변경 금지
- `REPLAN-REQUEST`는 Main Agent 승인 전 \"제안\" 상태로 유지
- 승인된 재계획은 `REQ/TASK/TEST` 문서를 동시 갱신해야 유효

## 9) 서버 반영 규칙

- 티켓 PR(`feature/issue-* -> feature/{stream}`)은 검증 승인 후 머지 가능하다.
- 최종 통합 PR(`feature/{stream} -> main`)은 사용자 명시 승인 전 `tea pulls merge` 실행 금지.
- Main 병합 시 승인 근거 코멘트 필수.

## 10) 최종 main 병합 조건

- 모든 티켓이 program feature branch로 병합 완료
- Runtime Verifier의 구동/모니터링 검증 완료
- 사용자 최종 승인 코멘트 확인 후에만 `feature -> main` PR 머지 허용
