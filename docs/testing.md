# Testing Guidelines

## Test Structure

**551 tests** across **25 files**. `asyncio_mode = "auto"` in pyproject.toml — async tests need no special decorator.

The `settings` fixture in `conftest.py` provides safe defaults with test credentials and in-memory DB.

### Test Files

#### Core Components

##### `tests/test_risk.py` (14 tests)
- Circuit breaker boundaries and exact threshold triggers
- Fat-finger edge cases and percentage validation
- P&L calculation edge cases
- Order validation logic

##### `tests/test_broker.py` (11 tests)
- OAuth token lifecycle
- Rate limiting enforcement
- Hash key generation
- Network error handling
- SSL context configuration

##### `tests/test_brain.py` (24 tests)
- Valid JSON parsing and markdown-wrapped JSON handling
- Malformed JSON fallback
- Missing fields handling
- Invalid action validation
- Confidence threshold enforcement
- Empty response handling
- Prompt construction for different markets

##### `tests/test_market_schedule.py` (24 tests)
- Market open/close logic
- Timezone handling (UTC, Asia/Seoul, America/New_York, etc.)
- DST (Daylight Saving Time) transitions
- Weekend handling and lunch break logic
- Multiple market filtering
- Next market open calculation

##### `tests/test_db.py` (3 tests)
- Database initialization and table creation
- Trade logging with all fields (market, exchange_code, decision_id)
- Query and retrieval operations

##### `tests/test_main.py` (37 tests)
- Trading loop orchestration
- Market iteration and stock processing
- Dashboard integration (`--dashboard` flag)
- Telegram command handler wiring
- Error handling and graceful shutdown

#### Strategy & Playbook (v2)

##### `tests/test_pre_market_planner.py` (37 tests)
- Pre-market playbook generation
- Gemini API integration for scenario creation
- Timeout handling and defensive playbook fallback
- Multi-market playbook generation

##### `tests/test_scenario_engine.py` (44 tests)
- Scenario matching against live market data
- Confidence scoring and threshold filtering
- Multiple scenario type handling
- Edge cases (no match, partial match, expired scenarios)

##### `tests/test_playbook_store.py` (23 tests)
- Playbook persistence to SQLite
- Date-based retrieval and market filtering
- Playbook status management (generated, active, expired)
- JSON serialization/deserialization

##### `tests/test_strategy_models.py` (33 tests)
- Pydantic model validation for scenarios, playbooks, decisions
- Field constraints and default values
- Serialization round-trips

#### Analysis & Scanning

##### `tests/test_volatility.py` (24 tests)
- ATR and RSI calculation accuracy
- Volume surge ratio computation
- Momentum scoring
- Breakout/breakdown pattern detection
- Market scanner watchlist management

##### `tests/test_smart_scanner.py` (13 tests)
- Python-first filtering pipeline
- RSI and volume ratio filter logic
- Candidate scoring and ranking
- Fallback to static watchlist

#### Context & Memory

##### `tests/test_context.py` (18 tests)
- L1-L7 layer storage and retrieval
- Context key-value CRUD operations
- Timeframe-based queries
- Layer metadata management

##### `tests/test_context_scheduler.py` (5 tests)
- Periodic context aggregation scheduling
- Layer summarization triggers

#### Evolution & Review

##### `tests/test_evolution.py` (24 tests)
- Strategy optimization loop
- High-confidence losing trade analysis
- Generated strategy validation

##### `tests/test_daily_review.py` (10 tests)
- End-of-day review generation
- Trade performance summarization
- Context layer (L6_DAILY) integration

##### `tests/test_scorecard.py` (3 tests)
- Daily scorecard metrics calculation
- Win rate, P&L, confidence tracking

#### Notifications & Commands

##### `tests/test_telegram.py` (25 tests)
- Message sending and formatting
- Rate limiting (leaky bucket)
- Error handling (network timeout, invalid token)
- Auto-disable on missing credentials
- Notification types (trade, circuit breaker, fat-finger, market events)

##### `tests/test_telegram_commands.py` (31 tests)
- 9 command handlers (/help, /status, /positions, /report, /scenarios, /review, /dashboard, /stop, /resume)
- Long polling and command dispatch
- Authorization filtering by chat_id
- Command response formatting

#### Dashboard

##### `tests/test_dashboard.py` (14 tests)
- FastAPI endpoint responses (8 API routes)
- Status, playbook, scorecard, performance, context, decisions, scenarios
- Query parameter handling (market, date, limit)

#### Performance & Quality

##### `tests/test_token_efficiency.py` (34 tests)
- Gemini token usage optimization
- Prompt size reduction verification
- Cache effectiveness

##### `tests/test_latency_control.py` (30 tests)
- API call latency measurement
- Rate limiter timing accuracy
- Async operation overhead

##### `tests/test_decision_logger.py` (9 tests)
- Decision audit trail completeness
- Context snapshot capture
- Outcome tracking (P&L, accuracy)

##### `tests/test_data_integration.py` (38 tests)
- External data source integration
- News API, market data, economic calendar
- Error handling for API failures

##### `tests/test_backup.py` (23 tests)
- Backup scheduler and execution
- Cloud storage (S3) upload
- Health monitoring
- Data export functionality

## Coverage Requirements

**Minimum coverage: 80%**

Check coverage:
```bash
pytest -v --cov=src --cov-report=term-missing
```

**Note:** `main.py` has lower coverage as it contains the main loop which is tested via integration/manual testing.

## Test Configuration

### `pyproject.toml`
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py"]
```

### `tests/conftest.py`
```python
@pytest.fixture
def settings() -> Settings:
    """Provide test settings with safe defaults."""
    return Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        MODE="paper",
        DB_PATH=":memory:",  # In-memory SQLite
        CONFIDENCE_THRESHOLD=80,
        ENABLED_MARKETS="KR",
    )
```

## Writing New Tests

### Naming Convention
- Test files: `test_<module>.py`
- Test functions: `test_<feature>_<scenario>()`
- Use descriptive names that explain what is being tested

### Good Test Example
```python
async def test_send_order_with_market_price(broker, settings):
    """Market orders should use price=0 and ORD_DVSN='01'."""
    # Arrange
    stock_code = "005930"
    order_type = "BUY"
    quantity = 10

    # Act
    with patch.object(broker._session, 'post') as mock_post:
        mock_post.return_value.__aenter__.return_value.status = 200
        mock_post.return_value.__aenter__.return_value.json = AsyncMock(
            return_value={"rt_cd": "0", "msg1": "OK"}
        )

        await broker.send_order(stock_code, order_type, quantity, price=0)

    # Assert
    call_args = mock_post.call_args
    body = call_args.kwargs['json']
    assert body['ORD_DVSN'] == '01'  # Market order
    assert body['ORD_UNPR'] == '0'   # Price 0
```

### Test Checklist
- [ ] Test passes in isolation (`pytest tests/test_foo.py::test_bar -v`)
- [ ] Test has clear docstring explaining what it tests
- [ ] Arrange-Act-Assert structure
- [ ] Uses appropriate fixtures from conftest.py
- [ ] Mocks external dependencies (API calls, network)
- [ ] Tests edge cases and error conditions
- [ ] Doesn't rely on test execution order

## Running Tests

```bash
# All tests
pytest -v

# Specific file
pytest tests/test_risk.py -v

# Specific test
pytest tests/test_brain.py::test_parse_valid_json -v

# With coverage
pytest -v --cov=src --cov-report=term-missing

# Stop on first failure
pytest -x

# Verbose output with print statements
pytest -v -s
```

## CI/CD Integration

Tests run automatically on:
- Every commit to feature branches
- Every PR to main
- Scheduled daily runs

**Blocking conditions:**
- Test failures → PR blocked
- Coverage < 80% → PR blocked (warning only for main.py)

**Non-blocking:**
- `mypy --strict` errors (type hints encouraged but not enforced)
- `ruff check` warnings (must be acknowledged)
