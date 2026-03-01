<!--
Doc-ID: DOC-ROOT-001
Version: 1.0.1
Status: active
Owner: strategy
Updated: 2026-03-01
-->

# The Ouroboros 실행 문서 허브

이 폴더는 `source/ouroboros_plan_v2.txt`, `source/ouroboros_plan_v3.txt`를 구현 가능한 작업 지시서 수준으로 분해한 문서 허브다.

## 읽기 순서 (Routing)

1. 검증 체계부터 확정: [00_validation_system.md](./00_validation_system.md)
2. 단일 진실원장(요구사항): [01_requirements_registry.md](./01_requirements_registry.md)
3. v2 실행 지시서: [10_phase_v2_execution.md](./10_phase_v2_execution.md)
4. v3 실행 지시서: [20_phase_v3_execution.md](./20_phase_v3_execution.md)
5. 코드 레벨 작업 지시: [30_code_level_work_orders.md](./30_code_level_work_orders.md)
6. 수용 기준/테스트 계획: [40_acceptance_and_test_plan.md](./40_acceptance_and_test_plan.md)
7. PM 시나리오/이슈 분류: [50_scenario_matrix_and_issue_taxonomy.md](./50_scenario_matrix_and_issue_taxonomy.md)
8. TPM 제어 프로토콜/수용 매트릭스: [50_tpm_control_protocol.md](./50_tpm_control_protocol.md)
9. 저장소 강제 설정 체크리스트: [60_repo_enforcement_checklist.md](./60_repo_enforcement_checklist.md)
10. 메인 에이전트 아이디에이션 백로그: [70_main_agent_ideation.md](./70_main_agent_ideation.md)
11. v2/v3 구현 감사 및 수익률 분석: [80_implementation_audit.md](./80_implementation_audit.md)
12. 손실 복구 실행 계획: [85_loss_recovery_action_plan.md](./85_loss_recovery_action_plan.md)

## 운영 규칙

- 계획 변경은 반드시 `01_requirements_registry.md`의 ID 정의부터 수정한다.
- 구현 문서는 원장 ID만 참조하고 자체 숫자/정책을 새로 만들지 않는다.
- 문서 품질 룰셋(`RULE-DOC-001` `RULE-DOC-002` `RULE-DOC-003` `RULE-DOC-004` `RULE-DOC-005` `RULE-DOC-006`)은 [00_validation_system.md](./00_validation_system.md)를 기준으로 적용한다.
- 문서 병합 전 아래 검증을 통과해야 한다.

```bash
python3 scripts/validate_ouroboros_docs.py
```

## 원본 계획 문서

- [v2](./source/ouroboros_plan_v2.txt)
- [v3](./source/ouroboros_plan_v3.txt)
