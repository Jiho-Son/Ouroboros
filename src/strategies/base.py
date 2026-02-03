"""Base class for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseStrategy(ABC):
    """All strategies must inherit from this class."""

    @abstractmethod
    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
        """Evaluate market data and return a trade decision.

        Returns:
            dict with keys: action ("BUY"|"SELL"|"HOLD"), confidence (int), rationale (str)
        """
        ...
