"""Smart Volatility Scanner with volatility-first market ranking logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.analysis.volatility import VolatilityAnalyzer
from src.broker.kis_api import KISBroker
from src.broker.overseas import OverseasBroker
from src.config import Settings
from src.markets.schedule import MarketInfo

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
    """Scans market rankings and applies volatility-first filters.

    Flow:
    1. Fetch fluctuation rankings as primary universe
    2. Fetch volume rankings for liquidity bonus
    3. Score by volatility first, liquidity second
    4. Return top N qualified candidates
    """

    def __init__(
        self,
        broker: KISBroker,
        overseas_broker: OverseasBroker | None,
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
        self.overseas_broker = overseas_broker
        self.analyzer = volatility_analyzer
        self.settings = settings

        # Extract scanner settings
        self.rsi_oversold = settings.RSI_OVERSOLD_THRESHOLD
        self.rsi_momentum = settings.RSI_MOMENTUM_THRESHOLD
        self.vol_multiplier = settings.VOL_MULTIPLIER
        self.top_n = settings.SCANNER_TOP_N

    async def scan(
        self,
        market: MarketInfo | None = None,
        fallback_stocks: list[str] | None = None,
        domestic_session_id: str | None = None,
    ) -> list[ScanCandidate]:
        """Execute smart scan and return qualified candidates.

        Args:
            market: Target market info (domestic vs overseas behavior)
            fallback_stocks: Stock codes to use if ranking API fails

        Returns:
            List of ScanCandidate, sorted by score, up to top_n items
        """
        if market and not market.is_domestic:
            return await self._scan_overseas(market, fallback_stocks)

        return await self._scan_domestic(fallback_stocks, session_id=domestic_session_id)

    async def _scan_domestic(
        self,
        fallback_stocks: list[str] | None = None,
        session_id: str | None = None,
    ) -> list[ScanCandidate]:
        """Scan domestic market using volatility-first ranking + liquidity bonus."""
        # 1) Primary universe from fluctuation ranking.
        try:
            fluct_rows = await self.broker.fetch_market_rankings(
                ranking_type="fluctuation",
                limit=50,
                session_id=session_id,
            )
        except ConnectionError as exc:
            logger.warning("Domestic fluctuation ranking failed: %s", exc)
            fluct_rows = []

        # 2) Liquidity bonus from volume ranking.
        try:
            volume_rows = await self.broker.fetch_market_rankings(
                ranking_type="volume",
                limit=50,
                session_id=session_id,
            )
        except ConnectionError as exc:
            logger.warning("Domestic volume ranking failed: %s", exc)
            volume_rows = []

        if not fluct_rows and fallback_stocks:
            logger.info(
                "Domestic ranking unavailable; using fallback symbols (%d)",
                len(fallback_stocks),
            )
            fluct_rows = [
                {
                    "stock_code": code,
                    "name": code,
                    "price": 0.0,
                    "volume": 0.0,
                    "change_rate": 0.0,
                    "volume_increase_rate": 0.0,
                }
                for code in fallback_stocks
            ]

        if not fluct_rows:
            return []

        volume_rank_bonus: dict[str, float] = {}
        for idx, row in enumerate(volume_rows):
            code = _extract_stock_code(row)
            if not code:
                continue
            volume_rank_bonus[code] = max(0.0, 15.0 - idx * 0.3)

        candidates: list[ScanCandidate] = []
        for stock in fluct_rows:
            stock_code = _extract_stock_code(stock)
            if not stock_code:
                continue

            try:
                price = _extract_last_price(stock)
                change_rate = _extract_change_rate_pct(stock)
                volume = _extract_volume(stock)

                intraday_range_pct = 0.0
                volume_ratio = _safe_float(stock.get("volume_increase_rate"), 0.0) / 100.0 + 1.0

                # Use daily chart to refine range/volume when available.
                daily_prices = await self.broker.get_daily_prices(stock_code, days=2)
                if daily_prices:
                    latest = daily_prices[-1]
                    latest_close = _safe_float(latest.get("close"), default=price)
                    if price <= 0:
                        price = latest_close
                    latest_high = _safe_float(latest.get("high"))
                    latest_low = _safe_float(latest.get("low"))
                    if (
                        latest_close > 0
                        and latest_high > 0
                        and latest_low > 0
                        and latest_high >= latest_low
                    ):
                        intraday_range_pct = (latest_high - latest_low) / latest_close * 100.0
                    if volume <= 0:
                        volume = _safe_float(latest.get("volume"))
                    if len(daily_prices) >= 2:
                        prev_day_volume = _safe_float(daily_prices[-2].get("volume"))
                        if prev_day_volume > 0:
                            volume_ratio = max(volume_ratio, volume / prev_day_volume)

                volatility_pct = max(abs(change_rate), intraday_range_pct)
                if price <= 0 or volatility_pct < 0.8:
                    continue

                volatility_score = min(volatility_pct / 10.0, 1.0) * 85.0
                liquidity_score = volume_rank_bonus.get(stock_code, 0.0)
                score = min(100.0, volatility_score + liquidity_score)
                signal = "momentum" if change_rate >= 0 else "oversold"
                implied_rsi = max(0.0, min(100.0, 50.0 + (change_rate * 2.0)))

                candidates.append(
                    ScanCandidate(
                        stock_code=stock_code,
                        name=stock.get("name", stock_code),
                        price=price,
                        volume=volume,
                        volume_ratio=max(1.0, volume_ratio, volatility_pct / 2.0),
                        rsi=implied_rsi,
                        signal=signal,
                        score=score,
                    )
                )

            except ConnectionError as exc:
                logger.warning("Failed to analyze %s: %s", stock_code, exc)
                continue
            except Exception as exc:
                logger.error("Unexpected error analyzing %s: %s", stock_code, exc)
                continue

        logger.info("Domestic ranking scan found %d candidates", len(candidates))
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: self.top_n]

    async def _scan_overseas(
        self,
        market: MarketInfo,
        fallback_stocks: list[str] | None = None,
    ) -> list[ScanCandidate]:
        """Scan overseas symbols using ranking API first, then fallback universe."""
        if self.overseas_broker is None:
            logger.warning(
                "Overseas scanner unavailable for %s: overseas broker not configured",
                market.name,
            )
            return []

        candidates = await self._scan_overseas_from_rankings(market)
        if not candidates:
            candidates = await self._scan_overseas_from_symbols(market, fallback_stocks)

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: self.top_n]

    async def _scan_overseas_from_rankings(
        self,
        market: MarketInfo,
    ) -> list[ScanCandidate]:
        """Build overseas candidates from ranking APIs using volatility-first scoring."""
        assert self.overseas_broker is not None
        try:
            fluct_rows = await self.overseas_broker.fetch_overseas_rankings(
                exchange_code=market.exchange_code,
                ranking_type="fluctuation",
                limit=50,
            )
        except Exception as exc:
            logger.warning("Overseas fluctuation ranking failed for %s: %s", market.code, exc)
            fluct_rows = []

        if not fluct_rows:
            return []

        volume_rank_bonus: dict[str, float] = {}
        try:
            volume_rows = await self.overseas_broker.fetch_overseas_rankings(
                exchange_code=market.exchange_code,
                ranking_type="volume",
                limit=50,
            )
        except Exception as exc:
            logger.warning("Overseas volume ranking failed for %s: %s", market.code, exc)
            volume_rows = []

        for idx, row in enumerate(volume_rows):
            code = _extract_stock_code(row)
            if not code:
                continue
            # Top-ranked by traded value/volume gets higher liquidity bonus.
            volume_rank_bonus[code] = max(0.0, 15.0 - idx * 0.3)

        candidates: list[ScanCandidate] = []
        for row in fluct_rows:
            stock_code = _extract_stock_code(row)
            if not stock_code:
                continue

            price = _extract_last_price(row)
            change_rate = _extract_change_rate_pct(row)
            volume = _extract_volume(row)
            intraday_range_pct = _extract_intraday_range_pct(row, price)
            volatility_pct = max(abs(change_rate), intraday_range_pct)

            # Volatility-first filter (not simple gainers/value ranking).
            if price <= 0 or volatility_pct < 0.8:
                continue

            volatility_score = min(volatility_pct / 10.0, 1.0) * 85.0
            liquidity_score = volume_rank_bonus.get(stock_code, 0.0)
            score = min(100.0, volatility_score + liquidity_score)
            signal = "momentum" if change_rate >= 0 else "oversold"
            implied_rsi = max(0.0, min(100.0, 50.0 + (change_rate * 2.0)))
            candidates.append(
                ScanCandidate(
                    stock_code=stock_code,
                    name=str(row.get("name") or row.get("ovrs_item_name") or stock_code),
                    price=price,
                    volume=volume,
                    volume_ratio=max(1.0, volatility_pct / 2.0),
                    rsi=implied_rsi,
                    signal=signal,
                    score=score,
                )
            )

        if candidates:
            logger.info(
                "Overseas ranking scan found %d candidates for %s",
                len(candidates),
                market.name,
            )
        return candidates

    async def _scan_overseas_from_symbols(
        self,
        market: MarketInfo,
        symbols: list[str] | None,
    ) -> list[ScanCandidate]:
        """Fallback overseas scan from dynamic symbol universe."""
        assert self.overseas_broker is not None
        if not symbols:
            logger.info("Overseas scanner: no symbol universe for %s", market.name)
            return []

        logger.info(
            "Overseas scanner: scanning %d fallback symbols for %s",
            len(symbols),
            market.name,
        )
        candidates: list[ScanCandidate] = []
        for stock_code in symbols:
            try:
                price_data = await self.overseas_broker.get_overseas_price(
                    market.exchange_code, stock_code
                )
                output = price_data.get("output", {})
                price = _extract_last_price(output)
                change_rate = _extract_change_rate_pct(output)
                volume = _extract_volume(output)
                intraday_range_pct = _extract_intraday_range_pct(output, price)
                volatility_pct = max(abs(change_rate), intraday_range_pct)

                if price <= 0 or volatility_pct < 0.8:
                    continue

                score = min(volatility_pct / 10.0, 1.0) * 100.0
                signal = "momentum" if change_rate >= 0 else "oversold"
                implied_rsi = max(0.0, min(100.0, 50.0 + (change_rate * 2.0)))
                candidates.append(
                    ScanCandidate(
                        stock_code=stock_code,
                        name=stock_code,
                        price=price,
                        volume=volume,
                        volume_ratio=max(1.0, volatility_pct / 2.0),
                        rsi=implied_rsi,
                        signal=signal,
                        score=score,
                    )
                )
            except ConnectionError as exc:
                logger.warning("Failed to analyze overseas %s: %s", stock_code, exc)
            except Exception as exc:
                logger.error("Unexpected error analyzing overseas %s: %s", stock_code, exc)
        logger.info(
            "Overseas symbol fallback scan found %d candidates for %s",
            len(candidates),
            market.name,
        )
        return candidates

    def get_stock_codes(self, candidates: list[ScanCandidate]) -> list[str]:
        """Extract stock codes from candidates for watchlist update.

        Args:
            candidates: List of scan candidates

        Returns:
            List of stock codes
        """
        return [c.stock_code for c in candidates]


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert arbitrary values to float safely."""
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_stock_code(row: dict[str, Any]) -> str:
    """Extract normalized stock code from various API schemas."""
    return (
        str(
            row.get("symb")
            or row.get("ovrs_pdno")
            or row.get("stock_code")
            or row.get("pdno")
            or ""
        )
        .strip()
        .upper()
    )


def _extract_last_price(row: dict[str, Any]) -> float:
    """Extract last/close-like price from API schema variants."""
    return _safe_float(
        row.get("last")
        or row.get("ovrs_nmix_prpr")
        or row.get("stck_prpr")
        or row.get("price")
        or row.get("close")
    )


def _extract_change_rate_pct(row: dict[str, Any]) -> float:
    """Extract daily change rate (%) from API schema variants."""
    return _safe_float(
        row.get("rate")
        or row.get("change_rate")
        or row.get("prdy_ctrt")
        or row.get("evlu_pfls_rt")
        or row.get("chg_rt")
    )


def _extract_volume(row: dict[str, Any]) -> float:
    """Extract volume/traded-amount proxy from schema variants."""
    return _safe_float(
        row.get("tvol") or row.get("acml_vol") or row.get("vol") or row.get("volume")
    )


def _extract_intraday_range_pct(row: dict[str, Any], price: float) -> float:
    """Estimate intraday range percentage from high/low fields."""
    if price <= 0:
        return 0.0
    high = _safe_float(
        row.get("high") or row.get("ovrs_hgpr") or row.get("stck_hgpr") or row.get("day_hgpr")
    )
    low = _safe_float(
        row.get("low") or row.get("ovrs_lwpr") or row.get("stck_lwpr") or row.get("day_lwpr")
    )
    if high <= 0 or low <= 0 or high < low:
        return 0.0
    return (high - low) / price * 100.0
