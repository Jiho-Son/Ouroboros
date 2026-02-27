## Linked Issue

- Closes #N

## Scope

- REQ: `REQ-...`
- TASK: `TASK-...`
- TEST: `TEST-...`

## Ticket Stage

- Current stage: `Implemented` / `Integrated` / `Observed` / `Accepted`
- Previous stage evidence link:

## Main -> Verifier Directive Contract

- Scope: 대상 요구사항/코드/로그 경로
- Method: 실행 커맨드 + 관측 포인트
- PASS criteria:
- FAIL criteria:
- NOT_OBSERVED criteria:
- Evidence format: PR 코멘트 `Coverage Matrix`

## Verifier Coverage Matrix (Required)

| Item | Evidence | Status (PASS/FAIL/NOT_OBSERVED) |
|---|---|---|
| REQ-... | 링크/로그 | PASS |

`NOT_OBSERVED`가 1개라도 있으면 승인/머지 금지.

## Gitea Preflight

- [ ] `docs/commands.md`와 `docs/workflow.md` 트러블슈팅 선확인
- [ ] `tea` 사용 (`gh` 미사용)

## Session Handover Gate

- [ ] `python3 scripts/session_handover_check.py --strict` 통과
- [ ] `workflow/session-handover.md` 최신 엔트리가 현재 브랜치/당일(UTC) 기준으로 갱신됨
- 최신 handover 엔트리 heading:

## Runtime Evidence

- 시스템 실제 구동 커맨드:
- 모니터링 로그 경로:
- 이상 징후/이슈 링크:

## Approval Gate

- [ ] Static Verifier approval comment linked
- [ ] Runtime Verifier approval comment linked
