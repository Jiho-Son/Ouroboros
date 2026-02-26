<!--
Doc-ID: DOC-VAL-001
Version: 1.0.0
Status: active
Owner: strategy
Updated: 2026-02-26
-->

# 문서 검증 시스템

본 문서는 문서 간 허위 내용, 수치 충돌, 구현 불가능 지시를 사전에 제거하기 위한 검증 규칙이다.

## 검증 목표

- 단일 진실원장 기준으로 모든 지시서의 수치/규칙 정합성 보장
- 설계 문장과 코드 작업 지시 간 추적성 보장
- 테스트 미정의 상태에서 구현 착수 금지

## 불일치 유형 정의

- `RULE-DOC-001`: 정의되지 않은 요구사항 ID 사용
- `RULE-DOC-002`: 동일 요구사항 ID에 상충되는 값(예: 슬리피지 수치) 기술
- `RULE-DOC-003`: 시간대 미표기 또는 KST/UTC 혼용 지시
- `RULE-DOC-004`: 주문 정책과 리스크 정책 충돌(예: 저유동 세션 시장가 허용)
- `RULE-DOC-005`: 구현 태스크에 테스트 ID 미연결
- `RULE-DOC-006`: 문서 라우팅 링크 깨짐

## 검증 파이프라인

1. 정적 검사 (자동)
- 대상: `docs/ouroboros/*.md`
- 검사: 메타데이터, 링크 유효성, ID 정의/참조 일치, REQ-추적성 매핑
- 도구: `scripts/validate_ouroboros_docs.py`

2. 추적성 검사 (자동 + 수동)
- 자동: `REQ-*`가 최소 1개 `TASK-*`와 1개 `TEST-*`에 연결되었는지 확인
- 수동: 정책 충돌 후보를 PR 체크리스트로 검토

3. 도메인 무결성 검사 (수동)
- KIS 점검시간 회피, 주문 유형 강제, Kill Switch 순서, 환율 정책이 동시에 존재하는지 점검
- 백테스트 체결가가 보수 가정인지 점검

## 변경 통제 규칙

- `REQ-*` 추가/수정 시 반드시 `01_requirements_registry.md` 먼저 변경
- `TASK-*` 수정 시 반드시 `40_acceptance_and_test_plan.md`의 대응 테스트를 동시 수정
- 충돌 발생 시 우선순위: `requirements_registry > phase execution > code work order`

적용 룰셋:
- `RULE-DOC-001` `RULE-DOC-002` `RULE-DOC-003` `RULE-DOC-004` `RULE-DOC-005` `RULE-DOC-006`

## PR 게이트

- `python3 scripts/validate_ouroboros_docs.py` 성공
- 신규/변경 `REQ-*`가 테스트 기준(`TEST-*`)과 연결됨
- 원본 계획(v2/v3)과 모순 없음
