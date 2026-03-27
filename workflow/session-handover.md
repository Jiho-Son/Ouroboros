# Session Handover Log

목적: 세션 시작 시 인수인계 확인을 기록하고, 구현/검증 작업 시작 전에 공통 컨텍스트를 강제한다.

작성 규칙:
- 세션 시작마다 최신 엔트리를 맨 아래에 추가한다.
- `docs/workflow.md`, `docs/commands.md`, `docs/agent-constraints.md`를 먼저 확인한 뒤 기록한다.
- 각 엔트리는 현재 작업 브랜치 기준으로 작성한다.

템플릿:

```md
### YYYY-MM-DD | session=<id or short label>
- branch: <current-branch>
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #...
- next_ticket: #...
- process_gate_checked: process_ticket=#..., merged_to_feature_branch=yes|no|n/a
- risks_or_notes: ...
```

### 2026-02-27 | session=handover-gate-bootstrap
- branch: feature/v3-session-policy-stream
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #304, #305, #306
- next_ticket: #304
- risks_or_notes: 세션 시작 게이트를 문서/스크립트/CI로 강제 적용

### 2026-02-27 | session=codex-handover-start
- branch: feature/v3-session-policy-stream
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #306, #308, #309
- next_ticket: #304
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 미추적 로컬 파일 존재(문서/DB/lock)로 커밋 범위 분리 필요

### 2026-02-27 | session=codex-process-gate-hardening
- branch: feature/issue-304-runtime-staged-exit-semantics
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #304, #305
- next_ticket: #304
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: process-change-first 실행 게이트를 문서+스크립트로 강화

### 2026-02-27 | session=codex-handover-start-2
- branch: feature/issue-304-runtime-staged-exit-semantics
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #304, #305
- next_ticket: #304
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: handover 재시작 요청으로 세션 엔트리 추가, 미추적 산출물(AMS/NAS/NYS, DB, lock, xlsx) 커밋 분리 필요

### 2026-02-27 | session=codex-issue305-start
- branch: feature/v3-session-policy-stream
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #305
- next_ticket: #305
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: #305 구현을 위해 분석/백테스트 모듈 통합 경로 점검 시작

### 2026-02-27 | session=codex-issue305-ticket-branch
- branch: feature/issue-305-backtest-pipeline-integration
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #305
- next_ticket: #305
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 티켓 브랜치 분기 후 strict gate 재통과를 위한 엔트리 추가

### 2026-02-27 | session=codex-backtest-gate-automation
- branch: feature/v3-session-policy-stream
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #304, #305
- next_ticket: (create) backtest automation gate
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 백테스트 자동화 누락 재발 방지 위해 이슈/티켓 브랜치/PR 절차로 즉시 정규화

### 2026-02-27 | session=codex-issue314-ticket-branch
- branch: feature/issue-314-backtest-gate-automation
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #314
- next_ticket: #314
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 백테스트 자동 게이트 도입 티켓 브랜치 strict gate 통과용 엔트리

### 2026-02-28 | session=codex-issue316-forbidden-monitor
- branch: feature/issue-316-weekend-forbidden-monitor
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #316
- next_ticket: #316
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 모니터 판정을 liveness 중심에서 policy invariant(FORBIDDEN) 중심으로 전환

### 2026-03-01 | session=codex-v3-stream-next-ticket
- branch: feature/v3-session-policy-stream
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #368, #369, #370, #371, #374, #375, #376, #377, #381
- next_ticket: #368
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 비블로킹 소견은 합당성(정확성/안정성/유지보수성) 기준으로 반영하고, 미반영 시 근거를 코멘트로 남긴다.

### 2026-03-01 | session=codex-issue368-start
- branch: feature/issue-368-backtest-cost-execution
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #368
- next_ticket: #368
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: TASK-V2-012 구현 갭 보완을 위해 cost guard + execution-adjusted fold metric + 회귀 테스트를 함께 반영한다.

### 2026-03-02 | session=codex-v3-stream-next-ticket-369
- branch: feature/v3-session-policy-stream
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #369, #370, #371, #374, #375, #376, #377, #381
- next_ticket: #369
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 구현 티켓은 코드/테스트/문서(요구사항 원장/구현감사/PR traceability) 동시 반영을 기본 원칙으로 진행한다.

### 2026-03-02 | session=codex-issue369-start
- branch: feature/issue-369-model-exit-signal-spec-sync
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #369
- next_ticket: #369
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: v2 사양 기준으로 model_exit_signal을 직접 청산 트리거가 아닌 보조 트리거로 정합화하고 테스트/문서를 동기화한다.

### 2026-03-02 | session=codex-v3-stream-next-ticket-377
- branch: feature/v3-session-policy-stream
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #377, #370, #371, #375, #376, #381
- next_ticket: #377
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: kill switch refresh 재시도 정책(횟수/간격/중단조건)을 코드/테스트/요구사항 원장/감사 문서에 동시 반영한다.

### 2026-03-02 | session=codex-issue377-start
- branch: feature/issue-377-kill-switch-refresh-retry
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #377
- next_ticket: #377
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: refresh 단계를 최대 3회(초기+재시도2), 실패 시 지수 백오프로 재시도하고 성공 시 즉시 중단, 소진 시 오류를 기록한 뒤 다음 단계를 계속 수행한다.

### 2026-03-04 | session=codex-issue409-start
- branch: feature/issue-409-kr-session-exchange-routing
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #409, #318, #325
- next_ticket: #409
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: #409 코드수정/검증 후 프로그램 재시작 및 24h 런타임 모니터링 수행, 모니터 이상 징후는 별도 이슈 발행

### 2026-03-04 | session=claude-issues412-413-414
- branch: feature/issue-412-413-414-runtime-and-governance
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #412, #413, #414
- next_ticket: #412, #413, #414
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: #413 pipefail fix (find_live_pids), #412 startup crash 로깅 강화, #414 PR 거버넌스 preflight 추가

### 2026-03-06 | session=codex-issue438-open-issue-triage
- branch: feature/issue-438-open-issue-triage
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #318, #325, #426, #428, #429, #435, #436, #438
- next_ticket: #438
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 구조적 버그/정책 이슈는 테스트 증거로 close 가능, 런타임 민감 이슈는 실동작 증거 없으면 코멘트만 남기고 open 유지

### 2026-03-08 | session=codex-issue451-ci-fail
- branch: feature/issue-445-project-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #451
- next_ticket: #451
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: 최근 main.py 분리 리팩터에서 남은 미사용 import와 테스트 파일 line-length 위반으로 CI ruff 단계가 실패한다.

### 2026-03-08 | session=codex-issue447-start
- branch: feature/issue-447-trading-cycle-subfunctions
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #447
- next_ticket: #447
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: trading_cycle()를 데이터 수집, 시나리오 평가, 주문 실행, 로깅 helper로 분해하되 함수 시그니처와 동작은 유지한다.

### 2026-03-09 | session=codex-issue459-start
- branch: feature/issue-459-take-profit-responsiveness
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #459, #458, #461
- next_ticket: #459
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: favorable exit 반응성만 분리 개선하며 hard-stop websocket 경로와 책임을 섞지 않는다. main 최신 기준 티켓 브랜치로 진행한다.

### 2026-03-09 | session=codex-issue458-start
- branch: feature/issue-458-kr-websocket-hard-stop
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #458, #459
- next_ticket: #458
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: KR 하드 스탑 초과손실을 줄이기 위해 WebSocket 기반 실시간 손절 감시를 우선 도입하고, 익절 반응성 개선은 #459로 분리한다.

### 2026-03-09 | session=codex-issue459-continue
- branch: feature/issue-459-take-profit-responsiveness
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #459, #458, #461
- next_ticket: #459
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: #458 websocket 경로를 선행 cherry-pick한 뒤 favorable-exit peak hint만 추가 반영한다. hard-stop 직접 실행 책임은 유지한다.

### 2026-03-09 | session=codex-issue461-start
- branch: feature/issue-461-us-realtime-hard-stop
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #461, #458, #459
- next_ticket: #461
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: KR websocket hard-stop 경로를 US_NASDAQ/US_NYSE/US_AMEX까지 일반화하되, favorable exit 책임은 유지하고 PR 본문 거버넌스 검증까지 선행한다.

### 2026-03-09 | session=codex-issue461-runtime-observation
- branch: feature/issue-461-runtime-observation
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #461
- next_ticket: #461
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: main 반영 후 실동작 재시작 로그에서 US realtime hard-stop 증적을 확인하고, websocket 연결/트리거 미관측 시 운영 관측 갭을 별도 이슈로 기록한다.

### 2026-03-27 | session=codex-pr879-review-followup
- branch: feature/issue-862-market-lifecycle-reconciler
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-862, PR #879
- next_ticket: OOR-862
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #879 review 지적을 코드/테스트와 대조해 session transition 강제 rescan 회귀를 TDD로 고정하고, dead parameter/중복 handover 정리 후 검증·push·PR thread reply까지 같은 세션에서 마무리한다.

### 2026-03-27 | session=codex-issue860-review-followup
- branch: feature/issue-860-playbook-session-identity
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #860, PR #877
- next_ticket: #860
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #877 리뷰 스레드를 검토해 타당한 지적만 반영하고, 변경 시 테스트와 PR 코멘트/답글까지 함께 정리한다.

### 2026-03-23 | session=codex-oor-844-start
- branch: feature/issue-844-pnl-usd-settlement
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-844
- next_ticket: OOR-844
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: pnl 결산을 달러 기준으로 전환하려면 결산 시점 환율 소스 존재 여부가 선결 조건이며, 소스 부재 시 구현 보류 근거를 명시해야 한다.

### 2026-03-21 | session=codex-pr847-review-followup
- branch: feature/issue-833-sell-trade-none-branch-test
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #833
- next_ticket: #833
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #847 리뷰 코멘트 기준으로 test-only 수정과 pre-existing follow-up 정리를 수행한다.

### 2026-03-21 | session=codex-pr845-review-followup
- branch: feature/issue-831-recent-sell-guard-helper
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #831, PR #845
- next_ticket: #831
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #845 리뷰 지적을 기술적으로 검토한 뒤 필요한 수정만 반영하고, push 및 GitHub thread reply/comment까지 마무리한다.

### 2026-03-18 | session=codex-pr834-review-followup
- branch: feature/issue-822-korean-policy-validator-stability
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #822, #834
- next_ticket: #822
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: PR #834 리뷰 코멘트를 검토하고 타당성 검증 후 필요한 코드 수정, PR 코멘트, 리뷰 스레드 답글을 정리한다.

### 2026-03-12 | session=codex-oor-816-start
- branch: feature/issue-816-buy-entry-timing
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-816
- next_ticket: OOR-816
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: 매수 시점이 최근 고점 추격으로 치우치는지 재현부터 확인하고, planner/scenario/runtime 중 가장 좁은 수정면에 회귀 테스트를 추가한다.

### 2026-03-09 | session=codex-issue465-466-467-review-followup
- branch: fix/issue-465-466-467-overseas-order-rejection
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #465, #466, #467, PR #468 review comments
- next_ticket: #465
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: PR 리뷰 후 pending-order resubmit network ambiguity는 롤백 금지로 보수화하고, 브로커 제출 상태 재조정은 별도 이슈로 추적한다.


### 2026-03-09 | session=codex-issue469-start
- branch: feature/issue-469-ambiguous-pending-reconcile
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #469, PR #468 review follow-up
- next_ticket: #469
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: pending-order resubmit의 ambiguous submit은 broker pending orders와 holdings를 재조회해 BUY rollback/SELL restore를 broker 확인 후에만 수행한다. PR 본문은 governance validator 통과 후 생성한다.

### 2026-03-11 | session=codex-issue809-rework-v3
- branch: feature/issue-809-harness-engineering-rework-v3
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-809 current Rework state, existing workpad, attached PR #806, rework comments
- next_ticket: OOR-809
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: Rework flow requires a fresh reset again from the current attached PR and workpad; verify handover, close stale PR #806, delete the old workpad, cut a new branch from origin/main, then rerun bootstrap and GitHub publish preflight before deciding whether any code changes remain.

### 2026-03-12 | session=codex-issue810-rework-r8
- branch: feature/issue-810-operations-process-requirements-r8
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-810 current Rework state, issue description, Jiho Son comments, no attached PR metadata, no matching GitHub PRs
- next_ticket: OOR-810
- process_gate_checked: process_ticket=OOR-810 merged_to_feature_branch=n/a
- risks_or_notes: Restart the rework from fresh origin/main@8784453 after the workflow hook fix; preserve the user's unstaged WORKFLOW.md change while re-running reproduction, validation, and publish preflight in this sandbox.

### 2026-03-17 | session=codex-oor-815-start
- branch: feature/issue-815-rebuy-after-sell
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-815
- next_ticket: OOR-815
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: 매도 직후 1분 내 고가 재매수가 planner/scenario/runtime 중 어디서 재진입하는지 재현 신호부터 확보하고 가장 좁은 수정면에 회귀 테스트를 둔다.

### 2026-03-12 | session=codex-oor-370-start
- branch: feature/issue-370-us-session-transition
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-370
- next_ticket: OOR-370
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: US session 전환(US_DAY/US_PRE/US_REG) 버그를 재현 우선으로 확인하고 세션 인지 상태추적, 강제 재스캔, 비거래 세션 차단을 테스트 우선으로 수정한다.

### 2026-03-12 | session=codex-oor-816-start-r2
- branch: feature/issue-816-buy-entry-timing
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-816 current In Progress state, issue description, no attachments, no prior workpad
- next_ticket: OOR-816
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: 재현 신호 확보 후 planner/scenario/runtime 중 가장 좁은 수정면을 택해 최근 고점 추격 매수를 억제하는 회귀 테스트와 문서를 함께 반영한다.

### 2026-03-13 | session=codex-oor-408-rework-r2
- branch: feature/issue-408-us-websocket-hard-stop-diagnostics-r2
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-408 Rework state, OOR-408 prior workpad + PR #813, OOR-403 overlap + PR #814
- next_ticket: OOR-408
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: Rework reset closes stale PR #813 and rebuilds OOR-408 from origin/main@65983bd; OOR-403 is only a partial overlap, so this attempt should keep deeper parse/evaluate/persistence diagnostics in OOR-408 while avoiding unnecessary duplication of startup/subscription-only evidence.

### 2026-03-13 | session=codex-oor-408-merge-r3
- branch: feature/issue-408-us-websocket-hard-stop-diagnostics-r2
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-408 Merging state, live workpad comment, attached PR #815, GitHub review/check state
- next_ticket: OOR-408
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: Approved PR #815 is now conflicting with origin/main@979bb48 after main advanced to fix session-high buy blocking; this session is limited to merge-sync, revalidation, and landing without broadening OOR-408 scope.

### 2026-03-13 | session=codex-oor-819-start
- branch: feature/issue-819-realtime-mode-prep
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-819
- next_ticket: OOR-819
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: LLM client 추상화와 Ollama provider 추가가 목표이며, 기존 Gemini 기본 경로를 유지하는 구성이 필요하다.

### 2026-03-15 | session=codex-oor-811-rework-r3
- branch: feature/issue-811-canonical-restart-before-remove-r3
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-811 Rework state, issue description, human review on PR #822, prior workpad comment, attached PR metadata
- next_ticket: OOR-811
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: Rework reset requires closing PR #822 and deleting the previous Codex Workpad before rebuilding from origin/main@29e05fc with explicit handling for lock-timeout and restart-failure review feedback.

### 2026-03-15 | session=codex-oor-821-rework-r3
- branch: feature/issue-821-linear-korean-writing-rules-rework-r2
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-821 Rework state, issue description, human comments, PR #820/#825 review comments
- next_ticket: OOR-821
- process_gate_checked: process_ticket=OOR-821 merged_to_feature_branch=n/a
- risks_or_notes: Rework flow resets prior attempt by closing PR #825, removing stale workpad, and rebuilding from origin/main@c3ac2f3 with stricter Korean-policy token validation to reduce section-external false positives.

### 2026-03-16 | session=codex-oor-825-rework-r2-implementation
- branch: feature/issue-825-sell-unfilled-loop-r2
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-825 Rework state, issue description, human comment, closed PR #830 metadata
- next_ticket: OOR-825
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: `origin/main@e1575a8` 기준 새 브랜치에서 retry exhausted SELL state를 `trading_cycle` 로 전달해 terminal exit로 격상했다. `ruff`, `validate_docs_sync`, targeted regression, full `pytest --cov` 를 통과했다.

### 2026-03-15 | session=codex-oor-814-rework-r2-start
- branch: feature/issue-814-token-refresh-r2
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-814 Rework state, issue description, human comment, prior PR #819 feedback
- next_ticket: OOR-814
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: Rework reset closes PR #819 and restarts from origin/main; this pass must address reviewer feedback on token-refresh fallback semantics and test assertions while preserving issue scope.

### 2026-03-17 | session=codex-oor-826-start
- branch: feature/issue-826-main-merge-restart-debug
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-826 Todo state, issue description, no existing workpad comment, no attachments
- next_ticket: OOR-826
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: `origin/main@35e2cae` 기준 새 브랜치에서 main merge 후 restart hook 미동작을 재현하고 권한/훅/로그 경로를 우선 점검한다.

### 2026-03-17 | session=codex-issue826-review-fix
- branch: feature/issue-826-main-merge-restart-debug
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #826, PR #832 review comments
- next_ticket: #826
- process_gate_checked: process_ticket=#826 merged_to_feature_branch=n/a
- risks_or_notes: PR #832 리뷰 반영으로 before_remove dry-run을 no-side-effect semantics로 정합화하고 canonical main checkout pull/restart 문서 및 회귀 테스트를 함께 수정한다.

### 2026-03-17 | session=codex-oor-815-start-r2
- branch: feature/issue-815-rebuy-after-sell
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-815
- next_ticket: OOR-815
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: 매도 직후 1분 내 고가 재매수가 planner/scenario/runtime 중 어디서 재진입하는지 재현 신호부터 확보하고 가장 좁은 수정면에 회귀 테스트를 둔다.

### 2026-03-18 | session=codex-oor-822-start
- branch: feature/issue-822-korean-policy-validator-stability
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-822 Todo→In Progress 상태, 이슈 설명/Acceptance Criteria, 기존 workpad 없음, 첨부 PR 없음
- next_ticket: OOR-822
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: Korean policy 검증의 날짜 하드코딩, H1/H2 경계 파싱, 누락 키워드 메시지를 재현한 뒤 TDD로 안정화하고 회귀 테스트를 남긴다.

### 2026-03-18 | session=codex-oor-823-rework-start
- branch: feature/issue-823-pending-orders-quote-dedupe
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-823 Rework 상태, 이슈 설명/Acceptance Criteria, 삭제한 prior workpad, issue attachment 없음, PR #827 review comments
- next_ticket: OOR-823
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: `origin/main@60bff92` 기준 fresh branch로 재시작하며, OOR-813 동작을 유지한 채 `pending_orders`의 4중 호가 조회/ask-bid 추출 중복을 공통 helper로 접는다.

### 2026-03-19 | session=codex-oor-823-main-sync
- branch: feature/issue-823-pending-orders-quote-dedupe
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-823 live workpad, PR #837 checks, origin/main merge commit 1e83227, CI failure in tests/test_runtime_overnight_scripts.py
- next_ticket: OOR-823
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: PR CI가 최신 main merge ref에서만 실패해 main sync를 수행했고, workflow hook의 explicit canonical-root 경로와 runtime-overnight fixture mismatch를 수정한 뒤 전체 게이트를 재검증한다.

### 2026-03-19 | session=codex-pr837-review-followup
- branch: feature/issue-823-pending-orders-quote-dedupe
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #823, #837
- next_ticket: #823
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: PR #837 리뷰 코멘트 대응으로 overseas 컨테이너 우선순위 회귀 방지, helper 예외 경로 테스트 추가, 계획 문서 내부 AI 지시문 제거를 함께 반영한다.

### 2026-03-19 | session=codex-oor-820-start
- branch: feature/issue-820-llm-provider-abstraction
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-820 Todo 상태, 이슈 설명/Acceptance Criteria, 기존 workpad 없음, 첨부 PR 없음
- next_ticket: OOR-820
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `GeminiClient` 명칭 누수와 provider wiring을 재현한 뒤 provider-agnostic entrypoint/factory 추상화로 정리하고 docs/tests까지 함께 갱신한다.

### 2026-03-19 | session=codex-oor-824-start
- branch: feature/issue-824-pending-order-executable-quote-follow-ups
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-824 Todo→In Progress 상태, 이슈 설명/Acceptance Criteria, PR #829 review follow-up, 기존 workpad 없음, 첨부 PR 없음
- next_ticket: OOR-824
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `config.py` gap-cap JSON 이중 파싱, pending-order executable quote extraction/async contract/SELL retry policy, dead branch 정리를 PR #829 후속 범위로 묶어 재현 후 TDD로 수정한다.

### 2026-03-19 | session=codex-pr839-review-followup
- branch: feature/issue-824-pending-order-executable-quote-follow-ups
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-824, PR #839 review summary 및 inline review threads 확인 시작
- next_ticket: OOR-824
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: PR #839 리뷰 지적을 코드 기준으로 재검증한 뒤 필요한 수정만 반영하고, 검증 후 thread reply 및 PR 코멘트를 남긴다.

### 2026-03-19 | session=codex-oor-827-start
- branch: feature/issue-827-loss-increase-investigation
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-827 Todo 상태, 이슈 설명, 기존 workpad 없음, 첨부 PR 없음
- next_ticket: OOR-827
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: 손실 증가 원인을 재현 가능한 데이터/로그로 먼저 고정하고, planner-scorer-risk/runtime 경로를 따라 근본 원인을 문서화하거나 필요한 최소 수정만 반영한다.

### 2026-03-20 | session=codex-oor-828-start
- branch: feature/issue-828-before-remove-git-ancestry-path
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-828 Todo→In Progress 상태, 이슈 설명/Acceptance Criteria, 기존 workpad 없음, 첨부 PR 없음
- next_ticket: OOR-828
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `test_workflow_before_remove_hook_resolves_script_from_nested_worktree_dir` 의 merge-detection 의도를 재현으로 먼저 고정한 뒤, `github_merged=False` 조건에서도 git ancestry 경로만으로 통과함이 드러나도록 테스트 서술과 fixture 입력을 최소 수정한다.

### 2026-03-20 | session=codex-oor-829-start
- branch: feature/issue-829-recent-sell-fee-buffer-design
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-829
- next_ticket: OOR-829
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: OOR-815 recent SELL guard 불변식을 기준으로 fee/slippage buffer 도입 필요성을 재검토하고, 적용 여부와 근거를 코드/테스트/문서에 함께 정리한다.

### 2026-03-20 | session=codex-pr842-review-followup
- branch: feature/issue-829-recent-sell-fee-buffer-design
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: PR #842 review summary 확인, 문서 중복/Claude 지시문 제거 필요성 재검증
- next_ticket: OOR-829
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: implementation plan은 실행 체크리스트만 남기고 설계 판단 근거는 design doc 단일 문서로 정리한다. 선택적 마이너 코멘트는 문서 가독성 개선 범위에서만 반영한다.
### 2026-03-20 | session=codex-oor-830-start
- branch: feature/issue-830-recent-sell-market-setting-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-830 Todo→In Progress 상태, 이슈 설명/Acceptance Criteria, 기존 workpad 없음, 첨부 PR 없음
- next_ticket: OOR-830
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `src/core/order_helpers.py` 의 `_resolve_market_setting` 지연 import 책임 경계를 먼저 재현하고, `window_seconds` 주입 vs 공용 helper 분리안을 비교한 뒤 테스트/최소 문서를 함께 정리한다.

### 2026-03-20 | session=codex-pr844-review-followup
- branch: feature/issue-830-recent-sell-market-setting-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: PR #844 review 본문 확인, import 일관성/docstring/test 보강 필요 여부 재검증
- next_ticket: OOR-830
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `_resolve_market_setting` 모듈 레벨 import가 이미 안전하게 동작하므로 `src/core/order_helpers.py` 내부의 동일 심볼 lazy import 잔존 여부를 정리하고, 리뷰 스레드에는 반영 근거와 검증 결과를 남긴다.

### 2026-03-20 | session=codex-oor-831-start
- branch: feature/issue-831-recent-sell-guard-helper
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-831
- next_ticket: OOR-831
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: PR #833 리뷰 후속으로 recent SELL guard 중복 블록을 공용 helper 계약으로 통합하고 로그/rationale 일관성을 검증한다.

### 2026-03-21 | session=codex-oor-832-start
- branch: feature/issue-832-sell-trade-exchange-priority
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-832 Todo 상태, 이슈 설명/Acceptance Criteria, 기존 workpad 없음, 첨부 PR 없음
- next_ticket: OOR-832
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `get_latest_sell_trade()` 의 `exchange_code` 우선 정렬이 실제 정책인지 먼저 재현/근거로 고정하고, guard window가 참조하는 SELL 선택 규칙을 테스트로 잠근다.

### 2026-03-21 | session=codex-pr846-review-followup
- branch: feature/issue-832-sell-trade-exchange-priority
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: PR #846 review summary 확인, inline review thread 없음, OOR-832 범위 재검증
- next_ticket: OOR-832
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `tests/test_db.py` 에서 동일 timestamp로 고정한 SELL row가 실제로 exchange_code tie-breaker만 검증하는지 재확인했고, thread reply 대신 top-level PR comment로 반영 결과와 검증 명령을 남긴다.

### 2026-03-21 | session=codex-pr846-review-apply
- branch: feature/issue-832-sell-trade-exchange-priority
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: PR #846 follow-up review summary 확인, 남은 관찰 사항은 `timestamp` 단언 보강과 handover 기록 구체화
- next_ticket: OOR-832
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `test_get_latest_sell_trade_prefers_exchange_code_match` 에 동일 timestamp 단언을 추가해 tie-breaker 의미를 고정하고, 이번 세션에서 review 반영 내용과 검증 결과를 PR comment/push로 마무리한다.

### 2026-03-21 | session=codex-oor-833-start-latest
- branch: feature/issue-833-sell-trade-none-branch-test
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-833
- next_ticket: OOR-833
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `get_latest_sell_trade()` 의 `exchange_code=None` 분기 회귀 테스트를 추가하고 `get_latest_buy_trade()` 와 helper 분기 대칭성을 검증한다.

### 2026-03-22 | session=codex-oor-834-start
- branch: feature/issue-834-executable-quote-intent-alignment
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-834, PR #839
- next_ticket: OOR-834
- process_gate_checked: process_ticket=OOR-834 merged_to_feature_branch=n/a
- risks_or_notes: PR #839 후속으로 테스트/주석 의도 정합화만 수행하며 런타임 동작 변경은 금지한다.

### 2026-03-22 | session=codex-oor-835-start
- branch: feature/issue-835-cumulative-loss-buy-guard
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-835 Todo->In Progress, 첨부 PR 없음, workpad 생성, 과거 코멘트 2건 확인
- next_ticket: OOR-835
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: 누적 손실/연속 저성과 scorecard가 현재 BUY 실행 게이트에 연결되지 않는 지점을 먼저 재현하고, SELL/HOLD 및 기존 circuit breaker 불변식을 약화하지 않는 최소 삽입 지점을 선택한다.

### 2026-03-22 | session=codex-oor-835-pr849-review-followup
- branch: feature/issue-835-cumulative-loss-buy-guard
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: PR #849 issue comment 2건 재확인, inline review comment 없음, 머지 블로커는 guard build 예외 전파와 보강 테스트 2건
- next_ticket: OOR-835
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=yes
- risks_or_notes: `generate_playbook()` 에서 guard 구성 실패가 fallback playbook 생성 자체를 막지 않도록 범위를 조정하고, inactive guard 및 fallback+guard 경로를 테스트로 먼저 고정한 뒤 GitHub thread/PR comment에 반영 내역을 남긴다.

### 2026-03-22 | session=codex-oor-836-start
- branch: feature/issue-836-raw-pnl-unit-fallback
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-836
- next_ticket: OOR-836
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: unsupported market raw PnL unit fallback을 market 코드 재사용 대신 명시적 fallback으로 고치고, prompt 계약/테스트/문서를 함께 동기화한다.

### 2026-03-22 | session=codex-pr850-review-followup
- branch: feature/issue-836-raw-pnl-unit-fallback
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #836, PR #850
- next_ticket: #836
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: PR #850 리뷰 지적 반영 및 GitHub thread reply 필요

### 2026-03-22 | session=codex-oor-837-start
- branch: feature/issue-837-telegram-notify-symbol-display
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-837 Todo->In Progress, 첨부 PR 없음, workpad 없음
- next_ticket: OOR-837
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: 텔레그램 거래 알림의 종목 표기를 `{name}({code})`로 고정하고, 현재 문자열 조립 경로를 먼저 재현한 뒤 테스트로 계약을 잠근다.

### 2026-03-22 | session=codex-pr851-review-followup
- branch: feature/issue-837-telegram-notify-symbol-display
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-837, PR #851 review comment(US 일반 trading cycle stock_name 테스트 갭)
- next_ticket: OOR-837
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: 외부 리뷰 지적은 코드 기준으로 먼저 검증하고, 미국 일반 trading cycle에서 `notify_trade_execution(stock_name=...)` 전파 여부를 failing test로 확인한 뒤 필요한 최소 수정만 반영한다.

### 2026-03-22 | session=codex-oor-838-start
- branch: feature/issue-838-before-remove-nested-dir-github-fallback
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-838 Todo->In Progress, 첨부 PR 없음, workpad 생성
- next_ticket: OOR-838
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `before_remove` nested dir 경로에서 `merged_by_git=False` / `github_merged=True` 조합을 먼저 재현하고, OOR-828에서 분리된 GitHub fallback 회귀 범위를 테스트 이름/주석/fixture 입력으로 명시적으로 고정한다.

### 2026-03-22 | session=codex-pr852-review-followup
- branch: feature/issue-838-before-remove-nested-dir-github-fallback
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-838, PR #852
- next_ticket: OOR-838
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #852 리뷰 코멘트를 코드 기준으로 재검증하고, PR에 올릴 수정과 후속 이슈 분리를 결정한 뒤 각 inline thread에 조치 결과를 남긴다.

### 2026-03-22 | session=codex-oor-838-merge
- branch: feature/issue-838-before-remove-nested-dir-github-fallback
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-838, PR #852, Linear workpad comment
- next_ticket: OOR-838
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `Merging` 상태에서 PR #852 의 mergeability/check/review 상태와 workpad를 최신 `HEAD` 기준으로 맞춘 뒤 squash merge 및 Linear `Done` 전이를 마무리한다.

### 2026-03-22 | session=codex-oor-840-start
- branch: feature/issue-840-daily-mode-runtime-warning
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-840
- next_ticket: OOR-840
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: daily mode 배치 cadence가 startup anchor 기준이라는 점을 재현으로 확인한 뒤, startup/runtime warning과 문서 정합성을 가장 좁은 수정면으로 반영한다.

### 2026-03-22 | session=codex-pr854-review-followup
- branch: feature/issue-840-daily-mode-runtime-warning
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-840, PR #854 review threads 확인 예정
- next_ticket: OOR-840
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #854 외부 리뷰 코멘트를 코드/테스트 기준으로 재검증하고, 필요한 수정은 failing test로 먼저 고정한 뒤 inline thread reply와 PR comment에 조치 근거를 남긴다.

### 2026-03-22 | session=codex-oor-841-start
- branch: feature/issue-841-latest-trade-helper-contract-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-841
- next_ticket: OOR-841
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `get_latest_buy_trade()` / `get_latest_sell_trade()` 계약 비대칭과 `decision_id` 필터 차이를 재현으로 고정한 뒤, 의도/버그 여부를 코드·테스트·문서 중 최소 변경으로 명시한다.

### 2026-03-23 | session=codex-oor-842-start
- branch: feature/issue-842-evolution-output-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-842
- next_ticket: OOR-842
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: unattended Linear 실행으로 하루 마감 시 생성되는 불필요한 Python 파일 경로를 재현하고, 진화 결과물 기록 방식을 브랜치 오염 없이 정리한다.
### 2026-03-23 | session=codex-pr856-review-followup
- branch: feature/issue-842-evolution-output-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #842, PR #856
- next_ticket: #842
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #856 리뷰 지적을 기술적으로 검토한 뒤 필요한 코드/테스트/문서만 반영하고, 검증 후 push 및 GitHub 코멘트를 남긴다.
### 2026-03-23 | session=codex-pr856-review-followup-2
- branch: feature/issue-842-evolution-output-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #842, PR #856
- next_ticket: #842
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: 추가 리뷰에서 single-line fenced JSON 경로와 테스트 false-positive 가능성을 점검해 필요한 후속 수정만 반영한다.

### 2026-03-23 | session=codex-pr857-review-followup
- branch: feature/issue-844-pnl-usd-settlement
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-844, PR #857
- next_ticket: OOR-844
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #857 리뷰 기준으로 handover 중복 엔트리 제거와 trading_cycle US SELL 정산 환율 전달 누락만 최소 수정하고, 관련 회귀 테스트 및 GitHub 코멘트까지 남긴다.

### 2026-03-23 | session=codex-oor-847-start
- branch: feature/issue-847-evolution-prompt-check
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-847
- next_ticket: OOR-847
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: 진화 프롬프트의 기간별 컨텍스트 레벨/플레이북 컨텍스트 주입이 현재 구조에서 왜 비활성화됐는지 재현 기반으로 진단하고, 문서 선조치 범위를 먼저 확정한다.

### 2026-03-23 | session=codex-pr858-review-followup
- branch: feature/issue-847-evolution-prompt-check
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-847, PR #858
- next_ticket: OOR-847
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #858 리뷰 기준으로 L1-L7 레이어 순서와 `get_context_data()` 설명을 코드로 재검증하고, 계획 문서의 AI 지시 주석 제거 후 검증/푸시/PR thread reply까지 마무리한다.

### 2026-03-23 | session=codex-oor-845-start
- branch: feature/issue-845-before-remove-unmerged-negative
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-845, GitHub issue #853
- next_ticket: OOR-845
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: nested `before_remove` 미병합 음수 경로를 재현으로 먼저 고정하고, workflow hook이 canonical restart 및 stop/start side effect를 남기지 않는지 회귀 테스트로 최소 수정한다.

### 2026-03-23 | session=codex-pr860-review-followup
- branch: feature/issue-845-before-remove-unmerged-negative
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-845, PR #860
- next_ticket: OOR-845
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #860 리뷰 지적을 코드 기준으로 검증한 뒤 필요한 수정만 반영하고, 테스트/검증 후 모든 리뷰 스레드에 조치 결과를 thread reply로 남긴다.

### 2026-03-24 | session=codex-oor-848-start
- branch: feature/issue-848-evolution-context-bundle-reinjection
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-848, OOR-847
- next_ticket: OOR-848
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `EvolutionOptimizer.generate_recommendation()` 에 `L6/L5 + sampled context_snapshot` 재주입이 목표이며, recommendation JSON schema 와 planner 컨텍스트 계약은 유지해야 한다.

### 2026-03-24 | session=codex-pr861-review-followup
- branch: feature/issue-848-evolution-context-bundle-reinjection
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-848, PR #861
- next_ticket: OOR-848
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #861 리뷰 스레드를 코드베이스와 대조해 타당한 지적만 반영하고, 검증 후 thread reply 및 PR 코멘트까지 마무리한다.

### 2026-03-24 | session=codex-oor-849-start
- branch: feature/issue-849-market-scoped-upper-layer-aggregate
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-849, OOR-847, OOR-848
- next_ticket: OOR-849
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `src/context/aggregator.py` 상위 레이어 `L4-L1` 에 market-scoped aggregate key 를 추가하고, 기존 global aggregate key backward compatibility 와 evolution prompt 선행 계약을 테스트/문서로 함께 고정한다.

### 2026-03-24 | session=codex-pr862-review-followup
- branch: feature/issue-849-market-scoped-upper-layer-aggregate
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-849, PR #862
- next_ticket: OOR-849
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #862 리뷰 지적 1~4를 코드/테스트 기준으로 검증하고, 실제 버그만 수정한 뒤 thread reply 및 PR 코멘트까지 남긴다.

### 2026-03-24 | session=codex-pr862-rereview-followup
- branch: feature/issue-849-market-scoped-upper-layer-aggregate
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-849, PR #862 re-review
- next_ticket: OOR-849
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: 재리뷰에서 지적된 aggregator 데드 코드 `_sum_grouped_market_values` 및 `_resolve_grouped_total` 의 실제 호출 여부를 검증하고, 미사용이 맞으면 제거 후 회귀 테스트와 추가 커밋/푸시를 수행한다.

### 2026-03-24 | session=codex-oor-850-start
- branch: feature/issue-850-before-remove-cwd-helper
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-850
- next_ticket: OOR-850
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `tests/test_runtime_overnight_scripts.py` nested `workflow_before_remove_hook` 테스트의 `cwd` 경로 중복 계산을 helper 계약으로 흡수하고, nested positive/negative regression 의미를 유지하는 최소 범위 TDD 수정만 수행한다.

### 2026-03-24 | session=codex-oor-851-start
- branch: feature/issue-851-monthly-rollup-month-boundary
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-849, OOR-851
- next_ticket: OOR-851
- process_gate_checked: process_ticket=OOR-849 merged_to_feature_branch=yes
- risks_or_notes: monthly rollup 가 target month 밖 ISO week 를 포함하는지 재현 후, global/market-scoped upper-layer rollup 회귀까지 함께 보강한다.

### 2026-03-24 | session=codex-pr864-review-followup
- branch: feature/issue-851-monthly-rollup-month-boundary
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: #851, PR #864
- next_ticket: #851
- process_gate_checked: process_ticket=#306,#308 merged_to_feature_branch=yes
- risks_or_notes: PR #864 review 코멘트 확인 후 코드/테스트/PR 스레드 답변까지 한 세션에서 처리 예정

### 2026-03-24 | session=codex-oor-852-start
- branch: feature/issue-852-upper-layer-rollup-mixed-context
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-851, OOR-852
- next_ticket: OOR-852
- process_gate_checked: process_ticket=OOR-849 merged_to_feature_branch=yes
- risks_or_notes: mixed global-only + market-scoped monthly context 에서 상위 rollup 이 global-only 분을 누락하는지 재현한 뒤, quarterly/annual/legacy rollup 수정과 회귀 테스트를 TDD로 묶어 검증한다.

### 2026-03-24 | session=codex-oor-852-merge
- branch: feature/issue-852-upper-layer-rollup-mixed-context
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-852, PR #865
- next_ticket: OOR-852
- process_gate_checked: process_ticket=OOR-849 merged_to_feature_branch=yes
- risks_or_notes: PR #865 mergeability, review, checks, workpad 상태를 재확인하고 `land` skill로 squash merge 및 Linear `Done` 전환까지 마무리한다.

### 2026-03-25 | session=codex-oor-856-start
- branch: feature/issue-856-daily-held-position-coverage
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-856
- next_ticket: OOR-856
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `TRADE_MODE=daily` 경로가 scanner `top_n` 밖의 기존 보유 포지션을 평가에서 누락하는지 재현하고, daily exit coverage 를 보장하는 최소 수정과 회귀 테스트/문서 업데이트를 함께 완료한다.

### 2026-03-25 | session=codex-pr868-review-followup
- branch: feature/issue-856-daily-held-position-coverage
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-856, PR #868
- next_ticket: OOR-856
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #868 리뷰 스레드를 코드/테스트와 대조해 타당한 지적만 반영하고, 검증 후 모든 변경 사항에 대한 thread reply 및 PR 코멘트까지 마무리한다.

### 2026-03-25 | session=codex-oor-857-start
- branch: feature/issue-857-live-daily-hard-stop-monitoring
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-857
- next_ticket: OOR-857
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: live + `TRADE_MODE=daily` 런타임에서 realtime hard-stop websocket startup guard 를 재현하고, held position 보호 누락을 TDD로 고정한 뒤 문서/운영 checklist 와 함께 정합화한다.

### 2026-03-25 | session=codex-pr869-review-followup
- branch: feature/issue-857-live-daily-hard-stop-monitoring
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-857, PR #869
- next_ticket: OOR-857
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #869 top-level review 에 남은 테스트 순서 의존/부정 케이스 누락 지적의 타당성을 코드 기준으로 재검증하고, 필요한 최소 수정과 thread 대응/재검증까지 한 세션에서 마무리한다.

### 2026-03-25 | session=codex-oor-853-rework-fresh-branch
- branch: feature/issue-853-dashboard-cleanup-rework
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-853, PR #866
- next_ticket: OOR-853
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: reviewer 코멘트에 따라 PR #866/workpad를 정리했고, `origin/main@af9cd97` 기준 fresh branch에서 재현부터 다시 시작한다.

### 2026-03-25 | session=codex-pr870-review-followup
- branch: feature/issue-853-dashboard-cleanup-rework
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-853, PR #870
- next_ticket: OOR-853
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #870 review thread를 코드/테스트와 대조해 타당한 지적만 반영하고, 수정 사항 검증 후 push 및 inline reply/top-level comment까지 같은 세션에서 마무리한다.

### 2026-03-25 | session=codex-oor-854-start
- branch: feature/issue-854-dashboard-status-summary
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-853, OOR-854, PR #870
- next_ticket: OOR-854
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `main` 에 반영된 OOR-853 대시보드 정리 결과를 기준으로 시장별 상태 요약과 diagnostics 분리 구조를 재정의하고, summary/history/filter 규칙을 문서/테스트까지 함께 정리한다.

### 2026-03-25 | session=codex-pr871-review-followup
- branch: feature/issue-854-dashboard-status-summary
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-854, PR #871
- next_ticket: OOR-854
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #871 review thread를 코드/테스트와 대조해 타당한 지적만 반영하고, 수정 사항 검증 후 push 및 PR thread/top-level comment까지 같은 세션에서 마무리한다.

### 2026-03-26 | session=codex-oor-865-start
- branch: feature/issue-865-us-reg-daily-batch-coverage
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-865
- next_ticket: OOR-865
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: daily mode 6시간 cadence가 US_REG를 건너뛰는 재현 신호를 먼저 확보하고, 장중 추가 배치 보장 로직을 TDD로 최소 수정한다.

### 2026-03-26 | session=codex-pr874-review-followup
- branch: feature/issue-865-us-reg-daily-batch-coverage
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-865, PR #874
- next_ticket: OOR-865
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #874 review thread를 코드/테스트와 대조해 타당한 지적만 반영하고, 수정 사항 검증 후 push 및 GitHub inline reply까지 같은 세션에서 마무리한다.

### 2026-03-26 | session=codex-oor-858-start
- branch: feature/issue-858-market-close-detection
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-858
- next_ticket: OOR-858
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: `open_markets` 전체 비움 조건에 묶인 개별 마켓 close 누락을 먼저 재현하고, 마켓별 open/close diff 기반 처리와 상태 정리 타이밍을 TDD로 고정한다.

### 2026-03-26 | session=codex-pr875-review-followup
- branch: feature/issue-858-market-close-detection
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-858, PR #875
- next_ticket: OOR-858
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #875 review thread를 코드/테스트와 대조해 타당한 지적만 반영하고, 수정 사항 검증 후 push 및 GitHub review thread reply/top-level comment까지 같은 세션에서 마무리한다.

### 2026-03-26 | session=codex-oor-859-start
- branch: feature/issue-859-us-session-dst
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-859
- next_ticket: OOR-859
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: US session classifier 가 고정 KST 윈도우에 묶여 DST 시즌 1시간 어긋나는 신호를 재현하고, `America/New_York` 기준 세션 축으로 order policy 와 schedule 경로를 정합화한다.

### 2026-03-27 | session=codex-pr876-review-followup
- branch: feature/issue-859-us-session-dst
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-859, PR #876
- next_ticket: OOR-859
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #876 review thread를 코드/테스트와 대조해 타당한 지적만 반영하고, 수정 사항을 TDD로 검증한 뒤 push 및 GitHub review thread reply/top-level comment까지 같은 세션에서 마무리한다.

### 2026-03-27 | session=codex-oor-860-start
- branch: feature/issue-860-playbook-session-identity
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-860
- next_ticket: OOR-860
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: playbook 저장 identity가 `date + market + slot`에 묶여 있어 session-aware persistence와 resume selection 규칙을 모델/DB/테스트로 함께 정합화해야 한다.

### 2026-03-27 | session=codex-oor-861-start
- branch: feature/issue-861-runtime-tracking-cache-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-861
- next_ticket: OOR-861
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: runtime tracking cache session 종료 정리 누락을 재현 기준으로 고정하고, session transition에서 `active_stocks`/`scan_candidates`/`last_scan_time` carry-over 여부를 테스트 우선으로 검증한다.

### 2026-03-27 | session=codex-pr878-review-followup
- branch: feature/issue-861-runtime-tracking-cache-cleanup
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-861, PR #878
- next_ticket: OOR-861
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: PR #878 review 지적을 코드/테스트/문서와 대조해 불필요한 helper 제거 여부와 회귀 테스트 신호를 재평가하고, 타당한 항목만 반영한 뒤 검증·push·PR 코멘트까지 같은 세션에서 마무리한다.

### 2026-03-27 | session=codex-oor-862-start
- branch: feature/issue-862-market-lifecycle-reconciler
- docs_checked: docs/workflow.md, docs/commands.md, docs/agent-constraints.md
- open_issues_reviewed: OOR-858, OOR-861, OOR-862
- next_ticket: OOR-862
- process_gate_checked: process_ticket=n/a merged_to_feature_branch=n/a
- risks_or_notes: market lifecycle를 전역 open_markets 존재 여부 대신 per-market diff로 재구성하며, close/session transition/stale runtime cleanup 트리거를 분리 검증한다.
