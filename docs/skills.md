# The Ouroboros - Available Skills & Tools

## Development Tools

### Run Tests
```bash
pytest -v --cov=src --cov-report=term-missing
```
Run the full test suite with coverage reporting. **Must pass before any merge.**

### Run Specific Test Module
```bash
pytest tests/test_risk.py -v
pytest tests/test_broker.py -v
pytest tests/test_brain.py -v
```

### Type Checking
```bash
python -m mypy src/ --strict
```

### Linting
```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Operational Tools

### Start Trading Agent (Development)
```bash
python -m src.main --mode=paper
```
Runs the agent in paper-trading mode (no real orders).

### Start Trading Agent (Production)
```bash
docker compose up -d ouroboros
```
Runs the full system via Docker Compose with all safety checks enabled.

### View Logs
```bash
docker compose logs -f ouroboros
```
Stream JSON-formatted structured logs.

### Run Backtester
```bash
python -m src.evolution.optimizer --backtest --days=30
```
Analyze the last 30 days of trade logs and generate performance metrics.

## Evolution Tools

### Generate New Strategy
```bash
python -m src.evolution.optimizer --evolve
```
Triggers the evolution engine to:
1. Analyze `trade_logs.db` for failing patterns
2. Ask Gemini to generate a new strategy
3. Run tests on the new strategy
4. Create a PR if tests pass

### Validate Strategy
```bash
pytest tests/ -k "strategy" -v
```
Run only strategy-related tests to validate a new strategy file.

## Deployment Tools

### Build Docker Image
```bash
docker build -t ouroboros:latest .
```

### Deploy with Docker Compose
```bash
docker compose up -d
```

### Health Check
```bash
curl http://localhost:8080/health
```

## Database Tools

### View Trade Logs
```bash
sqlite3 data/trade_logs.db "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20;"
```

### Export Trade History
```bash
sqlite3 -header -csv data/trade_logs.db "SELECT * FROM trades;" > trades_export.csv
```

## Safety Checklist (Pre-Deploy)

- [ ] `pytest -v --cov=src` passes with >= 80% coverage
- [ ] `ruff check src/ tests/` reports no errors
- [ ] `.env` file contains valid KIS and Gemini API keys
- [ ] Circuit breaker threshold is set to -3.0% or stricter
- [ ] Rate limiter is configured for KIS API limits
- [ ] Docker health check endpoint responds 200
