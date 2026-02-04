"""Real-time market scanner for detecting high-momentum stocks.

Scans all available stocks in a market and ranks by volatility/momentum score.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from src.analysis.volatility import VolatilityAnalyzer, VolatilityMetrics
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.context.layer import ContextLayer
from src.context.store import ContextStore
from src.markets.schedule import MarketInfo

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result from a market scan."""

    market_code: str
    timestamp: str
    total_scanned: int
    top_movers: list[VolatilityMetrics]
    breakouts: list[str]  # Stock codes with breakout patterns
    breakdowns: list[str]  # Stock codes with breakdown patterns


class MarketScanner:
    """Scans markets for high-volatility, high-momentum stocks."""

    def __init__(
        self,
        broker: KISBroker,
        overseas_broker: OverseasBroker,
        volatility_analyzer: VolatilityAnalyzer,
        context_store: ContextStore,
        top_n: int = 5,
    ) -> None:
        """Initialize the market scanner.

        Args:
            broker: KIS broker instance for domestic market
            overseas_broker: Overseas broker instance
            volatility_analyzer: Volatility analyzer instance
            context_store: Context store for L7 real-time data
            top_n: Number of top movers to return per market (default 5)
        """
        self.broker = broker
        self.overseas_broker = overseas_broker
        self.analyzer = volatility_analyzer
        self.context_store = context_store
        self.top_n = top_n

    async def scan_stock(
        self,
        stock_code: str,
        market: MarketInfo,
    ) -> VolatilityMetrics | None:
        """Scan a single stock for volatility metrics.

        Args:
            stock_code: Stock code to scan
            market: Market information

        Returns:
            VolatilityMetrics if successful, None on error
        """
        try:
            if market.is_domestic:
                orderbook = await self.broker.get_orderbook(stock_code)
            else:
                # Rate limiting: Add 200ms delay for overseas API calls
                # to prevent hitting KIS API rate limit (EGW00201)
                await asyncio.sleep(0.2)

                # For overseas, we need to adapt the price data structure
                price_data = await self.overseas_broker.get_overseas_price(
                    market.exchange_code, stock_code
                )
                # Convert to orderbook-like structure
                orderbook = {
                    "output1": {
                        "stck_prpr": price_data.get("output", {}).get("last", "0") or "0",
                        "acml_vol": price_data.get("output", {}).get("tvol", "0") or "0",
                    }
                }

            # For now, use empty price history (would need real historical data)
            # In production, this would fetch from a time-series database or API
            price_history: dict[str, Any] = {
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
            }

            metrics = self.analyzer.analyze(stock_code, orderbook, price_history)

            # Store in L7 real-time layer
            from datetime import UTC, datetime
            timeframe = datetime.now(UTC).isoformat()
            self.context_store.set_context(
                ContextLayer.L7_REALTIME,
                timeframe,
                f"{market.code}_{stock_code}_volatility",
                {
                    "price": metrics.current_price,
                    "atr": metrics.atr,
                    "price_change_1m": metrics.price_change_1m,
                    "volume_surge": metrics.volume_surge,
                    "momentum_score": metrics.momentum_score,
                },
            )

            return metrics

        except Exception as exc:
            logger.warning("Failed to scan %s (%s): %s", stock_code, market.code, exc)
            return None

    async def scan_market(
        self,
        market: MarketInfo,
        stock_codes: list[str],
    ) -> ScanResult:
        """Scan all stocks in a market and rank by momentum.

        Args:
            market: Market to scan
            stock_codes: List of stock codes to scan

        Returns:
            ScanResult with ranked stocks
        """
        from datetime import UTC, datetime

        logger.info("Scanning %s market (%d stocks)", market.name, len(stock_codes))

        # Scan all stocks concurrently (with rate limiting handled by broker)
        tasks = [self.scan_stock(code, market) for code in stock_codes]
        results = await asyncio.gather(*tasks)

        # Filter out failures and sort by momentum score
        valid_metrics = [m for m in results if m is not None]
        valid_metrics.sort(key=lambda m: m.momentum_score, reverse=True)

        # Get top N movers
        top_movers = valid_metrics[: self.top_n]

        # Detect breakouts and breakdowns
        breakouts = [
            m.stock_code for m in valid_metrics if self.analyzer.is_breakout(m)
        ]
        breakdowns = [
            m.stock_code for m in valid_metrics if self.analyzer.is_breakdown(m)
        ]

        logger.info(
            "%s scan complete: %d scanned, top momentum=%.1f, %d breakouts, %d breakdowns",
            market.name,
            len(valid_metrics),
            top_movers[0].momentum_score if top_movers else 0.0,
            len(breakouts),
            len(breakdowns),
        )

        # Store scan results in L7
        timeframe = datetime.now(UTC).isoformat()
        self.context_store.set_context(
            ContextLayer.L7_REALTIME,
            timeframe,
            f"{market.code}_scan_result",
            {
                "total_scanned": len(valid_metrics),
                "top_movers": [m.stock_code for m in top_movers],
                "breakouts": breakouts,
                "breakdowns": breakdowns,
            },
        )

        return ScanResult(
            market_code=market.code,
            timestamp=timeframe,
            total_scanned=len(valid_metrics),
            top_movers=top_movers,
            breakouts=breakouts,
            breakdowns=breakdowns,
        )

    def get_updated_watchlist(
        self,
        current_watchlist: list[str],
        scan_result: ScanResult,
        max_replacements: int = 2,
    ) -> list[str]:
        """Update watchlist by replacing laggards with leaders.

        Args:
            current_watchlist: Current watchlist
            scan_result: Recent scan result
            max_replacements: Maximum stocks to replace per scan

        Returns:
            Updated watchlist with leaders
        """
        # Keep stocks that are in top movers
        top_codes = [m.stock_code for m in scan_result.top_movers]
        keepers = [code for code in current_watchlist if code in top_codes]

        # Add new leaders not in current watchlist
        new_leaders = [code for code in top_codes if code not in current_watchlist]

        # Limit replacements
        new_leaders = new_leaders[:max_replacements]

        # Create updated watchlist
        updated = keepers + new_leaders

        # If we removed too many, backfill from current watchlist
        if len(updated) < len(current_watchlist):
            backfill = [
                code for code in current_watchlist
                if code not in updated
            ][: len(current_watchlist) - len(updated)]
            updated.extend(backfill)

        logger.info(
            "Watchlist updated: %d kept, %d new leaders, %d total",
            len(keepers),
            len(new_leaders),
            len(updated),
        )

        return updated
