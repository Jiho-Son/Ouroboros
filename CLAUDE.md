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

# Run with dashboard
python -m src.main --mode=paper --dashboard
```

## Telegram Notifications (Optional)

Get real-time alerts for trades, circuit breakers, and system events via Telegram.

### Quick Setup

1. **Create bot**: Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot`
2. **Get chat ID**: Message [@userinfobot](https://t.me/userinfobot) → `/start`
3. **Configure**: Add to `.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   TELEGRAM_CHAT_ID=123456789
   TELEGRAM_ENABLED=true
   ```
4. **Test**: Start bot conversation (`/start`), then run the agent

**Full documentation**: [src/notifications/README.md](src/notifications/README.md)

### What You'll Get

- 🟢 Trade execution alerts (BUY/SELL with confidence)
- 🚨 Circuit breaker trips (automatic trading halt)
- ⚠️ Fat-finger rejections (oversized orders blocked)
- ℹ️ Market open/close notifications
- 📝 System startup/shutdown status

### Interactive Commands

With `TELEGRAM_COMMANDS_ENABLED=true` (default), the bot supports 9 bidirectional commands: `/help`, `/status`, `/positions`, `/report`, `/scenarios`, `/review`, `/dashboard`, `/stop`, `/resume`.

**Fail-safe**: Notifications never crash the trading system. Missing credentials or API errors are logged but trading continues normally.

## Smart Volatility Scanner (Optional)

Python-first filtering pipeline that reduces Gemini API calls by pre-filtering stocks using technical indicators.

### How It Works

1. **Fetch Rankings** — KIS API volume surge rankings (top 30 stocks)
2. **Python Filter** — RSI + volume ratio calculations (no AI)
   - Volume > 200% of previous day
   - RSI(14) < 30 (oversold) OR RSI(14) > 70 (momentum)
3. **AI Judgment** — Only qualified candidates (1-3 stocks) sent to Gemini

### Configuration

Add to `.env` (optional, has sensible defaults):
```bash
RSI_OVERSOLD_THRESHOLD=30    # 0-50, default 30
RSI_MOMENTUM_THRESHOLD=70    # 50-100, default 70
VOL_MULTIPLIER=2.0           # Volume threshold (2.0 = 200%)
SCANNER_TOP_N=3              # Max candidates per scan
```

### Benefits

- **Reduces API costs** — Process 1-3 stocks instead of 20-30
- **Python-based filtering** — Fast technical analysis before AI
- **Evolution-ready** — Selection context logged for strategy optimization
- **Fault-tolerant** — Falls back to static watchlist on API failure

### Realtime Mode Only

Smart Scanner runs in `TRADE_MODE=realtime` only. Daily mode uses static watchlists for batch efficiency.

## Documentation

- **[Workflow Guide](docs/workflow.md)** — Git workflow policy and agent-based development
- **[Command Reference](docs/commands.md)** — Common failures, build commands, troubleshooting
- **[Architecture](docs/architecture.md)** — System design, components, data flow
- **[Context Tree](docs/context-tree.md)** — L1-L7 hierarchical memory system
- **[Testing](docs/testing.md)** — Test structure, coverage requirements, writing tests
- **[Agent Policies](docs/agents.md)** — Prime directives, constraints, prohibited actions
- **[Requirements Log](docs/requirements-log.md)** — User requirements and feedback tracking

## Core Principles

1. **Safety First** — Risk manager is READ-ONLY and enforces circuit breakers
2. **Test Everything** — 80% coverage minimum, all changes require tests
3. **Issue-Driven Development** — All work goes through Gitea issues → feature branches → PRs
4. **Agent Specialization** — Use dedicated agents for design, coding, testing, docs, review

## Requirements Management

User requirements and feedback are tracked in [docs/requirements-log.md](docs/requirements-log.md):

- New requirements are added chronologically with dates
- Code changes should reference related requirements
- Helps maintain project evolution aligned with user needs
- Preserves context across conversations and development cycles

## Project Structure

```
src/
├── analysis/        # Technical analysis (RSI, volatility, smart scanner)
├── backup/          # Disaster recovery (scheduler, cloud storage, health)
├── brain/           # Gemini AI decision engine (prompt optimizer, context selector)
├── broker/          # KIS API client (domestic + overseas)
├── context/         # L1-L7 hierarchical memory system
├── core/            # Risk manager (READ-ONLY)
├── dashboard/       # FastAPI read-only monitoring (8 API endpoints)
├── data/            # External data integration (news, market data, calendar)
├── evolution/       # Self-improvement (optimizer, daily review, scorecard)
├── logging/         # Decision logger (audit trail)
├── markets/         # Market schedules and timezone handling
├── notifications/   # Telegram alerts + bidirectional commands (9 commands)
├── strategy/        # Pre-market planner, scenario engine, playbook store
├── db.py            # SQLite trade logging
├── main.py          # Trading loop orchestrator
└── config.py        # Settings (from .env)

tests/               # 551 tests across 25 files
docs/                # Extended documentation
```

## Key Commands

```bash
pytest -v --cov=src              # Run tests with coverage
ruff check src/ tests/           # Lint
mypy src/ --strict               # Type check

python -m src.main --mode=paper  # Paper trading
python -m src.main --mode=paper --dashboard  # With dashboard
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
- Confidence 임계값 (market_outlook별, 낮출 수 없음): BEARISH ≥ 90, NEUTRAL/기본 ≥ 80, BULLISH ≥ 75
- All code changes → corresponding tests → coverage ≥ 80%

## Contributing

See [docs/workflow.md](docs/workflow.md) for the complete development process.

**TL;DR:**
1. Create issue in Gitea
2. Create feature branch: `feature/issue-N-description`
3. Implement with tests
4. Open PR
5. Merge after review
