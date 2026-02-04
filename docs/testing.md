# Testing Guidelines

## Test Structure

**54 tests** across four files. `asyncio_mode = "auto"` in pyproject.toml — async tests need no special decorator.

The `settings` fixture in `conftest.py` provides safe defaults with test credentials and in-memory DB.

### Test Files

#### `tests/test_risk.py` (11 tests)
- Circuit breaker boundaries
- Fat-finger edge cases
- P&L calculation edge cases
- Order validation logic

**Example:**
```python
def test_circuit_breaker_exact_threshold(risk_manager):
    """Circuit breaker should trip at exactly -3.0%."""
    with pytest.raises(CircuitBreakerTripped):
        risk_manager.validate_order(
            current_pnl_pct=-3.0,
            order_amount=1000,
            total_cash=10000
        )
```

#### `tests/test_broker.py` (6 tests)
- OAuth token lifecycle
- Rate limiting enforcement
- Hash key generation
- Network error handling
- SSL context configuration

**Example:**
```python
async def test_rate_limiter(broker):
    """Rate limiter should delay requests to stay under 10 RPS."""
    start = time.monotonic()
    for _ in range(15):  # 15 requests
        await broker._rate_limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 1.0  # Should take at least 1 second
```

#### `tests/test_brain.py` (18 tests)
- Valid JSON parsing
- Markdown-wrapped JSON handling
- Malformed JSON fallback
- Missing fields handling
- Invalid action validation
- Confidence threshold enforcement
- Empty response handling
- Prompt construction for different markets

**Example:**
```python
async def test_confidence_below_threshold_forces_hold(brain):
    """Decisions below confidence threshold should force HOLD."""
    decision = brain.parse_response('{"action":"BUY","confidence":70,"rationale":"test"}')
    assert decision.action == "HOLD"
    assert decision.confidence == 70
```

#### `tests/test_market_schedule.py` (19 tests)
- Market open/close logic
- Timezone handling (UTC, Asia/Seoul, America/New_York, etc.)
- DST (Daylight Saving Time) transitions
- Weekend handling
- Lunch break logic
- Multiple market filtering
- Next market open calculation

**Example:**
```python
def test_is_market_open_during_trading_hours():
    """Market should be open during regular trading hours."""
    # KRX: 9:00-15:30 KST, no lunch break
    market = MARKETS["KR"]
    trading_time = datetime(2026, 2, 3, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # Monday 10:00
    assert is_market_open(market, trading_time) is True
```

## Coverage Requirements

**Minimum coverage: 80%**

Check coverage:
```bash
pytest -v --cov=src --cov-report=term-missing
```

Expected output:
```
Name                          Stmts   Miss  Cover   Missing
-----------------------------------------------------------
src/brain/gemini_client.py       85      5    94%   165-169
src/broker/kis_api.py           120     12    90%   ...
src/core/risk_manager.py         35      2    94%   ...
src/db.py                        25      1    96%   ...
src/main.py                     150     80    47%   (excluded from CI)
src/markets/schedule.py          95      3    97%   ...
-----------------------------------------------------------
TOTAL                           510     103   80%
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
