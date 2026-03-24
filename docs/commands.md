# Command Reference

## Common Command Failures

**Critical: Learn from failures. Never repeat the same failed command without modification.**

## Repository VCS Rule (Mandatory)

- 이 저장소의 티켓/PR/코멘트 작업은 GitHub 기준으로 수행한다.
- 인증/읽기 preflight 는 `gh auth status` 와 `gh pr status` 로 확인한다.
- unattended PR 생성/수정/조회 기본 도구는 `gh` 다.
- 실행 전 `docs/workflow.md`의 GitHub preflight / PR body troubleshooting 섹션을 반드시 확인한다.

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
  - `missing dynamic test count guidance`: `docs/testing.md`에 `pytest --collect-only -q` 가이드 누락

### GitHub CLI

#### Required publish preflight
```bash
gh auth status
git ls-remote origin HEAD
gh pr status
```

**📝 Notes:**
- 현재 브랜치에 PR 이 없으면 `gh pr status` 결과에 현재 브랜치 PR 이 비어 있을 수 있다.
- 그 경우에도 `gh` 자체가 정상 동작하면 create/edit/read 경로는 사용 가능하다.

#### ❌ PR body stored with stale or escaped-newline text
```bash
gh pr edit 374 --body-file /tmp/pr_body.md
# Body still fails validate_pr_body because draft text contains literal \n or stale content
```
**💡 Reason:** Draft body text was prepared incorrectly or not revalidated after edits.

**✅ Solution:** Validate the file before and after upload
```bash
cat > /tmp/pr_body.md <<'EOF'
## Summary
- REQ-OPS-001 / TASK-OPS-001 / TEST-OPS-001
EOF

python3 scripts/validate_pr_body.py --body-file /tmp/pr_body.md
gh pr edit 374 --body-file /tmp/pr_body.md
python3 scripts/validate_pr_body.py --pr 374
```

**📝 Notes:**
- `gh pr create` / `gh pr edit` reads the file contents exactly once.
- Always re-run the post-check against the live PR after updating the body.

#### PR Body Governance Preflight (Mandatory before `gh pr create`)

PR 본문 파일 준비 후, **생성 전에** 아래 명령으로 형식 + 거버넌스 traceability를 검증한다.

```bash
python3 scripts/validate_pr_body.py --body-file /tmp/pr_body.md
```

검증 항목: `\n` 이스케이프, 마크다운 헤더, 리스트, **REQ-ID, TASK-ID, TEST-ID** 포함.

검증 실패 시:
- PR 본문에 실제 REQ-ID/TASK-ID/TEST-ID를 채운 뒤 재검증 통과 후에만 `gh pr create` 실행
- placeholder(`REQ-...`, `TASK-...`, `TEST-...`) 형태는 CI에서 실패 처리됨

#### PR Body Post-Check (Mandatory)

PR 생성 직후 본문이 `\n` 문자열로 깨지지 않았는지 반드시 확인한다.

```bash
python3 scripts/validate_pr_body.py --pr <PR_NUMBER>
```

검증 실패 시:
- PR 본문을 API patch 또는 파일 기반 본문으로 즉시 수정
- 같은 명령으로 재검증 통과 후에만 리뷰/머지 진행

#### ❌ GitHub auth is missing or pointed at the wrong account
```bash
gh pr status
# authentication required or insufficient scope
```
**💡 Reason:** `gh` is not authenticated for repo PR operations in this session.

**✅ Solution:** Verify auth first, then retry the intended `gh pr ...` command
```bash
gh auth status
gh pr status
```

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
# Runtime paper mode is banned (#426)
python -m src.main --mode=live

# Run with dashboard enabled
python -m src.main --mode=live --dashboard

# Runtime verification monitor (coverage + forbidden invariants)
bash scripts/runtime_verify_monitor.sh

# Runtime monitor with explicit policy timezone (example: KST)
POLICY_TZ=Asia/Seoul bash scripts/runtime_verify_monitor.sh

# After a restart, inspect US websocket hard-stop diagnostics in the latest run log
latest_run="$(ls -t data/overnight/run_*.log | head -n1)"
rg -n "Realtime hard-stop websocket monitor started|Realtime websocket action=connect|Realtime websocket action=(resubscribe|subscribe|unsubscribe)|Realtime websocket action=(parsed_us_event|ignore_us_parse_failure)|Realtime hard-stop evaluate action=(enter|result)|Realtime price event action=(received_us_event|no_trigger|dispatch_trigger)|Realtime hard-stop action=(decision_logged|trade_logged|persisted)" "$latest_run"

# Session handover gate (must pass before implementation)
python3 scripts/session_handover_check.py --strict

# Follow runtime verification log
tail -f data/overnight/runtime_verify_*.log

# Docker
docker compose up -d ouroboros          # Run agent
docker compose --profile test up test   # Run tests in container
```

## Overnight Runtime Operations

`scripts/run_overnight.sh`, `scripts/stop_overnight.sh`, `scripts/morning_report.sh`,
and `scripts/runtime_verify_monitor.sh` now share the same runtime-instance defaults.

Operational policy:
- Keep the continuously running canonical 운영 프로세스 only in the checkout whose
  git branch is `main`.
- Manage the post-merge restart from Symphony `hooks.before_remove` in
  [`WORKFLOW.md`](../WORKFLOW.md): the hook first resolves the worktree top-level
  with `git rev-parse --show-toplevel`, then runs
  `bash "$repo_root/scripts/symphony_before_remove_canonical_restart.sh"` before
  deletion, discovers the canonical `main` checkout with
  `git worktree list --porcelain`, pulls `origin/main`, and restarts only that
  canonical runtime.
- `--dry-run` on `scripts/symphony_before_remove_canonical_restart.sh` is now a
  no-side-effect planning mode: it validates the canonical `main` checkout and
  prints the intended restart inputs without calling `fetch origin` or writing
  `canonical_restart.log` / marker files.
- For squash merges where plain git ancestry cannot prove inclusion, the hook
  falls back to recent closed GitHub PR metadata (`head.ref` + `head.sha`)
  and may miss cases where additional commits were pushed after merge.
- Non-`main` worktrees may run the same scripts concurrently for validation; the
  scripts auto-isolate `LOG_DIR`, `LIVE_RUNTIME_LOCK_PATH`, `DASHBOARD_PORT`, and
  `TMUX_SESSION_PREFIX` per branch unless you override them explicitly.
- The hook stores its dedupe marker and restart log under the canonical state
  root (`data/overnight/canonical_restart.*` by default), so non-`main`
  worktree runtime state remains isolated. `canonical_restart.log` now records
  hook invocation context plus skip/failure/start decisions for debugging.
- When `flock` is unavailable the hook falls back to `mkdir` lock with timeout
  (`CANONICAL_RESTART_LOCK_WAIT_SECONDS`, default 30s) and exits with an error
  instead of waiting forever on stale lock state.
- If stop succeeds but start fails, the hook logs
  `[CRITICAL] canonical runtime start failed after stop; manual intervention required`
  and exits non-zero without advancing the dedupe marker.

Examples:

```bash
# Canonical main checkout: stable state root and dashboard port 8080
bash scripts/run_overnight.sh
bash scripts/runtime_verify_monitor.sh
tail -f data/overnight/runtime_verify_*.log

# Hook regression proof: merged worktree cleanup restarts canonical main once
pytest tests/test_runtime_overnight_scripts.py -k 'before_remove_canonical_restart' -v

# Non-main worktree: same commands auto-scope to data/overnight/<branch-slug>
bash scripts/run_overnight.sh
bash scripts/runtime_verify_monitor.sh
```

US websocket hard-stop restart validation:

- Use the latest `run_*.log` from the same `LOG_DIR`; do not mix logs across worktrees.
- Required startup evidence: `Realtime hard-stop websocket monitor started` and `Realtime websocket action=connect`.
- Required tracked-symbol evidence for US holdings under realtime monitoring: at least one `Realtime websocket action=subscribe` or `Realtime websocket action=resubscribe` line for the tracked symbol.
- Required event-path evidence: at least one of `Realtime websocket action=parsed_us_event`, `Realtime websocket action=ignore_us_parse_failure`, `Realtime price event action=no_trigger`, or `Realtime price event action=dispatch_trigger`.
- If a websocket hard-stop SELL fires, require all of `Realtime hard-stop action=decision_logged`, `Realtime hard-stop action=trade_logged`, and `Realtime hard-stop action=persisted ... source=websocket_hard_stop`.
- If any required row stays unobserved during the validation window, record it as `NOT_OBSERVED` and treat the runtime verification as failed.

Override knobs when you need a custom location or port:

## Dashboard

The FastAPI dashboard provides read-only monitoring of the trading system.

### Starting the Dashboard

```bash
# Via CLI flag
python -m src.main --mode=live --dashboard

# Via environment variable
DASHBOARD_ENABLED=true python -m src.main --mode=live
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
| `GET /api/decisions` | Decision log entries with trace/filter support |
| `GET /api/scenarios/active` | Today's matched scenarios |
| `GET /api/pnl/history` | P&L history over time |
| `GET /api/positions` | Current open positions |

### Decision History Filters

`GET /api/decisions` supports these query params:

- `market=all|<market>`
- `session_id=all|<session>`
- `action=all|BUY|SELL|HOLD`
- `stock_code=<substring>`
- `min_confidence=<0-100>`
- `from_date=<YYYY-MM-DD>`
- `to_date=<YYYY-MM-DD>`
- `matched_only=true|false`
- `limit=<1-500>`

The response also includes distinct `markets` / `sessions` metadata plus
`llm_prompt` / `llm_response` fields for per-decision trace inspection.

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
# Required: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO
# If LLM_PROVIDER=gemini, also set GEMINI_API_KEY
# If LLM_PROVIDER=ollama, ensure OLLAMA_BASE_URL / OLLAMA_MODEL are correct

# Verify configuration
python -c "from src.config import Settings; print(Settings())"
```
