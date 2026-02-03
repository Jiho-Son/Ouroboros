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

Self-evolving AI trading agent for Korean stock markets (KIS API). The main loop in `src/main.py` orchestrates four components in a 60-second cycle per stock:

1. **Broker** (`src/broker/kis_api.py`) — Async KIS API client with automatic OAuth token refresh, leaky-bucket rate limiter (10 RPS), and POST body hash-key signing. Uses a custom SSL context with disabled hostname verification for the VTS (virtual trading) endpoint due to a known certificate mismatch.

2. **Brain** (`src/brain/gemini_client.py`) — Sends structured prompts to Google Gemini, parses JSON responses into `TradeDecision` objects. Forces HOLD when confidence < threshold (default 80). Falls back to safe HOLD on any parse/API error.

3. **Risk Manager** (`src/core/risk_manager.py`) — **READ-ONLY by policy** (see `docs/agents.md`). Circuit breaker halts all trading via `SystemExit` when daily P&L drops below -3.0%. Fat-finger check rejects orders exceeding 30% of available cash.

4. **Evolution** (`src/evolution/optimizer.py`) — Analyzes high-confidence losing trades from SQLite, asks Gemini to generate new `BaseStrategy` subclasses, validates them by running the full pytest suite, and simulates PR creation.

**Data flow per cycle:** Fetch orderbook + balance → calculate P&L → get Gemini decision → validate with risk manager → execute order → log to SQLite (`src/db.py`).

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

35 tests across three files. `asyncio_mode = "auto"` in pyproject.toml — async tests need no special decorator. The `settings` fixture in `conftest.py` provides safe defaults with test credentials and in-memory DB.

- `test_risk.py` (11) — Circuit breaker boundaries, fat-finger edge cases
- `test_broker.py` (6) — Token lifecycle, rate limiting, hash keys, network errors
- `test_brain.py` (18) — JSON parsing, confidence threshold, malformed responses, prompt construction
