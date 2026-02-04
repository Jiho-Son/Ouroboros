"""Criticality assessment for urgency-based response system.

Evaluates market conditions to determine response urgency and enable
faster reactions in critical situations.
"""

from __future__ import annotations

from enum import StrEnum


class CriticalityLevel(StrEnum):
    """Urgency levels for market conditions and trading decisions."""

    CRITICAL = "CRITICAL"  # <5s timeout - Emergency response required
    HIGH = "HIGH"  # <30s timeout - Elevated priority
    NORMAL = "NORMAL"  # <60s timeout - Standard processing
    LOW = "LOW"  # No timeout - Batch processing


class CriticalityAssessor:
    """Assesses market conditions to determine response criticality level."""

    def __init__(
        self,
        critical_pnl_threshold: float = -2.5,
        critical_price_change_threshold: float = 5.0,
        critical_volume_surge_threshold: float = 10.0,
        high_volatility_threshold: float = 70.0,
        low_volatility_threshold: float = 30.0,
    ) -> None:
        """Initialize the criticality assessor.

        Args:
            critical_pnl_threshold: P&L % that triggers CRITICAL (default -2.5%)
            critical_price_change_threshold: Price change % that triggers CRITICAL
                (default 5.0% in 1 minute)
            critical_volume_surge_threshold: Volume surge ratio that triggers CRITICAL
                (default 10x average)
            high_volatility_threshold: Volatility score that triggers HIGH
                (default 70.0)
            low_volatility_threshold: Volatility score below which is LOW
                (default 30.0)
        """
        self.critical_pnl_threshold = critical_pnl_threshold
        self.critical_price_change_threshold = critical_price_change_threshold
        self.critical_volume_surge_threshold = critical_volume_surge_threshold
        self.high_volatility_threshold = high_volatility_threshold
        self.low_volatility_threshold = low_volatility_threshold

    def assess_market_conditions(
        self,
        pnl_pct: float,
        volatility_score: float,
        volume_surge: float,
        price_change_1m: float = 0.0,
        is_market_open: bool = True,
    ) -> CriticalityLevel:
        """Assess criticality level based on market conditions.

        Args:
            pnl_pct: Current P&L percentage
            volatility_score: Momentum score from VolatilityAnalyzer (0-100)
            volume_surge: Volume surge ratio (current / average)
            price_change_1m: 1-minute price change percentage
            is_market_open: Whether the market is currently open

        Returns:
            CriticalityLevel indicating required response urgency
        """
        # Market closed or very quiet → LOW priority (batch processing)
        if not is_market_open or volatility_score < self.low_volatility_threshold:
            return CriticalityLevel.LOW

        # CRITICAL conditions: immediate action required
        # 1. P&L near circuit breaker (-2.5% is close to -3.0% breaker)
        if pnl_pct <= self.critical_pnl_threshold:
            return CriticalityLevel.CRITICAL

        # 2. Large sudden price movement (>5% in 1 minute)
        if abs(price_change_1m) >= self.critical_price_change_threshold:
            return CriticalityLevel.CRITICAL

        # 3. Extreme volume surge (>10x average) indicates major event
        if volume_surge >= self.critical_volume_surge_threshold:
            return CriticalityLevel.CRITICAL

        # HIGH priority: elevated volatility requires faster response
        if volatility_score >= self.high_volatility_threshold:
            return CriticalityLevel.HIGH

        # NORMAL: standard trading conditions
        return CriticalityLevel.NORMAL

    def get_timeout(self, level: CriticalityLevel) -> float | None:
        """Get timeout in seconds for a given criticality level.

        Args:
            level: Criticality level

        Returns:
            Timeout in seconds, or None for no timeout (LOW priority)
        """
        timeout_map = {
            CriticalityLevel.CRITICAL: 5.0,
            CriticalityLevel.HIGH: 30.0,
            CriticalityLevel.NORMAL: 60.0,
            CriticalityLevel.LOW: None,
        }
        return timeout_map[level]
