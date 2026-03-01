"""Volatility and momentum analysis for stock selection.

Calculates ATR, price change percentages, volume surges, and price-volume divergence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VolatilityMetrics:
    """Volatility and momentum metrics for a stock."""

    stock_code: str
    current_price: float
    atr: float  # Average True Range (14 periods)
    price_change_1m: float  # 1-minute price change %
    price_change_5m: float  # 5-minute price change %
    price_change_15m: float  # 15-minute price change %
    volume_surge: float  # Volume vs average (ratio)
    pv_divergence: float  # Price-volume divergence score
    momentum_score: float  # Combined momentum score (0-100)

    def __repr__(self) -> str:
        return (
            f"VolatilityMetrics({self.stock_code}: "
            f"price={self.current_price:.2f}, "
            f"atr={self.atr:.2f}, "
            f"1m={self.price_change_1m:.2f}%, "
            f"vol_surge={self.volume_surge:.2f}x, "
            f"momentum={self.momentum_score:.1f})"
        )


class VolatilityAnalyzer:
    """Analyzes stock volatility and momentum for leader detection."""

    def __init__(self, min_volume_surge: float = 2.0, min_price_change: float = 1.0) -> None:
        """Initialize the volatility analyzer.

        Args:
            min_volume_surge: Minimum volume surge ratio (default 2x average)
            min_price_change: Minimum price change % for breakout (default 1%)
        """
        self.min_volume_surge = min_volume_surge
        self.min_price_change = min_price_change

    def calculate_atr(
        self,
        high_prices: list[float],
        low_prices: list[float],
        close_prices: list[float],
        period: int = 14,
    ) -> float:
        """Calculate Average True Range (ATR).

        Args:
            high_prices: List of high prices (most recent last)
            low_prices: List of low prices (most recent last)
            close_prices: List of close prices (most recent last)
            period: ATR period (default 14)

        Returns:
            ATR value
        """
        if (
            len(high_prices) < period + 1
            or len(low_prices) < period + 1
            or len(close_prices) < period + 1
        ):
            return 0.0

        true_ranges: list[float] = []
        for i in range(1, len(high_prices)):
            high = high_prices[i]
            low = low_prices[i]
            prev_close = close_prices[i - 1]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return 0.0

        # Simple Moving Average of True Range
        recent_tr = true_ranges[-period:]
        return sum(recent_tr) / len(recent_tr)

    def calculate_price_change(self, current_price: float, past_price: float) -> float:
        """Calculate price change percentage.

        Args:
            current_price: Current price
            past_price: Past price to compare against

        Returns:
            Price change percentage
        """
        if past_price == 0:
            return 0.0
        return ((current_price - past_price) / past_price) * 100

    def calculate_volume_surge(self, current_volume: float, avg_volume: float) -> float:
        """Calculate volume surge ratio.

        Args:
            current_volume: Current volume
            avg_volume: Average volume

        Returns:
            Volume surge ratio (current / average)
        """
        if avg_volume == 0:
            return 1.0
        return current_volume / avg_volume

    def calculate_rsi(
        self,
        close_prices: list[float],
        period: int = 14,
    ) -> float:
        """Calculate Relative Strength Index (RSI) using Wilder's smoothing.

        Args:
            close_prices: List of closing prices (oldest to newest, minimum period+1 values)
            period: RSI period (default 14)

        Returns:
            RSI value between 0 and 100, or 50.0 (neutral) if insufficient data

        Examples:
            >>> analyzer = VolatilityAnalyzer()
            >>> prices = [100 - i * 0.5 for i in range(20)]  # Downtrend
            >>> rsi = analyzer.calculate_rsi(prices)
            >>> assert rsi < 50  # Oversold territory
        """
        if len(close_prices) < period + 1:
            return 50.0  # Neutral RSI if insufficient data

        # Calculate price changes
        changes = [close_prices[i] - close_prices[i - 1] for i in range(1, len(close_prices))]

        # Separate gains and losses
        gains = [max(0.0, change) for change in changes]
        losses = [max(0.0, -change) for change in changes]

        # Calculate initial average gain/loss (simple average for first period)
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Apply Wilder's smoothing for remaining periods
        for i in range(period, len(changes)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        # Calculate RS and RSI
        if avg_loss == 0:
            return 100.0  # All gains, maximum RSI

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def calculate_pv_divergence(
        self,
        price_change: float,
        volume_surge: float,
    ) -> float:
        """Calculate price-volume divergence score.

        Positive divergence: Price up + Volume up = bullish
        Negative divergence: Price up + Volume down = bearish
        Neutral: Price/volume move together moderately

        Args:
            price_change: Price change percentage
            volume_surge: Volume surge ratio

        Returns:
            Divergence score (-100 to +100)
        """
        # Normalize volume surge to -1 to +1 scale (1.0 = neutral)
        volume_signal = (volume_surge - 1.0) * 10  # Scale for sensitivity

        # Calculate divergence
        # Positive: price and volume move in same direction
        # Negative: price and volume move in opposite directions
        if price_change > 0 and volume_surge > 1.0:
            # Bullish: price up, volume up
            return min(100.0, price_change * volume_signal)
        elif price_change < 0 and volume_surge < 1.0:
            # Bearish confirmation: price down, volume down
            return max(-100.0, price_change * volume_signal)
        elif price_change > 0 and volume_surge < 1.0:
            # Bearish divergence: price up but volume low (weak rally)
            return -abs(price_change) * 0.5
        elif price_change < 0 and volume_surge > 1.0:
            # Selling pressure: price down, volume up
            return price_change * volume_signal
        else:
            return 0.0

    def calculate_momentum_score(
        self,
        price_change_1m: float,
        price_change_5m: float,
        price_change_15m: float,
        volume_surge: float,
        atr: float,
        current_price: float,
    ) -> float:
        """Calculate combined momentum score (0-100).

        Weights:
        - 1m change: 40%
        - 5m change: 30%
        - 15m change: 20%
        - Volume surge: 10%

        Args:
            price_change_1m: 1-minute price change %
            price_change_5m: 5-minute price change %
            price_change_15m: 15-minute price change %
            volume_surge: Volume surge ratio
            atr: Average True Range
            current_price: Current price

        Returns:
            Momentum score (0-100)
        """
        # Weight recent changes more heavily
        weighted_change = price_change_1m * 0.4 + price_change_5m * 0.3 + price_change_15m * 0.2

        # Volume contribution (normalized to 0-10 scale)
        volume_contribution = min(10.0, (volume_surge - 1.0) * 5.0)

        # Volatility bonus: higher ATR = higher potential (normalized)
        volatility_bonus = 0.0
        if current_price > 0:
            atr_pct = (atr / current_price) * 100
            volatility_bonus = min(10.0, atr_pct)

        # Combine scores
        raw_score = weighted_change + volume_contribution + volatility_bonus

        # Normalize to 0-100 scale
        # Assume typical momentum range is -10 to +30
        normalized = ((raw_score + 10) / 40) * 100

        return max(0.0, min(100.0, normalized))

    def analyze(
        self,
        stock_code: str,
        orderbook_data: dict[str, Any],
        price_history: dict[str, Any],
    ) -> VolatilityMetrics:
        """Analyze volatility and momentum for a stock.

        Args:
            stock_code: Stock code
            orderbook_data: Current orderbook/quote data
            price_history: Historical price and volume data

        Returns:
            VolatilityMetrics with calculated indicators
        """
        # Extract current data from orderbook
        output1 = orderbook_data.get("output1", {})
        current_price = float(output1.get("stck_prpr", 0))
        current_volume = float(output1.get("acml_vol", 0))

        # Extract historical data
        high_prices = price_history.get("high", [])
        low_prices = price_history.get("low", [])
        close_prices = price_history.get("close", [])
        volumes = price_history.get("volume", [])

        # Calculate ATR
        atr = self.calculate_atr(high_prices, low_prices, close_prices)

        # Calculate price changes (use historical data if available)
        price_change_1m = 0.0
        price_change_5m = 0.0
        price_change_15m = 0.0

        if len(close_prices) > 0:
            if len(close_prices) >= 1:
                price_change_1m = self.calculate_price_change(current_price, close_prices[-1])
            if len(close_prices) >= 5:
                price_change_5m = self.calculate_price_change(current_price, close_prices[-5])
            if len(close_prices) >= 15:
                price_change_15m = self.calculate_price_change(current_price, close_prices[-15])

        # Calculate volume surge
        avg_volume = sum(volumes) / len(volumes) if volumes else current_volume
        volume_surge = self.calculate_volume_surge(current_volume, avg_volume)

        # Calculate price-volume divergence
        pv_divergence = self.calculate_pv_divergence(price_change_1m, volume_surge)

        # Calculate momentum score
        momentum_score = self.calculate_momentum_score(
            price_change_1m,
            price_change_5m,
            price_change_15m,
            volume_surge,
            atr,
            current_price,
        )

        return VolatilityMetrics(
            stock_code=stock_code,
            current_price=current_price,
            atr=atr,
            price_change_1m=price_change_1m,
            price_change_5m=price_change_5m,
            price_change_15m=price_change_15m,
            volume_surge=volume_surge,
            pv_divergence=pv_divergence,
            momentum_score=momentum_score,
        )

    def is_breakout(self, metrics: VolatilityMetrics) -> bool:
        """Determine if a stock is experiencing a breakout.

        Args:
            metrics: Volatility metrics for the stock

        Returns:
            True if breakout conditions are met
        """
        return (
            metrics.price_change_1m >= self.min_price_change
            and metrics.volume_surge >= self.min_volume_surge
            and metrics.pv_divergence > 0  # Bullish divergence
        )

    def is_breakdown(self, metrics: VolatilityMetrics) -> bool:
        """Determine if a stock is experiencing a breakdown.

        Args:
            metrics: Volatility metrics for the stock

        Returns:
            True if breakdown conditions are met
        """
        return (
            metrics.price_change_1m <= -self.min_price_change
            and metrics.volume_surge >= self.min_volume_surge
            and metrics.pv_divergence < 0  # Bearish divergence
        )
