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

## Branch Strategy (Mandatory)

- Team operation default branch is the **program feature branch**, not `main`.
- Ticket-level development happens only on **ticket temp branches** cut from the program feature branch.
- Ticket PR merges into program feature branch are allowed after verifier approval.
- Until final user sign-off, `main` merge is prohibited.

## Gitea CLI Formatting Troubleshooting

Issue/PR 본문 작성 시 줄바꿈(`\n`)이 문자열 그대로 저장되는 문제가 반복될 수 있다. 원인은 `-d "...\n..."` 형태에서 쉘/CLI가 이스케이프를 실제 개행으로 해석하지 않기 때문이다.

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
