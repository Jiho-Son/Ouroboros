# Development Workflow

## Git Workflow Policy

**CRITICAL: All code changes MUST follow this workflow. Direct pushes to `main` are ABSOLUTELY PROHIBITED.**

1. **Create Gitea Issue First** — All features, bug fixes, and policy changes require a Gitea issue before any code is written
2. **Create Feature Branch** — Branch from `main` using format `feature/issue-{N}-{short-description}`
   - After creating the branch, run `git pull origin main` and rebase to ensure the branch is up to date
3. **Implement Changes** — Write code, tests, and documentation on the feature branch
4. **Create Pull Request** — Submit PR to `main` branch referencing the issue number
5. **Review & Merge** — After approval, merge via PR (squash or merge commit)

**Never commit directly to `main`.** This policy applies to all changes, no exceptions.

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
