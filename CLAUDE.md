# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Workflow Policy

**CRITICAL: All code changes MUST follow this workflow. Direct pushes to `main` are ABSOLUTELY PROHIBITED.**

1. **Create Gitea Issue First** — All features, bug fixes, and policy changes require a Gitea issue before any code is written
2. **Create Feature Branch** — Branch from `main` using format `feature/issue-{N}-{short-description}`
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

## Common Command Failures

**Critical: Learn from failures. Never repeat the same failed command without modification.**

### tea CLI (Gitea Command Line Tool)

#### ❌ TTY Error - Interactive Confirmation Fails
```bash
~/bin/tea issues create --repo X --title "Y" --description "Z"
# Error: huh: could not open a new TTY: open /dev/tty: no such device or address
```
**💡 Reason:** tea tries to open `/dev/tty` for interactive confirmation prompts, which is unavailable in non-interactive environments.

**✅ Solution:** Use `YES=""` environment variable to bypass confirmation
```bash
YES="" ~/bin/tea issues create --repo jihoson/The-Ouroboros --title "Title" --description "Body"
YES="" ~/bin/tea issues edit <number> --repo jihoson/The-Ouroboros --description "Updated body"
YES="" ~/bin/tea pulls create --repo jihoson/The-Ouroboros --head feature-branch --base main --title "Title" --description "Body"
```

**📝 Notes:**
- Always set default login: `~/bin/tea login default local`
- Use `--repo jihoson/The-Ouroboros` when outside repo directory
- tea is preferred over direct Gitea API calls for consistency

#### ❌ Wrong Parameter Name
```bash
tea issues create --body "text"
# Error: flag provided but not defined: -body
```
**💡 Reason:** Parameter is `--description`, not `--body`.

**✅ Solution:** Use correct parameter name
```bash
YES="" ~/bin/tea issues create --description "text"
```

### Gitea API (Direct HTTP Calls)

#### ❌ Wrong Hostname
```bash
curl http://gitea.local:3000/api/v1/...
# Error: Could not resolve host: gitea.local
```
**💡 Reason:** Gitea instance runs on `localhost:3000`, not `gitea.local`.

**✅ Solution:** Use correct hostname (but prefer tea CLI)
```bash
curl http://localhost:3000/api/v1/repos/jihoson/The-Ouroboros/issues \
  -H "Authorization: token $GITEA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"...", "body":"..."}'
```

**📝 Notes:**
- Prefer `tea` CLI over direct API calls
- Only use curl for operations tea doesn't support

### Git Commands

#### ❌ User Not Configured
```bash
git commit -m "message"
# Error: Author identity unknown
```
**💡 Reason:** Git user.name and user.email not set.

**✅ Solution:** Configure git user
```bash
git config user.name "agentson"
git config user.email "agentson@localhost"
```

#### ❌ Permission Denied on Push
```bash
git push origin branch
# Error: User permission denied for writing
```
**💡 Reason:** Repository access token lacks write permissions or user lacks repo write access.

**✅ Solution:**
1. Verify user has write access to repository (admin grants this)
2. Ensure git credential has correct token with `write:repository` scope
3. Check remote URL uses correct authentication

### Python/Pytest

#### ❌ Module Import Error
```bash
pytest tests/test_foo.py
# ModuleNotFoundError: No module named 'src'
```
**💡 Reason:** Package not installed in development mode.

**✅ Solution:** Install package with dev dependencies
```bash
pip install -e ".[dev]"
```

#### ❌ Async Test Hangs
```python
async def test_something():  # Hangs forever
    result = await async_function()
```
**💡 Reason:** Missing pytest-asyncio or wrong configuration.

**✅ Solution:** Already configured in pyproject.toml
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```
No decorator needed for async tests.

## Build & Test Commands

```bash
# Install all dependencies (production + dev)
pip install ".[dev]"

# Run full test suite with coverage
pytest -v --cov=src --cov-report=term-missing

# Run a single test file
pytest tests/test_risk.py -v

# Run a single test by name
pytest tests/test_brain.py -k "test_parse_valid_json" -v

# Lint
ruff check src/ tests/

# Type check (strict mode, non-blocking in CI)
mypy src/ --strict

# Run the trading agent
python -m src.main --mode=paper

# Docker
docker compose up -d ouroboros          # Run agent
docker compose --profile test up test   # Run tests in container
```

## Architecture

Self-evolving AI trading agent for Korean stock markets (KIS API). The main loop in `src/main.py` orchestrates five components in a 60-second cycle per stock:

1. **Broker** (`src/broker/kis_api.py`) — Async KIS API client with automatic OAuth token refresh, leaky-bucket rate limiter (10 RPS), and POST body hash-key signing. Uses a custom SSL context with disabled hostname verification for the VTS (virtual trading) endpoint due to a known certificate mismatch.

2. **Brain** (`src/brain/gemini_client.py`) — Sends structured prompts to Google Gemini, parses JSON responses into `TradeDecision` objects. Forces HOLD when confidence < threshold (default 80). Falls back to safe HOLD on any parse/API error.

3. **Risk Manager** (`src/core/risk_manager.py`) — **READ-ONLY by policy** (see `docs/agents.md`). Circuit breaker halts all trading via `SystemExit` when daily P&L drops below -3.0%. Fat-finger check rejects orders exceeding 30% of available cash.

4. **Context Tree** (`src/context/`) — **NEW: Pillar 2 implementation.** 7-tier hierarchical memory (L1-L7) from real-time quotes to generational wisdom. Auto-aggregates daily → weekly → monthly → quarterly → annual → legacy. See [`docs/context-tree.md`](docs/context-tree.md) for details.

5. **Evolution** (`src/evolution/optimizer.py`) — Analyzes high-confidence losing trades from SQLite, asks Gemini to generate new `BaseStrategy` subclasses, validates them by running the full pytest suite, and simulates PR creation.

**Data flow per cycle:** Fetch orderbook + balance → calculate P&L → query context tree → get Gemini decision → validate with risk manager → execute order → log to SQLite + context layers (`src/db.py`).

## Key Constraints (from `docs/agents.md`)

- `core/risk_manager.py` is **READ-ONLY**. Changes require human approval.
- Circuit breaker threshold (-3.0%) may only be made stricter, never relaxed.
- Fat-finger protection (30% max order size) must always be enforced.
- Confidence < 80 **must** force HOLD — this rule cannot be weakened.
- All code changes require corresponding tests. Coverage must stay >= 80%.
- Generated strategies must pass the full test suite before activation.

## Configuration

Pydantic Settings loaded from `.env` (see `.env.example`). Required vars: `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO` (format `XXXXXXXX-XX`), `GEMINI_API_KEY`. Tests use in-memory SQLite (`DB_PATH=":memory:"`) and dummy credentials via `tests/conftest.py`.

## Test Structure

72 tests across five files. `asyncio_mode = "auto"` in pyproject.toml — async tests need no special decorator. The `settings` fixture in `conftest.py` provides safe defaults with test credentials and in-memory DB.

- `test_risk.py` (11) — Circuit breaker boundaries, fat-finger edge cases
- `test_broker.py` (6) — Token lifecycle, rate limiting, hash keys, network errors
- `test_brain.py` (18) — JSON parsing, confidence threshold, malformed responses, prompt construction
- `test_market_schedule.py` (19) — Market open/close logic, timezone handling, DST, lunch breaks
- `test_context.py` (18) — **NEW:** Context tree CRUD, aggregation logic, retention policies, layer metadata
