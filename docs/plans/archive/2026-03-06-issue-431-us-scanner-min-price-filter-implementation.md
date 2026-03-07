# US Scanner Min Price Filter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 해외 스캐너에서 `US_MIN_PRICE` 미만 종목을 candidates 생성 단계에서 차단하여 거래 불가 종목이 플레이북에 진입하지 않도록 한다.

**Architecture:** `SmartVolatilityScanner.__init__`에서 `settings.US_MIN_PRICE`를 저장하고, 해외 스캔 두 경로(`_scan_overseas_from_rankings`, `_scan_overseas_from_symbols`)에서 `price <= 0` 조건을 `price < self.us_min_price`로 교체한다. config의 `ge=0.0`을 `ge=1.0`으로 변경해 0 설정 자체를 막는다.

**Tech Stack:** Python, pydantic (Settings), pytest, unittest.mock

---

### Task 1: config.py — ge 제약 강화

**Files:**
- Modify: `src/config.py:63`
- Test: `tests/test_config.py` (파일이 없으면 새로 생성)

**Step 1: 실패하는 테스트 작성**

기존 config 테스트 파일 확인:
```bash
ls tests/test_config.py 2>/dev/null || echo "없음"
```

`tests/test_config.py`에 추가 (없으면 생성):
```python
import pytest
from pydantic import ValidationError
from src.config import Settings


def test_us_min_price_cannot_be_zero():
    """US_MIN_PRICE=0은 허용하지 않는다 (ge=1.0)."""
    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            US_MIN_PRICE=0.0,
        )


def test_us_min_price_cannot_be_below_one():
    """US_MIN_PRICE=0.5도 허용하지 않는다."""
    with pytest.raises(ValidationError):
        Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            US_MIN_PRICE=0.5,
        )


def test_us_min_price_default_is_five():
    """기본값은 5.0."""
    s = Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test",
    )
    assert s.US_MIN_PRICE == 5.0
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/test_config.py::test_us_min_price_cannot_be_zero -v
```

Expected: `FAILED` — `US_MIN_PRICE=0.0`이 현재 `ge=0.0`이라 통과되기 때문

**Step 3: 구현**

`src/config.py:63`:
```python
# 변경 전
US_MIN_PRICE: float = Field(default=5.0, ge=0.0)
# 변경 후
US_MIN_PRICE: float = Field(default=5.0, ge=1.0)
```

**Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 3개 모두 PASSED

**Step 5: 커밋**

```bash
git add src/config.py tests/test_config.py
git commit -m "fix: enforce US_MIN_PRICE >= 1.0 in config validation"
```

---

### Task 2: _scan_overseas_from_rankings — 가격 필터 추가

**Files:**
- Modify: `src/analysis/smart_scanner.py` (`__init__`, `_scan_overseas_from_rankings`)
- Test: `tests/test_smart_scanner.py`

**Step 1: 실패하는 테스트 작성**

`tests/test_smart_scanner.py`의 `TestSmartVolatilityScanner` 클래스에 추가:

```python
@pytest.mark.asyncio
async def test_scan_overseas_rankings_filters_penny_stocks(
    self, mock_broker: MagicMock, mock_overseas_broker: MagicMock, mock_settings: Settings
) -> None:
    """랭킹 API 결과에서 US_MIN_PRICE 미만 종목은 candidates에서 제외된다."""
    analyzer = VolatilityAnalyzer()
    scanner = SmartVolatilityScanner(
        broker=mock_broker,
        overseas_broker=mock_overseas_broker,
        volatility_analyzer=analyzer,
        settings=mock_settings,
    )
    market = MagicMock()
    market.name = "NASDAQ"
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    # IBO ($0.68), TPET ($1.35) — 둘 다 US_MIN_PRICE($5) 미만
    # NVDA ($780.2) — 정상 통과
    mock_overseas_broker.fetch_overseas_rankings.return_value = [
        {"symb": "IBO", "last": "0.68", "rate": "25.0", "tvol": "50000000"},
        {"symb": "TPET", "last": "1.35", "rate": "20.0", "tvol": "30000000"},
        {"symb": "NVDA", "last": "780.2", "rate": "5.0", "tvol": "12000000"},
    ]

    candidates = await scanner.scan(market=market)

    codes = [c.stock_code for c in candidates]
    assert "IBO" not in codes
    assert "TPET" not in codes
    assert "NVDA" in codes


@pytest.mark.asyncio
async def test_scan_overseas_rankings_allows_stocks_above_min_price(
    self, mock_broker: MagicMock, mock_overseas_broker: MagicMock, mock_settings: Settings
) -> None:
    """US_MIN_PRICE 이상 종목은 정상적으로 candidates에 포함된다."""
    analyzer = VolatilityAnalyzer()
    scanner = SmartVolatilityScanner(
        broker=mock_broker,
        overseas_broker=mock_overseas_broker,
        volatility_analyzer=analyzer,
        settings=mock_settings,
    )
    market = MagicMock()
    market.name = "NYSE"
    market.code = "US_NYSE"
    market.exchange_code = "NYSE"
    market.is_domestic = False

    mock_overseas_broker.fetch_overseas_rankings.return_value = [
        {"symb": "GOTU", "last": "6.50", "rate": "8.0", "tvol": "5000000"},
    ]

    candidates = await scanner.scan(market=market)

    assert any(c.stock_code == "GOTU" for c in candidates)
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/test_smart_scanner.py::TestSmartVolatilityScanner::test_scan_overseas_rankings_filters_penny_stocks -v
```

Expected: `FAILED` — 현재 IBO, TPET이 필터링되지 않음

**Step 3: 구현**

`src/analysis/smart_scanner.py` — `__init__` 메서드 끝에 추가:
```python
self.us_min_price = settings.US_MIN_PRICE
```

`_scan_overseas_from_rankings` 메서드의 필터 조건 (line ~283):
```python
# 변경 전
if price <= 0 or volatility_pct < 0.8:
    continue
# 변경 후
if price < self.us_min_price or volatility_pct < 0.8:
    continue
```

변경 후 바로 위에 DEBUG 로그도 추가 (continue 바로 전):
```python
if price < self.us_min_price or volatility_pct < 0.8:
    if 0 < price < self.us_min_price:
        logger.debug(
            "Overseas scanner: skipped %s (price=%.2f < US_MIN_PRICE=%.2f)",
            stock_code,
            price,
            self.us_min_price,
        )
    continue
```

**Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/test_smart_scanner.py::TestSmartVolatilityScanner::test_scan_overseas_rankings_filters_penny_stocks tests/test_smart_scanner.py::TestSmartVolatilityScanner::test_scan_overseas_rankings_allows_stocks_above_min_price -v
```

Expected: 2개 모두 PASSED

**Step 5: 기존 테스트 회귀 확인**

```bash
uv run pytest tests/test_smart_scanner.py -v
```

Expected: 전체 PASSED

**Step 6: 커밋**

```bash
git add src/analysis/smart_scanner.py tests/test_smart_scanner.py
git commit -m "fix: filter overseas ranking candidates below US_MIN_PRICE"
```

---

### Task 3: _scan_overseas_from_symbols — 가격 필터 추가

**Files:**
- Modify: `src/analysis/smart_scanner.py` (`_scan_overseas_from_symbols`)
- Test: `tests/test_smart_scanner.py`

**Step 1: 실패하는 테스트 작성**

`TestSmartVolatilityScanner` 클래스에 추가:

```python
@pytest.mark.asyncio
async def test_scan_overseas_symbols_filters_penny_stocks(
    self, mock_broker: MagicMock, mock_overseas_broker: MagicMock, mock_settings: Settings
) -> None:
    """fallback symbols 경로에서도 US_MIN_PRICE 미만 종목은 제외된다."""
    analyzer = VolatilityAnalyzer()
    scanner = SmartVolatilityScanner(
        broker=mock_broker,
        overseas_broker=mock_overseas_broker,
        volatility_analyzer=analyzer,
        settings=mock_settings,
    )
    market = MagicMock()
    market.name = "NASDAQ"
    market.code = "US_NASDAQ"
    market.exchange_code = "NASD"
    market.is_domestic = False

    # 랭킹 API 비활성화 → fallback 경로 사용
    mock_overseas_broker.fetch_overseas_rankings.return_value = []

    # IBO($0.68, 변동성 25%) — US_MIN_PRICE 미만으로 제외
    # NVDA($780.2, 변동성 5%) — 정상 포함
    mock_overseas_broker.get_overseas_price.side_effect = [
        {"output": {"last": "0.68", "rate": "25.0", "tvol": "50000000"}},
        {"output": {"last": "780.2", "rate": "5.0", "tvol": "12000000"}},
    ]

    candidates = await scanner.scan(market=market, fallback_stocks=["IBO", "NVDA"])

    codes = [c.stock_code for c in candidates]
    assert "IBO" not in codes
    assert "NVDA" in codes
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
uv run pytest tests/test_smart_scanner.py::TestSmartVolatilityScanner::test_scan_overseas_symbols_filters_penny_stocks -v
```

Expected: `FAILED`

**Step 3: 구현**

`_scan_overseas_from_symbols` 메서드의 필터 조건 (line ~341):
```python
# 변경 전
if price <= 0 or volatility_pct < 0.8:
    continue
# 변경 후
if price < self.us_min_price or volatility_pct < 0.8:
    if 0 < price < self.us_min_price:
        logger.debug(
            "Overseas scanner: skipped %s (price=%.2f < US_MIN_PRICE=%.2f)",
            stock_code,
            price,
            self.us_min_price,
        )
    continue
```

**Step 4: 테스트 실행 — 통과 확인**

```bash
uv run pytest tests/test_smart_scanner.py -v
```

Expected: 전체 PASSED

**Step 5: 커밋**

```bash
git add src/analysis/smart_scanner.py tests/test_smart_scanner.py
git commit -m "fix: filter overseas fallback-symbol candidates below US_MIN_PRICE"
```

---

### Task 4: 전체 검증 및 PR 생성

**Step 1: 전체 테스트 실행**

```bash
uv run pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: 전체 PASSED, 실패 없음

**Step 2: 커버리지 확인**

```bash
uv run pytest tests/test_smart_scanner.py tests/test_config.py --cov=src/analysis/smart_scanner --cov=src/config --cov-report=term-missing
```

Expected: 80% 이상

**Step 3: lint 확인**

```bash
uv run ruff check src/analysis/smart_scanner.py src/config.py tests/test_smart_scanner.py tests/test_config.py
```

Expected: 오류 없음

**Step 4: PR 생성**

```bash
YES="" ~/bin/tea pulls create \
  --head feature/issue-431-us-scanner-min-price-filter \
  --base main \
  --title "fix: filter US penny stocks from scanner candidates (#431)" \
  --description "## 문제
해외 스캐너가 US_MIN_PRICE(\$5.00)를 무시하고 페니스탁을 candidates에 포함시켜 플레이북이 생성됨. 실행 시점에서야 BUY가 차단되어 하루 종일 거래가 없음.

## 해결
- \`src/config.py\`: \`US_MIN_PRICE\` ge를 \`0.0\` → \`1.0\`으로 변경 (0 설정 불가)
- \`src/analysis/smart_scanner.py\`: \`__init__\`에 \`self.us_min_price\` 저장, \`_scan_overseas_from_rankings\` 및 \`_scan_overseas_from_symbols\` 양쪽에서 \`price <= 0\` 조건을 \`price < self.us_min_price\`로 교체

## 효과
IBO(\$0.68), TPET(\$1.35) 같은 페니스탁이 스캔 단계에서 차단되어 플레이북에 진입하지 않음.

Closes #431"
```
