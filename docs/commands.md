# Command Reference

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
pip install -e ".[dev]"

# Run full test suite with coverage (551 tests across 25 files)
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

# Run with dashboard enabled
python -m src.main --mode=paper --dashboard

# Docker
docker compose up -d ouroboros          # Run agent
docker compose --profile test up test   # Run tests in container
```

## Dashboard

The FastAPI dashboard provides read-only monitoring of the trading system.

### Starting the Dashboard

```bash
# Via CLI flag
python -m src.main --mode=paper --dashboard

# Via environment variable
DASHBOARD_ENABLED=true python -m src.main --mode=paper
```

Dashboard runs as a daemon thread on `DASHBOARD_HOST:DASHBOARD_PORT` (default: `127.0.0.1:8080`).

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | HTML dashboard UI |
| `GET /api/status` | Daily trading status by market |
| `GET /api/playbook/{date}` | Playbook for specific date (query: `market`) |
| `GET /api/scorecard/{date}` | Daily scorecard from L6_DAILY context |
| `GET /api/performance` | Performance metrics by market and combined |
| `GET /api/context/{layer}` | Context data by layer L1-L7 (query: `timeframe`) |
| `GET /api/decisions` | Decision log entries (query: `limit`, `market`) |
| `GET /api/scenarios/active` | Today's matched scenarios |

## Telegram Commands

When `TELEGRAM_COMMANDS_ENABLED=true` (default), the bot accepts these interactive commands:

| Command | Description |
|---------|-------------|
| `/help` | List available commands |
| `/status` | Show trading status (mode, markets, P&L) |
| `/positions` | Display account summary (balance, cash, P&L) |
| `/report` | Daily summary metrics (trades, P&L, win rate) |
| `/scenarios` | Show today's playbook scenarios |
| `/review` | Display recent scorecards (L6_DAILY layer) |
| `/dashboard` | Show dashboard URL if enabled |
| `/stop` | Pause trading |
| `/resume` | Resume trading |

Commands are only processed from the authorized `TELEGRAM_CHAT_ID`.

## Environment Setup

```bash
# Create .env file from example
cp .env.example .env

# Edit .env with your credentials
# Required: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, GEMINI_API_KEY

# Verify configuration
python -c "from src.config import Settings; print(Settings())"
```
