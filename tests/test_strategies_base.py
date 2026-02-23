"""Tests for BaseStrategy abstract class."""

from __future__ import annotations

from typing import Any

import pytest

from src.strategies.base import BaseStrategy


class ConcreteStrategy(BaseStrategy):
    """Minimal concrete strategy for testing."""

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
        return {"action": "HOLD", "confidence": 50, "rationale": "test"}


def test_base_strategy_cannot_be_instantiated() -> None:
    """BaseStrategy cannot be instantiated directly (it's abstract)."""
    with pytest.raises(TypeError):
        BaseStrategy()  # type: ignore[abstract]


def test_concrete_strategy_evaluate_returns_decision() -> None:
    """Concrete subclass must implement evaluate and return a dict."""
    strategy = ConcreteStrategy()
    result = strategy.evaluate({"close": [100.0, 101.0]})
    assert isinstance(result, dict)
    assert result["action"] == "HOLD"
    assert result["confidence"] == 50
    assert "rationale" in result
