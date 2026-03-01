# Command Reference

## Common Command Failures

**Critical: Learn from failures. Never repeat the same failed command without modification.**

## Repository VCS Rule (Mandatory)

- 이 저장소의 티켓/PR/코멘트 작업은 Gitea 기준으로 수행한다.
- `gh`(GitHub CLI) 명령 사용은 금지한다.
- 기본 도구는 `tea`이며, `tea` 미지원 케이스만 Gitea API를 fallback으로 사용한다.
- 실행 전 `docs/workflow.md`의 `Gitea CLI Formatting Troubleshooting`을 반드시 확인한다.

## Session Handover Preflight (Mandatory)

- 세션 시작 직후(코드 변경 전) 아래 명령을 먼저 실행한다.

```bash
python3 scripts/session_handover_check.py --strict
```

- 실패 시 `workflow/session-handover.md` 최신 엔트리를 보강한 뒤 재실행한다.

## Docs Sync Validator (Mandatory for docs changes)

- 문서 변경 PR에서는 아래 명령으로 동기화 검증을 먼저 실행한다.

```bash
python3 scripts/validate_docs_sync.py
```

- 검증 실패 시 메시지 기준으로 즉시 수정한다.
  - `absolute link is forbidden`: 문서 링크에 절대경로(`/...`) 사용
  - `broken link`: 상대경로 링크 대상 파일/앵커 누락
  - `missing core doc link reference`: `README.md`/`CLAUDE.md` 핵심 링크 누락
  - `duplicated API endpoint row`: `docs/commands.md` API endpoint 표 중복 행

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

# Run full test suite with coverage (998 tests across 41 files)
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

# Runtime verification monitor (coverage + forbidden invariants)
bash scripts/runtime_verify_monitor.sh

# Runtime monitor with explicit policy timezone (example: KST)
POLICY_TZ=Asia/Seoul bash scripts/runtime_verify_monitor.sh

# Session handover gate (must pass before implementation)
python3 scripts/session_handover_check.py --strict

# Follow runtime verification log
tail -f data/overnight/runtime_verify_*.log

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
| `GET /api/pnl/history` | P&L history over time |
| `GET /api/positions` | Current open positions |

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

## KIS API TR_ID 참조 문서

**TR_ID를 추가하거나 수정할 때 반드시 공식 문서를 먼저 확인할 것.**

공식 문서: `docs/한국투자증권_오픈API_전체문서_20260221_030000.xlsx`

> ⚠️ 커뮤니티 블로그, GitHub 예제 등 비공식 자료의 TR_ID는 오래되거나 틀릴 수 있음.
> 실제로 `VTTT1006U`(미국 매도 — 잘못됨)가 오랫동안 코드에 남아있던 사례가 있음 (Issue #189).

### 주요 TR_ID 목록

| 구분 | 모의투자 TR_ID | 실전투자 TR_ID | 시트명 |
|------|---------------|---------------|--------|
| 해외주식 매수 (미국) | `VTTT1002U` | `TTTT1002U` | 해외주식 주문 |
| 해외주식 매도 (미국) | `VTTT1001U` | `TTTT1006U` | 해외주식 주문 |

새로운 TR_ID가 필요할 때:
1. 위 xlsx 파일에서 해당 거래 유형의 시트를 찾는다.
2. 모의투자(`VTTT`) / 실전투자(`TTTT`) 컬럼을 구분하여 정확한 값을 사용한다.
3. 코드에 출처 주석을 남긴다: `# Source: 한국투자증권_오픈API_전체문서 — '<시트명>' 시트`

## Environment Setup

```bash
# Create .env file from example
cp .env.example .env

# Edit .env with your credentials
# Required: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, GEMINI_API_KEY

# Verify configuration
python -c "from src.config import Settings; print(Settings())"
```
