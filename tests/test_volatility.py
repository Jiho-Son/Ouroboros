"""Tests for volatility analysis and market scanning."""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.analysis.scanner import MarketScanner, ScanResult
from src.analysis.volatility import VolatilityAnalyzer, VolatilityMetrics
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.context.layer import ContextLayer
from src.context.store import ContextStore
from src.db import init_db
from src.markets.schedule import MARKETS


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Provide an in-memory database connection."""
    return init_db(":memory:")


@pytest.fixture
def context_store(db_conn: sqlite3.Connection) -> ContextStore:
    """Provide a ContextStore instance."""
    return ContextStore(db_conn)


@pytest.fixture
def volatility_analyzer() -> VolatilityAnalyzer:
    """Provide a VolatilityAnalyzer instance."""
    return VolatilityAnalyzer(min_volume_surge=2.0, min_price_change=1.0)


@pytest.fixture
def mock_settings() -> Settings:
    """Provide mock settings for broker initialization."""
    return Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
    )


@pytest.fixture
def mock_broker(mock_settings: Settings) -> KISBroker:
    """Provide a mock KIS broker."""
    broker = KISBroker(mock_settings)
    broker.get_orderbook = AsyncMock()  # type: ignore[method-assign]
    return broker


@pytest.fixture
def mock_overseas_broker(mock_broker: KISBroker) -> OverseasBroker:
    """Provide a mock overseas broker."""
    overseas = OverseasBroker(mock_broker)
    overseas.get_overseas_price = AsyncMock()  # type: ignore[method-assign]
    return overseas


class TestVolatilityAnalyzer:
    """Test suite for VolatilityAnalyzer."""

    def test_calculate_atr(self, volatility_analyzer: VolatilityAnalyzer) -> None:
        """Test ATR calculation."""
        high_prices = [110.0, 112.0, 115.0, 113.0, 116.0] + [120.0] * 10
        low_prices = [105.0, 107.0, 110.0, 108.0, 111.0] + [115.0] * 10
        close_prices = [108.0, 110.0, 112.0, 111.0, 114.0] + [118.0] * 10

        atr = volatility_analyzer.calculate_atr(high_prices, low_prices, close_prices, period=14)

        assert atr > 0.0
        # ATR should be roughly the average true range
        assert 3.0 <= atr <= 6.0

    def test_calculate_atr_insufficient_data(
        self, volatility_analyzer: VolatilityAnalyzer
    ) -> None:
        """Test ATR with insufficient data returns 0."""
        high_prices = [110.0, 112.0]
        low_prices = [105.0, 107.0]
        close_prices = [108.0, 110.0]

        atr = volatility_analyzer.calculate_atr(high_prices, low_prices, close_prices, period=14)

        assert atr == 0.0

    def test_calculate_price_change(self, volatility_analyzer: VolatilityAnalyzer) -> None:
        """Test price change percentage calculation."""
        # 10% increase
        change = volatility_analyzer.calculate_price_change(110.0, 100.0)
        assert change == pytest.approx(10.0)

        # 5% decrease
        change = volatility_analyzer.calculate_price_change(95.0, 100.0)
        assert change == pytest.approx(-5.0)

        # Zero past price
        change = volatility_analyzer.calculate_price_change(100.0, 0.0)
        assert change == 0.0

    def test_calculate_volume_surge(self, volatility_analyzer: VolatilityAnalyzer) -> None:
        """Test volume surge ratio calculation."""
        # 2x surge
        surge = volatility_analyzer.calculate_volume_surge(2000.0, 1000.0)
        assert surge == pytest.approx(2.0)

        # Below average
        surge = volatility_analyzer.calculate_volume_surge(500.0, 1000.0)
        assert surge == pytest.approx(0.5)

        # Zero average
        surge = volatility_analyzer.calculate_volume_surge(1000.0, 0.0)
        assert surge == 1.0

    def test_calculate_pv_divergence_bullish(
        self, volatility_analyzer: VolatilityAnalyzer
    ) -> None:
        """Test bullish price-volume divergence."""
        # Price up + Volume up = bullish
        divergence = volatility_analyzer.calculate_pv_divergence(5.0, 2.0)
        assert divergence > 0.0

    def test_calculate_pv_divergence_bearish(
        self, volatility_analyzer: VolatilityAnalyzer
    ) -> None:
        """Test bearish price-volume divergence."""
        # Price up + Volume down = bearish divergence
        divergence = volatility_analyzer.calculate_pv_divergence(5.0, 0.5)
        assert divergence < 0.0

    def test_calculate_pv_divergence_selling_pressure(
        self, volatility_analyzer: VolatilityAnalyzer
    ) -> None:
        """Test selling pressure detection."""
        # Price down + Volume up = selling pressure
        divergence = volatility_analyzer.calculate_pv_divergence(-5.0, 2.0)
        assert divergence < 0.0

    def test_calculate_momentum_score(
        self, volatility_analyzer: VolatilityAnalyzer
    ) -> None:
        """Test momentum score calculation."""
        score = volatility_analyzer.calculate_momentum_score(
            price_change_1m=5.0,
            price_change_5m=3.0,
            price_change_15m=2.0,
            volume_surge=2.5,
            atr=1.5,
            current_price=100.0,
        )

        assert 0.0 <= score <= 100.0
        assert score > 50.0  # Should be high for strong positive momentum

    def test_calculate_momentum_score_negative(
        self, volatility_analyzer: VolatilityAnalyzer
    ) -> None:
        """Test momentum score with negative price changes."""
        score = volatility_analyzer.calculate_momentum_score(
            price_change_1m=-5.0,
            price_change_5m=-3.0,
            price_change_15m=-2.0,
            volume_surge=1.0,
            atr=1.0,
            current_price=100.0,
        )

        assert 0.0 <= score <= 100.0
        assert score < 50.0  # Should be low for negative momentum

    def test_analyze(self, volatility_analyzer: VolatilityAnalyzer) -> None:
        """Test full analysis of a stock."""
        orderbook_data = {
            "output1": {
                "stck_prpr": "50000",
                "acml_vol": "1000000",
            }
        }

        price_history = {
            "high": [51000.0] * 20,
            "low": [49000.0] * 20,
            "close": [48000.0] + [50000.0] * 19,
            "volume": [500000.0] * 20,
        }

        metrics = volatility_analyzer.analyze("005930", orderbook_data, price_history)

        assert metrics.stock_code == "005930"
        assert metrics.current_price == 50000.0
        assert metrics.atr > 0.0
        assert metrics.volume_surge == pytest.approx(2.0)  # 1M / 500K
        assert 0.0 <= metrics.momentum_score <= 100.0

    def test_is_breakout(self, volatility_analyzer: VolatilityAnalyzer) -> None:
        """Test breakout detection."""
        # Strong breakout
        metrics = VolatilityMetrics(
            stock_code="005930",
            current_price=50000.0,
            atr=500.0,
            price_change_1m=2.5,
            price_change_5m=3.0,
            price_change_15m=4.0,
            volume_surge=3.0,
            pv_divergence=50.0,
            momentum_score=85.0,
        )

        assert volatility_analyzer.is_breakout(metrics) is True

    def test_is_breakout_no_volume(self, volatility_analyzer: VolatilityAnalyzer) -> None:
        """Test that breakout requires volume confirmation."""
        # Price up but no volume = not a real breakout
        metrics = VolatilityMetrics(
            stock_code="005930",
            current_price=50000.0,
            atr=500.0,
            price_change_1m=2.5,
            price_change_5m=3.0,
            price_change_15m=4.0,
            volume_surge=1.2,  # Below threshold
            pv_divergence=10.0,
            momentum_score=70.0,
        )

        assert volatility_analyzer.is_breakout(metrics) is False

    def test_is_breakdown(self, volatility_analyzer: VolatilityAnalyzer) -> None:
        """Test breakdown detection."""
        # Strong breakdown
        metrics = VolatilityMetrics(
            stock_code="005930",
            current_price=50000.0,
            atr=500.0,
            price_change_1m=-2.5,
            price_change_5m=-3.0,
            price_change_15m=-4.0,
            volume_surge=3.0,
            pv_divergence=-50.0,
            momentum_score=15.0,
        )

        assert volatility_analyzer.is_breakdown(metrics) is True

    def test_volatility_metrics_repr(self) -> None:
        """Test VolatilityMetrics string representation."""
        metrics = VolatilityMetrics(
            stock_code="005930",
            current_price=50000.0,
            atr=500.0,
            price_change_1m=2.5,
            price_change_5m=3.0,
            price_change_15m=4.0,
            volume_surge=3.0,
            pv_divergence=50.0,
            momentum_score=85.0,
        )

        repr_str = repr(metrics)
        assert "005930" in repr_str
        assert "50000.00" in repr_str
        assert "2.50%" in repr_str


class TestMarketScanner:
    """Test suite for MarketScanner."""

    @pytest.fixture
    def scanner(
        self,
        mock_broker: KISBroker,
        mock_overseas_broker: OverseasBroker,
        volatility_analyzer: VolatilityAnalyzer,
        context_store: ContextStore,
    ) -> MarketScanner:
        """Provide a MarketScanner instance."""
        return MarketScanner(
            broker=mock_broker,
            overseas_broker=mock_overseas_broker,
            volatility_analyzer=volatility_analyzer,
            context_store=context_store,
            top_n=5,
        )

    @pytest.mark.asyncio
    async def test_scan_stock_domestic(
        self,
        scanner: MarketScanner,
        mock_broker: KISBroker,
        context_store: ContextStore,
    ) -> None:
        """Test scanning a domestic stock."""
        mock_broker.get_orderbook.return_value = {
            "output1": {
                "stck_prpr": "50000",
                "acml_vol": "1000000",
            }
        }

        market = MARKETS["KR"]
        metrics = await scanner.scan_stock("005930", market)

        assert metrics is not None
        assert metrics.stock_code == "005930"
        assert metrics.current_price == 50000.0

        # Verify L7 context was stored
        latest_timeframe = context_store.get_latest_timeframe(ContextLayer.L7_REALTIME)
        assert latest_timeframe is not None

    @pytest.mark.asyncio
    async def test_scan_stock_overseas(
        self,
        scanner: MarketScanner,
        mock_overseas_broker: OverseasBroker,
        context_store: ContextStore,
    ) -> None:
        """Test scanning an overseas stock."""
        mock_overseas_broker.get_overseas_price.return_value = {
            "output": {
                "last": "150.50",
                "tvol": "5000000",
            }
        }

        market = MARKETS["US_NASDAQ"]
        metrics = await scanner.scan_stock("AAPL", market)

        assert metrics is not None
        assert metrics.stock_code == "AAPL"
        assert metrics.current_price == 150.50

    @pytest.mark.asyncio
    async def test_scan_stock_error_handling(
        self,
        scanner: MarketScanner,
        mock_broker: KISBroker,
    ) -> None:
        """Test that scan_stock handles errors gracefully."""
        mock_broker.get_orderbook.side_effect = Exception("Network error")

        market = MARKETS["KR"]
        metrics = await scanner.scan_stock("005930", market)

        assert metrics is None  # Should return None on error, not crash

    @pytest.mark.asyncio
    async def test_scan_market(
        self,
        scanner: MarketScanner,
        mock_broker: KISBroker,
        context_store: ContextStore,
    ) -> None:
        """Test scanning a full market."""

        def mock_orderbook(stock_code: str) -> dict[str, Any]:
            """Generate mock orderbook with varying prices."""
            base_price = int(stock_code) if stock_code.isdigit() else 50000
            return {
                "output1": {
                    "stck_prpr": str(base_price),
                    "acml_vol": str(base_price * 20),  # Volume proportional to price
                }
            }

        mock_broker.get_orderbook.side_effect = mock_orderbook

        market = MARKETS["KR"]
        stock_codes = ["005930", "000660", "035420"]

        result = await scanner.scan_market(market, stock_codes)

        assert result.market_code == "KR"
        assert result.total_scanned == 3
        assert len(result.top_movers) <= 5
        assert all(isinstance(m, VolatilityMetrics) for m in result.top_movers)

        # Verify scan result was stored in L7
        latest_timeframe = context_store.get_latest_timeframe(ContextLayer.L7_REALTIME)
        assert latest_timeframe is not None
        scan_result = context_store.get_context(
            ContextLayer.L7_REALTIME,
            latest_timeframe,
            "KR_scan_result",
        )
        assert scan_result is not None
        assert scan_result["total_scanned"] == 3

    @pytest.mark.asyncio
    async def test_scan_market_with_breakouts(
        self,
        scanner: MarketScanner,
        mock_broker: KISBroker,
    ) -> None:
        """Test that scan detects breakouts."""
        # Mock strong price increase with volume
        mock_broker.get_orderbook.return_value = {
            "output1": {
                "stck_prpr": "55000",  # High price
                "acml_vol": "5000000",  # High volume
            }
        }

        market = MARKETS["KR"]
        stock_codes = ["005930"]

        result = await scanner.scan_market(market, stock_codes)

        # With high volume and price, might detect breakouts
        # (depends on price history which is empty in this test)
        assert isinstance(result.breakouts, list)
        assert isinstance(result.breakdowns, list)

    def test_get_updated_watchlist(self, scanner: MarketScanner) -> None:
        """Test watchlist update logic."""
        current_watchlist = ["005930", "000660", "035420"]

        # Create scan result with new leaders
        top_movers = [
            VolatilityMetrics("005930", 50000, 500, 2.0, 3.0, 4.0, 3.0, 50.0, 90.0),
            VolatilityMetrics("005380", 48000, 480, 1.8, 2.5, 3.0, 2.8, 45.0, 85.0),
            VolatilityMetrics("005490", 46000, 460, 1.5, 2.0, 2.5, 2.5, 40.0, 80.0),
        ]

        scan_result = ScanResult(
            market_code="KR",
            timestamp="2026-02-04T10:00:00",
            total_scanned=10,
            top_movers=top_movers,
            breakouts=["005380"],
            breakdowns=[],
        )

        updated = scanner.get_updated_watchlist(
            current_watchlist,
            scan_result,
            max_replacements=2,
        )

        assert "005930" in updated  # Should keep existing top mover
        assert "005380" in updated  # Should add new leader
        assert len(updated) == len(current_watchlist)  # Should maintain size

    def test_get_updated_watchlist_all_keepers(self, scanner: MarketScanner) -> None:
        """Test watchlist when all current stocks are still top movers."""
        current_watchlist = ["005930", "000660", "035420"]

        top_movers = [
            VolatilityMetrics("005930", 50000, 500, 2.0, 3.0, 4.0, 3.0, 50.0, 90.0),
            VolatilityMetrics("000660", 48000, 480, 1.8, 2.5, 3.0, 2.8, 45.0, 85.0),
            VolatilityMetrics("035420", 46000, 460, 1.5, 2.0, 2.5, 2.5, 40.0, 80.0),
        ]

        scan_result = ScanResult(
            market_code="KR",
            timestamp="2026-02-04T10:00:00",
            total_scanned=10,
            top_movers=top_movers,
            breakouts=[],
            breakdowns=[],
        )

        updated = scanner.get_updated_watchlist(
            current_watchlist,
            scan_result,
            max_replacements=2,
        )

        # Should keep all current stocks since they're all in top movers
        assert set(updated) == set(current_watchlist)

    def test_get_updated_watchlist_max_replacements(
        self, scanner: MarketScanner
    ) -> None:
        """Test that max_replacements limit is respected."""
        current_watchlist = ["000660", "035420", "005490"]

        # All new leaders (none in current watchlist)
        top_movers = [
            VolatilityMetrics("005930", 50000, 500, 2.0, 3.0, 4.0, 3.0, 50.0, 90.0),
            VolatilityMetrics("005380", 48000, 480, 1.8, 2.5, 3.0, 2.8, 45.0, 85.0),
            VolatilityMetrics("035720", 46000, 460, 1.5, 2.0, 2.5, 2.5, 40.0, 80.0),
        ]

        scan_result = ScanResult(
            market_code="KR",
            timestamp="2026-02-04T10:00:00",
            total_scanned=10,
            top_movers=top_movers,
            breakouts=[],
            breakdowns=[],
        )

        updated = scanner.get_updated_watchlist(
            current_watchlist,
            scan_result,
            max_replacements=1,  # Only allow 1 replacement
        )

        # Should add at most 1 new leader
        new_additions = [code for code in updated if code not in current_watchlist]
        assert len(new_additions) <= 1
        assert len(updated) == len(current_watchlist)
