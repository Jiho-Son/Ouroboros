"""Smart context selection for optimizing token usage.

This module implements intelligent selection of context layers (L1-L7) based on
decision type and market conditions:
- L7 (real-time) for normal trading decisions
- L6-L5 (daily/weekly) for strategic decisions
- L4-L1 (monthly/legacy) only for major events or policy changes
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.context.layer import ContextLayer
from src.context.store import ContextStore


class DecisionType(str, Enum):
    """Type of trading decision being made."""

    NORMAL = "normal"  # Regular trade decision
    STRATEGIC = "strategic"  # Strategy adjustment
    MAJOR_EVENT = "major_event"  # Portfolio rebalancing, policy change


@dataclass(frozen=True)
class ContextSelection:
    """Selected context layers and their relevance scores."""

    layers: list[ContextLayer]
    relevance_scores: dict[ContextLayer, float]
    total_score: float


class ContextSelector:
    """Selects optimal context layers to minimize token usage."""

    def __init__(self, store: ContextStore) -> None:
        """Initialize the context selector.

        Args:
            store: ContextStore instance for retrieving context data
        """
        self.store = store

    def select_layers(
        self,
        decision_type: DecisionType = DecisionType.NORMAL,
        include_realtime: bool = True,
    ) -> list[ContextLayer]:
        """Select context layers based on decision type.

        Strategy:
        - NORMAL: L7 (real-time) only
        - STRATEGIC: L7 + L6 + L5 (real-time + daily + weekly)
        - MAJOR_EVENT: All layers L1-L7

        Args:
            decision_type: Type of decision being made
            include_realtime: Whether to include L7 real-time data

        Returns:
            List of context layers to use (ordered by priority)
        """
        if decision_type == DecisionType.NORMAL:
            # Normal trading: only real-time data
            return [ContextLayer.L7_REALTIME] if include_realtime else []

        elif decision_type == DecisionType.STRATEGIC:
            # Strategic decisions: real-time + recent history
            layers = []
            if include_realtime:
                layers.append(ContextLayer.L7_REALTIME)
            layers.extend([ContextLayer.L6_DAILY, ContextLayer.L5_WEEKLY])
            return layers

        else:  # MAJOR_EVENT
            # Major events: all layers for comprehensive context
            layers = []
            if include_realtime:
                layers.append(ContextLayer.L7_REALTIME)
            layers.extend(
                [
                    ContextLayer.L6_DAILY,
                    ContextLayer.L5_WEEKLY,
                    ContextLayer.L4_MONTHLY,
                    ContextLayer.L3_QUARTERLY,
                    ContextLayer.L2_ANNUAL,
                    ContextLayer.L1_LEGACY,
                ]
            )
            return layers

    def score_layer_relevance(
        self,
        layer: ContextLayer,
        decision_type: DecisionType,
        current_time: datetime | None = None,
    ) -> float:
        """Calculate relevance score for a context layer.

        Relevance is based on:
        1. Decision type (normal, strategic, major event)
        2. Layer recency (L7 > L6 > ... > L1)
        3. Data availability

        Args:
            layer: Context layer to score
            decision_type: Type of decision being made
            current_time: Current time (defaults to now)

        Returns:
            Relevance score (0.0 to 1.0)
        """
        if current_time is None:
            current_time = datetime.now(UTC)

        # Base scores by decision type
        base_scores = {
            DecisionType.NORMAL: {
                ContextLayer.L7_REALTIME: 1.0,
                ContextLayer.L6_DAILY: 0.1,
                ContextLayer.L5_WEEKLY: 0.05,
                ContextLayer.L4_MONTHLY: 0.01,
                ContextLayer.L3_QUARTERLY: 0.0,
                ContextLayer.L2_ANNUAL: 0.0,
                ContextLayer.L1_LEGACY: 0.0,
            },
            DecisionType.STRATEGIC: {
                ContextLayer.L7_REALTIME: 0.9,
                ContextLayer.L6_DAILY: 0.8,
                ContextLayer.L5_WEEKLY: 0.7,
                ContextLayer.L4_MONTHLY: 0.3,
                ContextLayer.L3_QUARTERLY: 0.2,
                ContextLayer.L2_ANNUAL: 0.1,
                ContextLayer.L1_LEGACY: 0.05,
            },
            DecisionType.MAJOR_EVENT: {
                ContextLayer.L7_REALTIME: 0.7,
                ContextLayer.L6_DAILY: 0.7,
                ContextLayer.L5_WEEKLY: 0.7,
                ContextLayer.L4_MONTHLY: 0.8,
                ContextLayer.L3_QUARTERLY: 0.8,
                ContextLayer.L2_ANNUAL: 0.9,
                ContextLayer.L1_LEGACY: 1.0,
            },
        }

        score = base_scores[decision_type].get(layer, 0.0)

        # Check data availability
        latest_timeframe = self.store.get_latest_timeframe(layer)
        if latest_timeframe is None:
            # No data available - reduce score significantly
            score *= 0.1

        return score

    def select_with_scoring(
        self,
        decision_type: DecisionType = DecisionType.NORMAL,
        min_score: float = 0.5,
    ) -> ContextSelection:
        """Select context layers with relevance scoring.

        Args:
            decision_type: Type of decision being made
            min_score: Minimum relevance score to include a layer

        Returns:
            ContextSelection with selected layers and scores
        """
        all_layers = [
            ContextLayer.L7_REALTIME,
            ContextLayer.L6_DAILY,
            ContextLayer.L5_WEEKLY,
            ContextLayer.L4_MONTHLY,
            ContextLayer.L3_QUARTERLY,
            ContextLayer.L2_ANNUAL,
            ContextLayer.L1_LEGACY,
        ]

        scores = {
            layer: self.score_layer_relevance(layer, decision_type) for layer in all_layers
        }

        # Filter by minimum score
        selected_layers = [layer for layer, score in scores.items() if score >= min_score]

        # Sort by score (descending)
        selected_layers.sort(key=lambda layer: scores[layer], reverse=True)

        total_score = sum(scores[layer] for layer in selected_layers)

        return ContextSelection(
            layers=selected_layers,
            relevance_scores=scores,
            total_score=total_score,
        )

    def get_context_data(
        self,
        layers: list[ContextLayer],
        max_items_per_layer: int = 10,
    ) -> dict[str, Any]:
        """Retrieve context data for selected layers.

        Args:
            layers: List of context layers to retrieve
            max_items_per_layer: Maximum number of items per layer

        Returns:
            Dictionary with context data organized by layer
        """
        result: dict[str, Any] = {}

        for layer in layers:
            # Get latest timeframe for this layer
            latest_timeframe = self.store.get_latest_timeframe(layer)
            if latest_timeframe:
                # Get all contexts for latest timeframe
                contexts = self.store.get_all_contexts(layer, latest_timeframe)

                # Limit number of items
                if len(contexts) > max_items_per_layer:
                    # Keep only first N items
                    contexts = dict(list(contexts.items())[:max_items_per_layer])

                result[layer.value] = contexts

        return result

    def estimate_context_tokens(self, context_data: dict[str, Any]) -> int:
        """Estimate total tokens for context data.

        Args:
            context_data: Context data dictionary

        Returns:
            Estimated token count
        """
        import json

        from src.brain.prompt_optimizer import PromptOptimizer

        # Serialize to JSON and estimate tokens
        json_str = json.dumps(context_data, ensure_ascii=False)
        return PromptOptimizer.estimate_tokens(json_str)

    def optimize_context_for_budget(
        self,
        decision_type: DecisionType,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Select and retrieve context data within a token budget.

        Args:
            decision_type: Type of decision being made
            max_tokens: Maximum token budget for context

        Returns:
            Optimized context data within budget
        """
        # Start with minimal selection
        selection = self.select_with_scoring(decision_type, min_score=0.5)

        # Retrieve data
        context_data = self.get_context_data(selection.layers)

        # Check if within budget
        estimated_tokens = self.estimate_context_tokens(context_data)

        if estimated_tokens <= max_tokens:
            return context_data

        # If over budget, progressively reduce
        # 1. Reduce items per layer
        for max_items in [5, 3, 1]:
            context_data = self.get_context_data(selection.layers, max_items)
            estimated_tokens = self.estimate_context_tokens(context_data)
            if estimated_tokens <= max_tokens:
                return context_data

        # 2. Remove lower-priority layers
        for min_score in [0.6, 0.7, 0.8, 0.9]:
            selection = self.select_with_scoring(decision_type, min_score=min_score)
            context_data = self.get_context_data(selection.layers, max_items_per_layer=1)
            estimated_tokens = self.estimate_context_tokens(context_data)
            if estimated_tokens <= max_tokens:
                return context_data

        # Last resort: return only L7 with minimal data
        return self.get_context_data([ContextLayer.L7_REALTIME], max_items_per_layer=1)
