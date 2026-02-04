"""Additional market data indicators beyond basic price data.

Provides market breadth, sector performance, and market sentiment indicators.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class MarketSentiment(Enum):
    """Overall market sentiment levels."""

    EXTREME_FEAR = 1
    FEAR = 2
    NEUTRAL = 3
    GREED = 4
    EXTREME_GREED = 5


@dataclass
class SectorPerformance:
    """Performance metrics for a market sector."""

    sector_name: str
    daily_change_pct: float
    weekly_change_pct: float
    leader_stock: str  # Best performing stock in sector
    laggard_stock: str  # Worst performing stock in sector


@dataclass
class MarketBreadth:
    """Market breadth indicators."""

    advancing_stocks: int
    declining_stocks: int
    unchanged_stocks: int
    new_highs: int
    new_lows: int
    advance_decline_ratio: float


@dataclass
class MarketIndicators:
    """Aggregated market indicators."""

    sentiment: MarketSentiment
    breadth: MarketBreadth
    sector_performance: list[SectorPerformance]
    vix_level: float | None  # Volatility index if available


class MarketData:
    """Market data provider for additional indicators."""

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize market data provider.

        Args:
            api_key: API key for data provider (None for testing)
        """
        self._api_key = api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_market_sentiment(self) -> MarketSentiment:
        """Get current market sentiment level.

        This is a simplified version. In production, this would integrate
        with Fear & Greed Index or similar sentiment indicators.

        Returns:
            MarketSentiment enum value
        """
        # Default to neutral when API not available
        if self._api_key is None:
            logger.debug("No market data API key — returning NEUTRAL sentiment")
            return MarketSentiment.NEUTRAL

        # TODO: Integrate with actual sentiment API
        return MarketSentiment.NEUTRAL

    def get_market_breadth(self, market: str = "US") -> MarketBreadth | None:
        """Get market breadth indicators.

        Args:
            market: Market code ("US", "KR", etc.)

        Returns:
            MarketBreadth object or None if unavailable
        """
        if self._api_key is None:
            logger.debug("No market data API key — returning None for breadth")
            return None

        # TODO: Integrate with actual market breadth API
        return None

    def get_sector_performance(
        self, market: str = "US"
    ) -> list[SectorPerformance]:
        """Get sector performance rankings.

        Args:
            market: Market code ("US", "KR", etc.)

        Returns:
            List of SectorPerformance objects, sorted by daily change
        """
        if self._api_key is None:
            logger.debug("No market data API key — returning empty sector list")
            return []

        # TODO: Integrate with actual sector performance API
        return []

    def get_market_indicators(self, market: str = "US") -> MarketIndicators:
        """Get aggregated market indicators.

        Args:
            market: Market code ("US", "KR", etc.)

        Returns:
            MarketIndicators with all available data
        """
        sentiment = self.get_market_sentiment()
        breadth = self.get_market_breadth(market)
        sectors = self.get_sector_performance(market)

        # Default breadth if unavailable
        if breadth is None:
            breadth = MarketBreadth(
                advancing_stocks=0,
                declining_stocks=0,
                unchanged_stocks=0,
                new_highs=0,
                new_lows=0,
                advance_decline_ratio=1.0,
            )

        return MarketIndicators(
            sentiment=sentiment,
            breadth=breadth,
            sector_performance=sectors,
            vix_level=None,  # TODO: Add VIX integration
        )

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------

    def calculate_fear_greed_score(
        self, breadth: MarketBreadth, vix: float | None = None
    ) -> int:
        """Calculate a simple fear/greed score (0-100).

        Args:
            breadth: Market breadth data
            vix: VIX level (optional)

        Returns:
            Score from 0 (extreme fear) to 100 (extreme greed)
        """
        # Start at neutral
        score = 50

        # Adjust based on advance/decline ratio
        if breadth.advance_decline_ratio > 1.5:
            score += 20
        elif breadth.advance_decline_ratio > 1.0:
            score += 10
        elif breadth.advance_decline_ratio < 0.5:
            score -= 20
        elif breadth.advance_decline_ratio < 1.0:
            score -= 10

        # Adjust based on new highs/lows
        if breadth.new_highs > breadth.new_lows * 2:
            score += 15
        elif breadth.new_lows > breadth.new_highs * 2:
            score -= 15

        # Adjust based on VIX if available
        if vix is not None:
            if vix > 30:  # High volatility = fear
                score -= 15
            elif vix < 15:  # Low volatility = complacency/greed
                score += 10

        # Clamp to 0-100
        return max(0, min(100, score))
