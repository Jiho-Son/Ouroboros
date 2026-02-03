"""Risk management — the Shield that protects the portfolio.

This module is READ-ONLY by policy (see docs/agents.md).
Changes require human approval and two passing test suites.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config import Settings

logger = logging.getLogger(__name__)


class CircuitBreakerTripped(SystemExit):
    """Raised when daily P&L loss exceeds the allowed threshold."""

    def __init__(self, pnl_pct: float, threshold: float) -> None:
        self.pnl_pct = pnl_pct
        self.threshold = threshold
        super().__init__(
            f"CIRCUIT BREAKER: Daily P&L {pnl_pct:.2f}% exceeded "
            f"threshold {threshold:.2f}%. All trading halted."
        )


class FatFingerRejected(Exception):
    """Raised when an order exceeds the maximum allowed proportion of cash."""

    def __init__(self, order_amount: float, total_cash: float, max_pct: float) -> None:
        self.order_amount = order_amount
        self.total_cash = total_cash
        self.max_pct = max_pct
        ratio = (order_amount / total_cash * 100) if total_cash > 0 else float("inf")
        super().__init__(
            f"FAT FINGER: Order {order_amount:,.0f} is {ratio:.1f}% of "
            f"cash {total_cash:,.0f} (max allowed: {max_pct:.1f}%)."
        )


class RiskManager:
    """Pre-order risk gate that enforces circuit breaker and fat-finger checks."""

    def __init__(self, settings: Settings) -> None:
        self._cb_threshold = settings.CIRCUIT_BREAKER_PCT
        self._ff_max_pct = settings.FAT_FINGER_PCT

    def check_circuit_breaker(self, current_pnl_pct: float) -> None:
        """Halt trading if daily loss exceeds the threshold.

        The threshold is inclusive: exactly -3.0% is allowed, but -3.01% is not.
        """
        if current_pnl_pct < self._cb_threshold:
            logger.critical(
                "Circuit breaker tripped",
                extra={"pnl_pct": current_pnl_pct},
            )
            raise CircuitBreakerTripped(current_pnl_pct, self._cb_threshold)

    def check_fat_finger(self, order_amount: float, total_cash: float) -> None:
        """Reject orders that exceed the maximum proportion of available cash."""
        if total_cash <= 0:
            raise FatFingerRejected(order_amount, total_cash, self._ff_max_pct)

        ratio_pct = (order_amount / total_cash) * 100
        if ratio_pct > self._ff_max_pct:
            logger.warning(
                "Fat finger check failed",
                extra={"order_amount": order_amount},
            )
            raise FatFingerRejected(order_amount, total_cash, self._ff_max_pct)

    def validate_order(
        self,
        current_pnl_pct: float,
        order_amount: float,
        total_cash: float,
    ) -> None:
        """Run all pre-order risk checks. Raises on failure."""
        self.check_circuit_breaker(current_pnl_pct)
        self.check_fat_finger(order_amount, total_cash)
        logger.info("Order passed risk validation")
