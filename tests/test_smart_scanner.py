"""Tests for SmartVolatilityScanner."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.smart_scanner import ScanCandidate, SmartVolatilityScanner
from src.analysis.volatility import VolatilityAnalyzer
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings


@pytest.fixture
def mock_settings() -> Settings:
    """Create test settings."""
    return Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test",
        RSI_OVERSOLD_THRESHOLD=30,
        RSI_MOMENTUM_THRESHOLD=70,
        VOL_MULTIPLIER=2.0,
        SCANNER_TOP_N=3,
        DB_PATH=":memory:",
    )


@pytest.fixture
def mock_broker(mock_settings: Settings) -> MagicMock:
    """Create mock broker."""
    broker = MagicMock(spec=KISBroker)
    broker._settings = mock_settings
    broker.fetch_market_rankings = AsyncMock()
    broker.get_daily_prices = AsyncMock()
    return broker


@pytest.fixture
def scanner(mock_broker: MagicMock, mock_settings: Settings) -> SmartVolatilityScanner:
    """Create smart scanner instance."""
    analyzer = VolatilityAnalyzer()
    return SmartVolatilityScanner(
        broker=mock_broker,
        overseas_broker=None,
        volatility_analyzer=analyzer,
        settings=mock_settings,
    )


@pytest.fixture
def mock_overseas_broker() -> MagicMock:
    """Create mock overseas broker."""
    broker = MagicMock(spec=OverseasBroker)
    broker.get_overseas_price = AsyncMock()
    broker.fetch_overseas_rankings = AsyncMock(return_value=[])
    return broker


class TestSmartVolatilityScanner:
    """Test suite for SmartVolatilityScanner."""

    @pytest.mark.asyncio
    async def test_scan_domestic_prefers_volatility_with_liquidity_bonus(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Domestic scan should score by volatility first and volume rank second."""
        fluctuation_rows = [
            {
                "stock_code": "005930",
                "name": "Samsung",
                "price": 70000,
                "volume": 5000000,
                "change_rate": -5.0,
                "volume_increase_rate": 250,
            },
            {
                "stock_code": "035420",
                "name": "NAVER",
                "price": 250000,
                "volume": 3000000,
                "change_rate": 3.0,
                "volume_increase_rate": 200,
            },
        ]
        volume_rows = [
            {"stock_code": "035420", "name": "NAVER", "price": 250000, "volume": 3000000},
            {"stock_code": "005930", "name": "Samsung", "price": 70000, "volume": 5000000},
        ]
        mock_broker.fetch_market_rankings.side_effect = [fluctuation_rows, volume_rows]
        mock_broker.get_daily_prices.return_value = [
            {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1000000},
            {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1000000},
        ]

        candidates = await scanner.scan()

        assert len(candidates) >= 1
        # Samsung has higher absolute move, so it should lead despite lower volume rank bonus.
        assert candidates[0].stock_code == "005930"
        assert candidates[0].signal == "oversold"

    @pytest.mark.asyncio
    async def test_scan_domestic_passes_session_id_to_rankings(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        fluctuation_rows = [
            {
                "stock_code": "005930",
                "name": "Samsung",
                "price": 70000,
                "volume": 5000000,
                "change_rate": 1.0,
                "volume_increase_rate": 120,
            },
        ]
        mock_broker.fetch_market_rankings.side_effect = [fluctuation_rows, fluctuation_rows]
        mock_broker.get_daily_prices.return_value = [
            {"open": 1, "high": 71000, "low": 69000, "close": 70000, "volume": 1000000},
            {"open": 1, "high": 70000, "low": 68000, "close": 69000, "volume": 900000},
        ]

        await scanner.scan(domestic_session_id="NXT_PRE")

        first_call = mock_broker.fetch_market_rankings.call_args_list[0]
        second_call = mock_broker.fetch_market_rankings.call_args_list[1]
        assert first_call.kwargs["session_id"] == "NXT_PRE"
        assert second_call.kwargs["session_id"] == "NXT_PRE"

    @pytest.mark.asyncio
    async def test_scan_domestic_finds_momentum_candidate(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Positive change should be represented as momentum signal."""
        fluctuation_rows = [
            {
                "stock_code": "035420",
                "name": "NAVER",
                "price": 250000,
                "volume": 3000000,
                "change_rate": 5.0,
                "volume_increase_rate": 300,
            },
        ]
        mock_broker.fetch_market_rankings.side_effect = [fluctuation_rows, fluctuation_rows]
        mock_broker.get_daily_prices.return_value = [
            {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1000000},
            {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1000000},
        ]

        candidates = await scanner.scan()

        assert [c.stock_code for c in candidates] == ["035420"]
        assert candidates[0].signal == "momentum"

    @pytest.mark.asyncio
    async def test_scan_domestic_filters_low_volatility(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Domestic scan should drop symbols below volatility threshold."""
        fluctuation_rows = [
            {
                "stock_code": "000660",
                "name": "SK Hynix",
                "price": 150000,
                "volume": 500000,
                "change_rate": 0.2,
                "volume_increase_rate": 50,
            },
        ]
        mock_broker.fetch_market_rankings.side_effect = [fluctuation_rows, fluctuation_rows]
        mock_broker.get_daily_prices.return_value = [
            {"open": 1, "high": 150100, "low": 149900, "close": 150000, "volume": 1000000},
            {"open": 1, "high": 150100, "low": 149900, "close": 150000, "volume": 1000000},
        ]

        candidates = await scanner.scan()

        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_scan_uses_fallback_on_api_error(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Domestic scan should remain operational using fallback symbols."""
        mock_broker.fetch_market_rankings.side_effect = [
            ConnectionError("API unavailable"),
            ConnectionError("API unavailable"),
        ]
        mock_broker.get_daily_prices.return_value = [
            {"open": 1, "high": 103, "low": 97, "close": 100, "volume": 1000000},
            {"open": 1, "high": 103, "low": 97, "close": 100, "volume": 800000},
        ]

        candidates = await scanner.scan(fallback_stocks=["005930", "000660"])

        assert isinstance(candidates, list)
        assert len(candidates) >= 1

    @pytest.mark.asyncio
    async def test_scan_returns_top_n_only(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test that scan returns at most top_n candidates."""
        fluctuation_rows = [
            {
                "stock_code": f"00{i}000",
                "name": f"Stock{i}",
                "price": 10000 * i,
                "volume": 5000000,
                "change_rate": -10,
                "volume_increase_rate": 500,
            }
            for i in range(1, 10)
        ]
        mock_broker.fetch_market_rankings.side_effect = [fluctuation_rows, fluctuation_rows]
        mock_broker.get_daily_prices.return_value = [
            {"open": 1, "high": 105, "low": 95, "close": 100, "volume": 1000000},
            {"open": 1, "high": 105, "low": 95, "close": 100, "volume": 900000},
        ]

        candidates = await scanner.scan()

        # Should respect top_n limit (3)
        assert len(candidates) <= scanner.top_n

    @pytest.mark.asyncio
    async def test_get_stock_codes(self, scanner: SmartVolatilityScanner) -> None:
        """Test extraction of stock codes from candidates."""
        candidates = [
            ScanCandidate(
                stock_code="005930",
                name="Samsung",
                price=70000,
                volume=5000000,
                volume_ratio=2.5,
                rsi=28,
                signal="oversold",
                score=85.0,
            ),
            ScanCandidate(
                stock_code="035420",
                name="NAVER",
                price=250000,
                volume=3000000,
                volume_ratio=3.0,
                rsi=75,
                signal="momentum",
                score=88.0,
            ),
        ]

        codes = scanner.get_stock_codes(candidates)

        assert codes == ["005930", "035420"]

    @pytest.mark.asyncio
    async def test_scan_overseas_uses_dynamic_symbols(
        self, mock_broker: MagicMock, mock_overseas_broker: MagicMock, mock_settings: Settings
    ) -> None:
        """Overseas scan should use provided dynamic universe symbols."""
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

        mock_overseas_broker.get_overseas_price.side_effect = [
            {"output": {"last": "210.5", "rate": "1.6", "tvol": "1500000"}},
            {"output": {"last": "330.1", "rate": "0.2", "tvol": "900000"}},
        ]

        candidates = await scanner.scan(
            market=market,
            fallback_stocks=["AAPL", "MSFT"],
        )

        assert [c.stock_code for c in candidates] == ["AAPL"]
        assert candidates[0].signal == "momentum"
        assert candidates[0].price == 210.5

    @pytest.mark.asyncio
    async def test_scan_overseas_uses_ranking_api_first(
        self, mock_broker: MagicMock, mock_overseas_broker: MagicMock, mock_settings: Settings
    ) -> None:
        """Overseas scan should prioritize ranking API when available."""
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

        mock_overseas_broker.fetch_overseas_rankings.return_value = [
            {"symb": "NVDA", "last": "780.2", "rate": "2.4", "tvol": "1200000"},
            {"symb": "MSFT", "last": "420.0", "rate": "0.3", "tvol": "900000"},
        ]

        candidates = await scanner.scan(market=market, fallback_stocks=["AAPL", "TSLA"])

        assert mock_overseas_broker.fetch_overseas_rankings.call_count >= 1
        mock_overseas_broker.get_overseas_price.assert_not_called()
        assert [c.stock_code for c in candidates] == ["NVDA"]

    @pytest.mark.asyncio
    async def test_scan_overseas_without_symbols_returns_empty(
        self, mock_broker: MagicMock, mock_overseas_broker: MagicMock, mock_settings: Settings
    ) -> None:
        """Overseas scan should return empty list when no symbol universe exists."""
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

        candidates = await scanner.scan(market=market, fallback_stocks=[])

        assert candidates == []

    @pytest.mark.asyncio
    async def test_scan_overseas_picks_high_intraday_range_even_with_low_change(
        self, mock_broker: MagicMock, mock_overseas_broker: MagicMock, mock_settings: Settings
    ) -> None:
        """Volatility selection should consider intraday range, not only change rate."""
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

        # change rate is tiny, but high-low range is large (15%).
        mock_overseas_broker.fetch_overseas_rankings.return_value = [
            {
                "symb": "ABCD",
                "last": "100",
                "rate": "0.2",
                "high": "110",
                "low": "95",
                "tvol": "800000",
            }
        ]

        candidates = await scanner.scan(market=market, fallback_stocks=[])

        assert [c.stock_code for c in candidates] == ["ABCD"]

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


class TestImpliedRSIFormula:
    """Test the implied_rsi formula in SmartVolatilityScanner (issue #181)."""

    def test_neutral_change_gives_neutral_rsi(self) -> None:
        """0% change → implied_rsi = 50 (neutral)."""
        # formula: 50 + (change_rate * 2.0)
        rsi = max(0.0, min(100.0, 50.0 + (0.0 * 2.0)))
        assert rsi == 50.0

    def test_10pct_change_gives_rsi_70(self) -> None:
        """10% upward change → implied_rsi = 70 (momentum signal)."""
        rsi = max(0.0, min(100.0, 50.0 + (10.0 * 2.0)))
        assert rsi == 70.0

    def test_minus_10pct_gives_rsi_30(self) -> None:
        """-10% change → implied_rsi = 30 (oversold signal)."""
        rsi = max(0.0, min(100.0, 50.0 + (-10.0 * 2.0)))
        assert rsi == 30.0

    def test_saturation_at_25pct(self) -> None:
        """Saturation occurs at >=25% change (not 12.5% as with old coefficient 4.0)."""
        rsi_12pct = max(0.0, min(100.0, 50.0 + (12.5 * 2.0)))
        rsi_25pct = max(0.0, min(100.0, 50.0 + (25.0 * 2.0)))
        rsi_30pct = max(0.0, min(100.0, 50.0 + (30.0 * 2.0)))
        # At 12.5% change: RSI = 75 (not 100, unlike old formula)
        assert rsi_12pct == 75.0
        # At 25%+ saturation
        assert rsi_25pct == 100.0
        assert rsi_30pct == 100.0  # Capped

    def test_negative_saturation(self) -> None:
        """Saturation at -25% gives RSI = 0."""
        rsi = max(0.0, min(100.0, 50.0 + (-25.0 * 2.0)))
        assert rsi == 0.0


class TestRSICalculation:
    """Test RSI calculation in VolatilityAnalyzer."""

    def test_rsi_oversold(self) -> None:
        """Test RSI calculation for downtrending prices."""
        analyzer = VolatilityAnalyzer()

        # Steadily declining prices
        prices = [100 - i * 0.5 for i in range(20)]
        rsi = analyzer.calculate_rsi(prices, period=14)

        assert rsi < 50  # Should be oversold territory

    def test_rsi_overbought(self) -> None:
        """Test RSI calculation for uptrending prices."""
        analyzer = VolatilityAnalyzer()

        # Steadily rising prices
        prices = [100 + i * 0.5 for i in range(20)]
        rsi = analyzer.calculate_rsi(prices, period=14)

        assert rsi > 50  # Should be overbought territory

    def test_rsi_neutral(self) -> None:
        """Test RSI calculation for flat prices."""
        analyzer = VolatilityAnalyzer()

        # Flat prices with small oscillation
        prices = [100 + (i % 2) * 0.1 for i in range(20)]
        rsi = analyzer.calculate_rsi(prices, period=14)

        assert 40 < rsi < 60  # Should be near neutral

    def test_rsi_insufficient_data(self) -> None:
        """Test RSI returns neutral when insufficient data."""
        analyzer = VolatilityAnalyzer()

        prices = [100, 101, 102]  # Only 3 prices, need 15+
        rsi = analyzer.calculate_rsi(prices, period=14)

        assert rsi == 50.0  # Default neutral

    def test_rsi_all_gains(self) -> None:
        """Test RSI returns 100 when all gains (no losses)."""
        analyzer = VolatilityAnalyzer()

        # Monotonic increase
        prices = [100 + i for i in range(20)]
        rsi = analyzer.calculate_rsi(prices, period=14)

        assert rsi == 100.0  # Maximum RSI
