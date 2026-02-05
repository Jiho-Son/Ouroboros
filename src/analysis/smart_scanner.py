"""Smart Volatility Scanner with RSI and volume filters.

Fetches market rankings from KIS API and applies technical filters
to identify high-probability trading candidates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.analysis.volatility import VolatilityAnalyzer
from src.broker.kis_api import KISBroker
from src.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ScanCandidate:
    """A qualified candidate from the smart scanner."""

    stock_code: str
    name: str
    price: float
    volume: float
    volume_ratio: float  # Current volume / previous day volume
    rsi: float
    signal: str  # "oversold" or "momentum"
    score: float  # Composite score for ranking


class SmartVolatilityScanner:
    """Scans market rankings and applies RSI/volume filters.

    Flow:
    1. Fetch volume rankings from KIS API
    2. For each ranked stock, fetch daily prices
    3. Calculate RSI and volume ratio
    4. Apply filters: volume > VOL_MULTIPLIER AND (RSI < 30 OR RSI > 70)
    5. Return top N qualified candidates
    """

    def __init__(
        self,
        broker: KISBroker,
        volatility_analyzer: VolatilityAnalyzer,
        settings: Settings,
    ) -> None:
        """Initialize the smart scanner.

        Args:
            broker: KIS broker for API calls
            volatility_analyzer: Analyzer for RSI calculation
            settings: Application settings
        """
        self.broker = broker
        self.analyzer = volatility_analyzer
        self.settings = settings

        # Extract scanner settings
        self.rsi_oversold = settings.RSI_OVERSOLD_THRESHOLD
        self.rsi_momentum = settings.RSI_MOMENTUM_THRESHOLD
        self.vol_multiplier = settings.VOL_MULTIPLIER
        self.top_n = settings.SCANNER_TOP_N

    async def scan(
        self,
        fallback_stocks: list[str] | None = None,
    ) -> list[ScanCandidate]:
        """Execute smart scan and return qualified candidates.

        Args:
            fallback_stocks: Stock codes to use if ranking API fails

        Returns:
            List of ScanCandidate, sorted by score, up to top_n items
        """
        # Step 1: Fetch rankings
        try:
            rankings = await self.broker.fetch_market_rankings(
                ranking_type="volume",
                limit=30,  # Fetch more than needed for filtering
            )
            logger.info("Fetched %d stocks from volume rankings", len(rankings))
        except ConnectionError as exc:
            logger.warning("Ranking API failed, using fallback: %s", exc)
            if fallback_stocks:
                # Create minimal ranking data for fallback
                rankings = [
                    {
                        "stock_code": code,
                        "name": code,
                        "price": 0,
                        "volume": 0,
                        "change_rate": 0,
                        "volume_increase_rate": 0,
                    }
                    for code in fallback_stocks
                ]
            else:
                return []

        # Step 2: Analyze each stock
        candidates: list[ScanCandidate] = []

        for stock in rankings:
            stock_code = stock["stock_code"]
            if not stock_code:
                continue

            try:
                # Fetch daily prices for RSI calculation
                daily_prices = await self.broker.get_daily_prices(stock_code, days=20)

                if len(daily_prices) < 15:  # Need at least 14+1 for RSI
                    logger.debug("Insufficient price history for %s", stock_code)
                    continue

                # Calculate RSI
                close_prices = [p["close"] for p in daily_prices]
                rsi = self.analyzer.calculate_rsi(close_prices, period=14)

                # Calculate volume ratio (today vs previous day avg)
                if len(daily_prices) >= 2:
                    prev_day_volume = daily_prices[-2]["volume"]
                    current_volume = stock.get("volume", 0) or daily_prices[-1]["volume"]
                    volume_ratio = (
                        current_volume / prev_day_volume if prev_day_volume > 0 else 1.0
                    )
                else:
                    volume_ratio = stock.get("volume_increase_rate", 0) / 100 + 1  # Fallback

                # Apply filters
                volume_qualified = volume_ratio >= self.vol_multiplier
                rsi_oversold = rsi < self.rsi_oversold
                rsi_momentum = rsi > self.rsi_momentum

                if volume_qualified and (rsi_oversold or rsi_momentum):
                    signal = "oversold" if rsi_oversold else "momentum"

                    # Calculate composite score
                    # Higher score for: extreme RSI + high volume
                    rsi_extremity = abs(rsi - 50) / 50  # 0-1 scale
                    volume_score = min(volume_ratio / 5, 1.0)  # Cap at 5x
                    score = (rsi_extremity * 0.6 + volume_score * 0.4) * 100

                    candidates.append(
                        ScanCandidate(
                            stock_code=stock_code,
                            name=stock.get("name", stock_code),
                            price=stock.get("price", daily_prices[-1]["close"]),
                            volume=current_volume,
                            volume_ratio=volume_ratio,
                            rsi=rsi,
                            signal=signal,
                            score=score,
                        )
                    )

                    logger.info(
                        "Qualified: %s (%s) RSI=%.1f vol=%.1fx signal=%s score=%.1f",
                        stock_code,
                        stock.get("name", ""),
                        rsi,
                        volume_ratio,
                        signal,
                        score,
                    )

            except ConnectionError as exc:
                logger.warning("Failed to analyze %s: %s", stock_code, exc)
                continue
            except Exception as exc:
                logger.error("Unexpected error analyzing %s: %s", stock_code, exc)
                continue

        # Sort by score and return top N
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: self.top_n]

    def get_stock_codes(self, candidates: list[ScanCandidate]) -> list[str]:
        """Extract stock codes from candidates for watchlist update.

        Args:
            candidates: List of scan candidates

        Returns:
            List of stock codes
        """
        return [c.stock_code for c in candidates]
