# Open Issue Triage Design

**Issue:** #438

## Goal

현재 오픈 이슈들의 실제 해결 상태를 코드, 테스트, 브랜치, 런타임 증거 기준으로 재평가하고, 닫을 것은 닫고 남길 것은 근거와 함께 유지한다.

## Evidence Policy

- 구조적 버그/회귀/마이그레이션 이슈는 코드 확인 + 회귀 테스트를 close 근거로 사용한다.
- 정책 이슈는 런타임 가드 + 회귀 테스트 + 문서 반영을 close 근거로 사용한다.
- 런타임 민감 이슈는 테스트만으로 close하지 않는다. 실동작 로그, DB 상태, API 호출 흔적, 운영 관측 메모 중 하나 이상이 있어야 한다.
- 런타임 증거가 없으면 코드상 해결 여부를 코멘트로 남기고 open 유지한다.

## Target Issues

- Close candidates from code/test evidence: #426, #428, #435, #436
- Runtime evidence required before close: #318, #325, #429

## Execution Plan

1. 각 이슈에 대해 현재 코드, 테스트, 브랜치, 관련 계획 문서를 대조한다.
2. 미해결 이슈는 TDD로 수정하고 회귀 테스트를 추가한다.
3. fresh verification 결과를 수집한다.
4. 각 이슈에 코멘트를 남기고 close/open 상태를 정리한다.
