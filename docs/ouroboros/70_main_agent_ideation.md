<!--
Doc-ID: DOC-IDEA-001
Version: 1.0.0
Status: active
Owner: main-agent
Updated: 2026-02-26
-->

# 메인 에이전트 아이디에이션 백로그

목적:
- 구현 진행 중 떠오른 신규 구현 아이디어를 계획 반영 전 임시 저장한다.
- 본 문서는 사용자 검토 후 다음 계획 포함 여부를 결정하기 위한 검토 큐다.

운영 규칙:
- 각 아이디어는 `IDEA-*` 식별자를 사용한다.
- 필수 필드: 배경, 기대효과, 리스크, 후속 티켓 후보.
- 상태는 `proposed`, `under-review`, `accepted`, `rejected` 중 하나를 사용한다.

## 아이디어 목록

- `IDEA-001` (status: proposed)
  - 제목: Kill-Switch 전역 상태를 프로세스 단일 전역에서 시장/세션 단위 상태로 분리
  - 배경: 현재는 전역 block 플래그 기반이라 시장별 분리 제어가 제한될 수 있음
  - 기대효과: KR/US 병행 운용 시 한 시장 장애가 다른 시장 주문을 불필요하게 막는 리스크 축소
  - 리스크: 상태 동기화 복잡도 증가, 테스트 케이스 확장 필요
  - 후속 티켓 후보: `TKT-P1-KS-SCOPE-SPLIT`

- `IDEA-002` (status: proposed)
  - 제목: Exit Engine 입력 계약(ATR/peak/model_prob/liquidity) 표준 DTO를 데이터 파이프라인에 고정
  - 배경: 현재 ATR/모델확률 일부가 fallback 기반이라 운영 일관성이 약함
  - 기대효과: 백테스트-실거래 입력 동형성 강화, 회귀 분석 용이
  - 리스크: 기존 스캐너/시나리오 엔진 연동 작업량 증가
  - 후속 티켓 후보: `TKT-P1-EXIT-CONTRACT`

- `IDEA-003` (status: proposed)
  - 제목: Runtime Verifier 자동 이슈 생성기(로그 패턴 -> 이슈 템플릿 자동화)
  - 배경: 런타임 이상 리포트가 수동 작성 중심이라 누락 가능성 존재
  - 기대효과: 이상 탐지 후 이슈 등록 리드타임 단축, 증적 표준화
  - 리스크: 오탐 이슈 폭증 가능성, 필터링 룰 필요
  - 후속 티켓 후보: `TKT-P1-RUNTIME-AUTO-ISSUE`

- `IDEA-004` (status: proposed)
  - 제목: PR 코멘트 워크플로우 자동 점검(리뷰어->개발논의->검증승인 누락 차단)
  - 배경: 현재 절차는 강력하지만 수행 확인이 수동
  - 기대효과: 절차 누락 방지, 감사 추적 자동화
  - 리스크: CLI/API 연동 유지보수 비용
  - 후속 티켓 후보: `TKT-P0-WORKFLOW-GUARD`
