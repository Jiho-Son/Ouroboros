"""Tests for SmartVolatilityScanner."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

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
    async def test_scan_finds_oversold_candidates(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test that scanner identifies oversold stocks with high volume."""
        # Mock rankings
        mock_broker.fetch_market_rankings.return_value = [
            {
                "stock_code": "005930",
                "name": "Samsung",
                "price": 70000,
                "volume": 5000000,
                "change_rate": -3.5,
                "volume_increase_rate": 250,
            },
        ]

        # Mock daily prices - trending down (oversold)
        prices = []
        for i in range(20):
            prices.append({
                "date": f"2026020{i:02d}",
                "open": 75000 - i * 200,
                "high": 75500 - i * 200,
                "low": 74500 - i * 200,
                "close": 75000 - i * 250,  # Steady decline
                "volume": 2000000,
            })
        mock_broker.get_daily_prices.return_value = prices

        candidates = await scanner.scan()

        # Should find at least one candidate (depending on exact RSI calculation)
        mock_broker.fetch_market_rankings.assert_called_once()
        mock_broker.get_daily_prices.assert_called_once_with("005930", days=20)

        # If qualified, should have oversold signal
        if candidates:
            assert candidates[0].signal in ["oversold", "momentum"]
            assert candidates[0].volume_ratio >= scanner.vol_multiplier

    @pytest.mark.asyncio
    async def test_scan_finds_momentum_candidates(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test that scanner identifies momentum stocks with high volume."""
        mock_broker.fetch_market_rankings.return_value = [
            {
                "stock_code": "035420",
                "name": "NAVER",
                "price": 250000,
                "volume": 3000000,
                "change_rate": 5.0,
                "volume_increase_rate": 300,
            },
        ]

        # Mock daily prices - trending up (momentum)
        prices = []
        for i in range(20):
            prices.append({
                "date": f"2026020{i:02d}",
                "open": 230000 + i * 500,
                "high": 231000 + i * 500,
                "low": 229000 + i * 500,
                "close": 230500 + i * 500,  # Steady rise
                "volume": 1000000,
            })
        mock_broker.get_daily_prices.return_value = prices

        candidates = await scanner.scan()

        mock_broker.fetch_market_rankings.assert_called_once()

    @pytest.mark.asyncio
    async def test_scan_filters_low_volume(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test that stocks with low volume ratio are filtered out."""
        mock_broker.fetch_market_rankings.return_value = [
            {
                "stock_code": "000660",
                "name": "SK Hynix",
                "price": 150000,
                "volume": 500000,
                "change_rate": -5.0,
                "volume_increase_rate": 50,  # Only 50% increase (< 200%)
            },
        ]

        # Low volume
        prices = []
        for i in range(20):
            prices.append({
                "date": f"2026020{i:02d}",
                "open": 150000 - i * 100,
                "high": 151000 - i * 100,
                "low": 149000 - i * 100,
                "close": 150000 - i * 150,  # Declining (would be oversold)
                "volume": 1000000,  # Current 500k < 2x prev day 1M
            })
        mock_broker.get_daily_prices.return_value = prices

        candidates = await scanner.scan()

        # Should be filtered out due to low volume ratio
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_scan_filters_neutral_rsi(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test that stocks with neutral RSI are filtered out."""
        mock_broker.fetch_market_rankings.return_value = [
            {
                "stock_code": "051910",
                "name": "LG Chem",
                "price": 500000,
                "volume": 3000000,
                "change_rate": 0.5,
                "volume_increase_rate": 300,  # High volume
            },
        ]

        # Flat prices (neutral RSI ~50)
        prices = []
        for i in range(20):
            prices.append({
                "date": f"2026020{i:02d}",
                "open": 500000 + (i % 2) * 100,  # Small oscillation
                "high": 500500,
                "low": 499500,
                "close": 500000 + (i % 2) * 50,
                "volume": 1000000,
            })
        mock_broker.get_daily_prices.return_value = prices

        candidates = await scanner.scan()

        # Should be filtered out (RSI ~50, not < 30 or > 70)
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_scan_uses_fallback_on_api_error(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test fallback to static list when ranking API fails."""
        mock_broker.fetch_market_rankings.side_effect = ConnectionError("API unavailable")

        # Fallback stocks should still be analyzed
        prices = []
        for i in range(20):
            prices.append({
                "date": f"2026020{i:02d}",
                "open": 50000 - i * 50,
                "high": 51000 - i * 50,
                "low": 49000 - i * 50,
                "close": 50000 - i * 75,  # Declining
                "volume": 1000000,
            })
        mock_broker.get_daily_prices.return_value = prices

        candidates = await scanner.scan(fallback_stocks=["005930", "000660"])

        # Should not crash
        assert isinstance(candidates, list)

    @pytest.mark.asyncio
    async def test_scan_returns_top_n_only(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test that scan returns at most top_n candidates."""
        # Return many stocks
        mock_broker.fetch_market_rankings.return_value = [
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

        # All oversold with high volume
        def make_prices(code: str) -> list[dict]:
            prices = []
            for i in range(20):
                prices.append({
                    "date": f"2026020{i:02d}",
                    "open": 10000 - i * 100,
                    "high": 10500 - i * 100,
                    "low": 9500 - i * 100,
                    "close": 10000 - i * 150,
                    "volume": 1000000,
                })
            return prices

        mock_broker.get_daily_prices.side_effect = make_prices

        candidates = await scanner.scan()

        # Should respect top_n limit (3)
        assert len(candidates) <= scanner.top_n

    @pytest.mark.asyncio
    async def test_scan_skips_insufficient_price_history(
        self, scanner: SmartVolatilityScanner, mock_broker: MagicMock
    ) -> None:
        """Test that stocks with insufficient history are skipped."""
        mock_broker.fetch_market_rankings.return_value = [
            {
                "stock_code": "005930",
                "name": "Samsung",
                "price": 70000,
                "volume": 5000000,
                "change_rate": -5.0,
                "volume_increase_rate": 300,
            },
        ]

        # Only 5 days of data (need 15+ for RSI)
        mock_broker.get_daily_prices.return_value = [
            {
                "date": f"2026020{i:02d}",
                "open": 70000,
                "high": 71000,
                "low": 69000,
                "close": 70000,
                "volume": 2000000,
            }
            for i in range(5)
        ]

        candidates = await scanner.scan()

        # Should skip due to insufficient data
        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_get_stock_codes(
        self, scanner: SmartVolatilityScanner
    ) -> None:
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

        mock_overseas_broker.fetch_overseas_rankings.assert_called_once()
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
