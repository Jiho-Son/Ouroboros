# Development Workflow

## Git Workflow Policy

**CRITICAL: All code changes MUST follow this workflow. Direct pushes to `main` are ABSOLUTELY PROHIBITED.**

1. **Create Gitea Issue First** — All features, bug fixes, and policy changes require a Gitea issue before any code is written
2. **Create Program Feature Branch** — Branch from `main` for the whole development stream
   - Format: `feature/{epic-or-stream-name}`
3. **Create Ticket Temp Branch** — Branch from the program feature branch per ticket
   - Format: `feature/issue-{N}-{short-description}`
4. **Implement Per Ticket** — Write code, tests, and documentation on the ticket temp branch
5. **Create Pull Request to Program Feature Branch** — `feature/issue-N-* -> feature/{stream}`
6. **Review/Verify and Merge into Program Feature Branch** — user approval not required
7. **Final Integration PR to main** — Only after all ticket stages complete and explicit user approval

**Never commit directly to `main`.** This policy applies to all changes, no exceptions.

## Agent Gitea Preflight (Mandatory)

Gitea 이슈/PR/코멘트 작업 전에 모든 에이전트는 아래를 먼저 확인해야 한다.

1. `docs/commands.md`의 `tea CLI` 실패 사례/해결 패턴 확인
2. 본 문서의 `Gitea CLI Formatting Troubleshooting` 확인
3. 명령 실행 전 `gh`(GitHub CLI) 사용 금지 확인

강제 규칙:
- 이 저장소 협업 명령은 `tea`를 기본으로 사용한다.
- `gh issue`, `gh pr` 등 GitHub CLI 명령은 사용 금지다.
- `tea` 실패 시 동일 명령 재시도 전에 원인/수정사항을 PR 코멘트에 남긴다.
- 필요한 경우에만 Gitea API(`localhost:3000`)를 fallback으로 사용한다.

## Session Handover Gate (Mandatory)

새 세션에서 구현/검증을 시작하기 전에 아래를 선행해야 한다.

1. `docs/workflow.md`, `docs/commands.md`, `docs/agent-constraints.md` 재확인
2. `workflow/session-handover.md`에 최신 세션 엔트리 추가
3. `python3 scripts/session_handover_check.py --strict` 통과 확인

강제 규칙:
- handover check 실패 상태에서 코드 수정/이슈 상태 전이/PR 생성 금지
- 최신 handover 엔트리는 현재 작업 브랜치를 명시해야 한다
- 최신 handover 엔트리는 당일(UTC) 날짜를 포함해야 한다

## Branch Strategy (Mandatory)

- Team operation default branch is the **program feature branch**, not `main`.
- Ticket-level development happens only on **ticket temp branches** cut from the program feature branch.
- Ticket PR merges into program feature branch are allowed after verifier approval.
- Until final user sign-off, `main` merge is prohibited.
- 각 에이전트는 주요 의사결정(리뷰 지적, 수정 방향, 검증 승인)마다 PR 코멘트를 적극 작성해 의사결정 과정을 남긴다.

## Backtest Gate Policy (Mandatory)

사람 의존도를 줄이기 위해 백테스트 검증은 자동 게이트를 기본으로 한다.

- 워크플로우: `.github/workflows/backtest-gate.yml`
- 실행 스크립트: `scripts/backtest_gate.sh`
- 기본 모드: `auto` (변경 파일 기반 실행/skip 판정)
- 정기 스케줄: daily `full` 강제 실행
- 수동 재실행: workflow dispatch + `mode` 지정

강제 규칙:
- 백테스트 민감 변경(PR/feature push)에서 게이트 실패 시 머지 금지
- 스케줄 게이트 실패 시 이슈 등록 후 원인/복구 계획 기록
- `python` 대신 `python3` 기준으로 실행한다

## Gitea CLI Formatting Troubleshooting

Issue/PR 본문 작성 시 줄바꿈(`\n`)이 문자열 그대로 저장되는 문제가 반복될 수 있다. 원인은 `-d "...\n..."` 형태에서 쉘/CLI가 이스케이프를 실제 개행으로 해석하지 않기 때문이다.

코멘트도 동일한 문제가 자주 발생하므로, 코멘트는 파일 기반 래퍼를 표준으로 사용한다.

```bash
# 권장: 파일/STDIN 기반 코멘트 등록 (줄바꿈 보존)
cat > /tmp/review.md <<'EOF'
리뷰 반영 완료했습니다.

- 항목 1
- 항목 2
EOF

scripts/tea_comment.sh 374 /tmp/review.md
# 또는
cat /tmp/review.md | scripts/tea_comment.sh 374 -
```

권장 패턴:

```bash
ISSUE_BODY=$(cat <<'EOF'
## Summary
- 변경 내용 1
- 변경 내용 2

## Why
- 배경 1
- 배경 2

## Scope
- 포함 범위
- 제외 범위
EOF
)

tea issues create \
  -t "docs: 제목" \
  -d "$ISSUE_BODY"
```

PR도 동일하게 적용:

```bash
PR_BODY=$(cat <<'EOF'
## Summary
- ...

## Validation
- python3 scripts/validate_ouroboros_docs.py
EOF
)

tea pr create \
  --base main \
  --head feature/issue-N-something \
  --title "docs: ... (#N)" \
  --description "$PR_BODY"
```

금지 패턴:

- `-d "line1\nline2"` (웹 UI에 `\n` 문자 그대로 노출될 수 있음)
- 본문에 백틱/괄호를 인라인로 넣고 적절한 quoting 없이 즉시 실행

## Agent Workflow

**Modern AI development leverages specialized agents for concurrent, efficient task execution.**

### Parallel Execution Strategy

Use **git worktree** or **subagents** (via the Task tool) to handle multiple requirements simultaneously:

- Each task runs in independent context
- Parallel branches for concurrent features
- Isolated test environments prevent interference
- Faster iteration with distributed workload

### Specialized Agent Roles

Deploy task-specific agents as needed instead of handling everything in the main conversation:

- **Conversational Agent** (main) — Interface with user, coordinate other agents
- **Ticket Management Agent** — Create/update Gitea issues, track task status
- **Design Agent** — Architectural planning, RFC documents, API design
- **Code Writing Agent** — Implementation following specs
- **Testing Agent** — Write tests, verify coverage, run test suites
- **Documentation Agent** — Update docs, docstrings, CLAUDE.md, README
- **Review Agent** — Code review, lint checks, security audits
- **Custom Agents** — Created dynamically for specialized tasks (performance analysis, migration scripts, etc.)

### When to Use Agents

**Prefer spawning specialized agents for:**

1. Complex multi-file changes requiring exploration
2. Tasks with clear, isolated scope (e.g., "write tests for module X")
3. Parallel work streams (feature A + bugfix B simultaneously)
4. Long-running analysis (codebase search, dependency audit)
5. Tasks requiring different contexts (multiple git worktrees)

**Use the main conversation for:**

1. User interaction and clarification
2. Quick single-file edits
3. Coordinating agent work
4. High-level decision making

### Implementation

```python
# Example: Spawn parallel test and documentation agents
task_tool(
    subagent_type="general-purpose",
    prompt="Write comprehensive tests for src/markets/schedule.py",
    description="Write schedule tests"
)

task_tool(
    subagent_type="general-purpose",
    prompt="Update README.md with global market feature documentation",
    description="Update README"
)
```

Use `run_in_background=True` for independent tasks that don't block subsequent work.

### Main -> Verifier Directive Contract (Mandatory)

메인 에이전트가 검증 에이전트에 작업을 위임할 때, 아래 6개를 누락하면 지시가 무효다.

1. 검증 대상 범위: `REQ-*`, `TASK-*`, 코드/로그 경로
2. 검증 방법: 실행 커맨드와 관측 포인트(예: 세션별 로그 키워드)
3. 합격 기준: PASS 조건을 수치/문구로 명시
4. 실패 기준: FAIL 조건을 수치/문구로 명시
5. 미관측 기준: `NOT_OBSERVED` 조건과 즉시 에스컬레이션 규칙
6. 증적 형식: PR 코멘트에 `Coverage Matrix` 표로 제출

`NOT_OBSERVED` 처리 규칙:
- 요구사항 항목이 관측되지 않았으면 PASS로 간주 금지
- `NOT_OBSERVED`는 운영상 `FAIL`과 동일하게 처리
- `NOT_OBSERVED`가 하나라도 있으면 승인/머지 금지

`FORBIDDEN` 처리 규칙:
- 정책 위반 신호(예: 주말 `session=KRX_REG`)는 `FORBIDDEN=HIT`으로 별도 기록한다
- `FORBIDDEN=HIT`은 즉시 `P0 FAIL`로 간주하고 모니터링 승인 불가
- 실시간 모니터는 `alive`만으로 정상 판정하지 않는다(정책 불변식 통과가 필수)

### Process-Change-First Rule (Mandatory)

재발 방지/운영 규칙 변경이 결정되면, 기능 구현 티켓보다 먼저 서버(feature branch)에 반영해야 한다.

- 순서: `process ticket merge` -> `implementation ticket start`
- process ticket 미반영 상태에서 기능 티켓 코딩/머지 금지
- 세션 전환 시에도 동일 규칙 유지

### Implementation Start Gate (Mandatory)

구현 티켓을 시작하기 전에 아래 3개를 모두 만족해야 한다.

1. `process ticket merge` 증적 확인 (feature branch 반영 커밋/PR)
2. `workflow/session-handover.md` 최신 엔트리에 `next_ticket`과 `process_gate_checked` 기록
3. `python3 scripts/session_handover_check.py --strict` 통과

강제 규칙:
- 위 3개 중 하나라도 불충족이면 코드/테스트 수정 금지
- 서브에이전트 지시도 동일하게 제한한다 (`process merged 확인 전 read-only 탐색만 허용`)
- 성급 착수 발견 시 구현 작업을 즉시 중단하고 handover/proces gate부터 복구한다

### Ticket Maturity Stages (Mandatory)

모든 티켓은 아래 4단계를 순서대로 통과해야 한다.

1. `Implemented`: 코드/문서 변경 완료
2. `Integrated`: 호출 경로/파이프라인 연결 완료
3. `Observed`: 런타임/실행 증적 확보 완료
4. `Accepted`: 정적 Verifier + Runtime Verifier 승인 완료

강제 규칙:
- 단계 점프 금지 (예: Implemented -> Accepted 금지)
- `Observed` 전에는 완료 선언 금지
- `Accepted` 전에는 머지 금지

## Code Review Checklist

**CRITICAL: Every PR review MUST verify plan-implementation consistency.**

Before approving any PR, the reviewer (human or agent) must check ALL of the following:

### 1. Plan Consistency (MANDATORY)

- [ ] **Implementation matches the approved plan** — Compare the actual code changes against the plan created during `EnterPlanMode`. Every item in the plan must be addressed.
- [ ] **No unplanned changes** — If the implementation includes changes not in the plan, they must be explicitly justified.
- [ ] **No plan items omitted** — If any planned item was skipped, the reason must be documented in the PR description.
- [ ] **Scope matches** — The PR does not exceed or fall short of the planned scope.

### 2. Safety & Constraints

- [ ] `src/core/risk_manager.py` is unchanged (READ-ONLY)
- [ ] Circuit breaker threshold not weakened (only stricter allowed)
- [ ] Fat-finger protection (30% max order) still enforced
- [ ] Confidence < 80 still forces HOLD
- [ ] No hardcoded API keys or secrets

### 3. Quality

- [ ] All new/modified code has corresponding tests
- [ ] Test coverage >= 80%
- [ ] `ruff check src/ tests/` passes (no lint errors)
- [ ] No `assert` statements removed from tests

### 4. Workflow

- [ ] PR references the Gitea issue number
- [ ] Feature branch follows naming convention (`feature/issue-N-description`)
- [ ] Commit messages are clear and descriptive
- [ ] 이슈/PR 작업 전에 `docs/commands.md`와 본 문서 트러블슈팅 섹션을 확인했다
- [ ] `gh` 명령을 사용하지 않고 `tea`(또는 허용된 Gitea API fallback)만 사용했다
- [ ] Main -> Verifier 지시가 Directive Contract 6개 항목을 모두 포함한다
- [ ] Verifier 결과에 `Coverage Matrix`(PASS/FAIL/NOT_OBSERVED)가 있고, `NOT_OBSERVED=0`이다
- [ ] Process-change-first 대상이면 해당 process PR이 먼저 머지되었다
- [ ] 티켓 단계가 `Implemented -> Integrated -> Observed -> Accepted` 순서로 기록되었다
- [ ] 정적 Verifier와 Runtime Verifier 승인 코멘트가 모두 존재한다
