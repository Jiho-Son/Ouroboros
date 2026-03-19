"""Compatibility shim for legacy GeminiClient imports."""

from __future__ import annotations

from src.brain.decision_engine import DecisionEngine, TradeDecision

GeminiClient = DecisionEngine

__all__ = ["GeminiClient", "TradeDecision"]
