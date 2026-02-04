# The Ouroboros

AI-powered trading agent for global stock markets with self-evolution capabilities.

## Quick Start

```bash
# Setup
pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your KIS and Gemini API credentials

# Test
pytest -v --cov=src

# Run (paper trading)
python -m src.main --mode=paper
```

## Documentation

- **[Workflow Guide](docs/workflow.md)** — Git workflow policy and agent-based development
- **[Command Reference](docs/commands.md)** — Common failures, build commands, troubleshooting
- **[Architecture](docs/architecture.md)** — System design, components, data flow
- **[Testing](docs/testing.md)** — Test structure, coverage requirements, writing tests
- **[Agent Policies](docs/agents.md)** — Prime directives, constraints, prohibited actions

## Core Principles

1. **Safety First** — Risk manager is READ-ONLY and enforces circuit breakers
2. **Test Everything** — 80% coverage minimum, all changes require tests
3. **Issue-Driven Development** — All work goes through Gitea issues → feature branches → PRs
4. **Agent Specialization** — Use dedicated agents for design, coding, testing, docs, review

## Project Structure

```
src/
├── broker/          # KIS API client (domestic + overseas)
├── brain/           # Gemini AI decision engine
├── core/            # Risk manager (READ-ONLY)
├── evolution/       # Self-improvement optimizer
├── markets/         # Market schedules and timezone handling
├── db.py            # SQLite trade logging
├── main.py          # Trading loop orchestrator
└── config.py        # Settings (from .env)

tests/               # 54 tests across 4 files
docs/                # Extended documentation
```

## Key Commands

```bash
pytest -v --cov=src              # Run tests with coverage
ruff check src/ tests/           # Lint
mypy src/ --strict               # Type check

python -m src.main --mode=paper  # Paper trading
python -m src.main --mode=live   # Live trading (⚠️ real money)

# Gitea workflow (requires tea CLI)
YES="" ~/bin/tea issues create --repo jihoson/The-Ouroboros --title "..." --description "..."
YES="" ~/bin/tea pulls create --head feature-branch --base main --title "..." --description "..."
```

## Markets Supported

- 🇰🇷 Korea (KRX)
- 🇺🇸 United States (NASDAQ, NYSE, AMEX)
- 🇯🇵 Japan (TSE)
- 🇭🇰 Hong Kong (SEHK)
- 🇨🇳 China (Shanghai, Shenzhen)
- 🇻🇳 Vietnam (Hanoi, HCM)

Markets auto-detected based on timezone and enabled in `ENABLED_MARKETS` env variable.

## Critical Constraints

⚠️ **Non-Negotiable Rules** (see [docs/agents.md](docs/agents.md)):

- `src/core/risk_manager.py` is **READ-ONLY** — changes require human approval
- Circuit breaker at -3.0% P&L — may only be made **stricter**
- Fat-finger protection: max 30% of cash per order — always enforced
- Confidence < 80 → force HOLD — cannot be weakened
- All code changes → corresponding tests → coverage ≥ 80%

## Contributing

See [docs/workflow.md](docs/workflow.md) for the complete development process.

**TL;DR:**
1. Create issue in Gitea
2. Create feature branch: `feature/issue-N-description`
3. Implement with tests
4. Open PR
5. Merge after review
