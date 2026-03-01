"""Context summarization for efficient historical data representation.

This module summarizes old context data instead of including raw details:
- Key metrics only (averages, trends, not details)
- Rolling window (keep last N days detailed, summarize older)
- Aggregate historical data efficiently
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.context.layer import ContextLayer
from src.context.store import ContextStore


@dataclass(frozen=True)
class SummaryStats:
    """Statistical summary of historical data."""

    count: int
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    std: float | None = None
    trend: str | None = None  # "up", "down", "flat"


class ContextSummarizer:
    """Summarizes historical context data to reduce token usage."""

    def __init__(self, store: ContextStore) -> None:
        """Initialize the context summarizer.

        Args:
            store: ContextStore instance for retrieving context data
        """
        self.store = store

    def summarize_numeric_values(self, values: list[float]) -> SummaryStats:
        """Summarize a list of numeric values.

        Args:
            values: List of numeric values to summarize

        Returns:
            SummaryStats with mean, min, max, std, and trend
        """
        if not values:
            return SummaryStats(count=0)

        count = len(values)
        mean = sum(values) / count
        min_val = min(values)
        max_val = max(values)

        # Calculate standard deviation
        if count > 1:
            variance = sum((x - mean) ** 2 for x in values) / (count - 1)
            std = variance**0.5
        else:
            std = 0.0

        # Determine trend
        trend = "flat"
        if count >= 3:
            # Simple trend: compare first third vs last third
            first_third = values[: count // 3]
            last_third = values[-(count // 3) :]
            first_avg = sum(first_third) / len(first_third)
            last_avg = sum(last_third) / len(last_third)

            # Trend threshold: 5% change
            threshold = 0.05 * abs(first_avg) if first_avg != 0 else 0.01

            if last_avg > first_avg + threshold:
                trend = "up"
            elif last_avg < first_avg - threshold:
                trend = "down"

        return SummaryStats(
            count=count,
            mean=round(mean, 4),
            min=round(min_val, 4),
            max=round(max_val, 4),
            std=round(std, 4),
            trend=trend,
        )

    def summarize_layer(
        self,
        layer: ContextLayer,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Summarize all context data for a layer within a date range.

        Args:
            layer: Context layer to summarize
            start_date: Start date (inclusive), None for all
            end_date: End date (inclusive), None for now

        Returns:
            Dictionary with summarized metrics
        """
        if end_date is None:
            end_date = datetime.now(UTC)

        # Get all contexts for this layer
        all_contexts = self.store.get_all_contexts(layer)

        if not all_contexts:
            return {"summary": "No data available", "count": 0}

        # Group numeric values by key
        numeric_data: dict[str, list[float]] = {}
        text_data: dict[str, list[str]] = {}

        for key, value in all_contexts.items():
            # Try to extract numeric values
            if isinstance(value, (int, float)):
                if key not in numeric_data:
                    numeric_data[key] = []
                numeric_data[key].append(float(value))
            elif isinstance(value, dict):
                # Extract numeric fields from dict
                for subkey, subvalue in value.items():
                    if isinstance(subvalue, (int, float)):
                        full_key = f"{key}.{subkey}"
                        if full_key not in numeric_data:
                            numeric_data[full_key] = []
                        numeric_data[full_key].append(float(subvalue))
            elif isinstance(value, str):
                if key not in text_data:
                    text_data[key] = []
                text_data[key].append(value)

        # Summarize numeric data
        summary: dict[str, Any] = {}

        for key, values in numeric_data.items():
            stats = self.summarize_numeric_values(values)
            summary[key] = {
                "count": stats.count,
                "avg": stats.mean,
                "range": [stats.min, stats.max],
                "trend": stats.trend,
            }

        # Summarize text data (just counts)
        for key, values in text_data.items():
            summary[f"{key}_count"] = len(values)

        summary["total_entries"] = len(all_contexts)

        return summary

    def rolling_window_summary(
        self,
        layer: ContextLayer,
        window_days: int = 30,
        summarize_older: bool = True,
    ) -> dict[str, Any]:
        """Create a rolling window summary.

        Recent data (within window) is kept detailed.
        Older data is summarized to key metrics.

        Args:
            layer: Context layer to summarize
            window_days: Number of days to keep detailed
            summarize_older: Whether to summarize data older than window

        Returns:
            Dictionary with recent (detailed) and historical (summary) data
        """
        result: dict[str, Any] = {
            "window_days": window_days,
            "recent_data": {},
            "historical_summary": {},
        }

        # Get all contexts
        all_contexts = self.store.get_all_contexts(layer)

        recent_values: dict[str, list[float]] = {}
        historical_values: dict[str, list[float]] = {}

        for key, value in all_contexts.items():
            # For simplicity, treat all numeric values
            if isinstance(value, (int, float)):
                # Note: We don't have timestamps in context keys
                # This is a simplified implementation
                # In practice, would need to check timeframe field

                # For now, put recent data in window
                if key not in recent_values:
                    recent_values[key] = []
                recent_values[key].append(float(value))

        # Detailed recent data
        result["recent_data"] = {key: values[-10:] for key, values in recent_values.items()}

        # Summarized historical data
        if summarize_older:
            for key, values in historical_values.items():
                stats = self.summarize_numeric_values(values)
                result["historical_summary"][key] = {
                    "count": stats.count,
                    "avg": stats.mean,
                    "trend": stats.trend,
                }

        return result

    def aggregate_to_higher_layer(
        self,
        source_layer: ContextLayer,
        target_layer: ContextLayer,
        metric_key: str,
        aggregation_func: str = "mean",
    ) -> float | None:
        """Aggregate data from source layer to target layer.

        Args:
            source_layer: Source context layer (more granular)
            target_layer: Target context layer (less granular)
            metric_key: Key of metric to aggregate
            aggregation_func: Aggregation function ("mean", "sum", "max", "min")

        Returns:
            Aggregated value, or None if no data available
        """
        # Get all contexts from source layer
        source_contexts = self.store.get_all_contexts(source_layer)

        # Extract values for metric_key
        values = []
        for key, value in source_contexts.items():
            if key == metric_key and isinstance(value, (int, float)):
                values.append(float(value))
            elif isinstance(value, dict) and metric_key in value:
                subvalue = value[metric_key]
                if isinstance(subvalue, (int, float)):
                    values.append(float(subvalue))

        if not values:
            return None

        # Apply aggregation function
        if aggregation_func == "mean":
            return sum(values) / len(values)
        elif aggregation_func == "sum":
            return sum(values)
        elif aggregation_func == "max":
            return max(values)
        elif aggregation_func == "min":
            return min(values)
        else:
            return sum(values) / len(values)  # Default to mean

    def create_compact_summary(
        self,
        layers: list[ContextLayer],
        top_n_metrics: int = 5,
    ) -> dict[str, Any]:
        """Create a compact summary across multiple layers.

        Args:
            layers: List of context layers to summarize
            top_n_metrics: Number of top metrics to include per layer

        Returns:
            Compact summary dictionary
        """
        summary: dict[str, Any] = {}

        for layer in layers:
            layer_summary = self.summarize_layer(layer)

            # Keep only top N metrics (by count/relevance)
            metrics = []
            for key, value in layer_summary.items():
                if isinstance(value, dict) and "count" in value:
                    metrics.append((key, value, value["count"]))

            # Sort by count (descending)
            metrics.sort(key=lambda x: x[2], reverse=True)

            # Keep top N
            top_metrics = {m[0]: m[1] for m in metrics[:top_n_metrics]}

            summary[layer.value] = top_metrics

        return summary

    def format_summary_for_prompt(self, summary: dict[str, Any]) -> str:
        """Format summary for inclusion in a prompt.

        Args:
            summary: Summary dictionary

        Returns:
            Formatted string for prompt
        """
        lines = []

        for layer, metrics in summary.items():
            if not metrics:
                continue

            lines.append(f"{layer}:")
            for key, value in metrics.items():
                if isinstance(value, dict):
                    # Format as: key: avg=X, trend=Y
                    parts = []
                    if "avg" in value and value["avg"] is not None:
                        parts.append(f"avg={value['avg']:.2f}")
                    if "trend" in value and value["trend"]:
                        parts.append(f"trend={value['trend']}")
                    if parts:
                        lines.append(f"  {key}: {', '.join(parts)}")
                else:
                    lines.append(f"  {key}: {value}")

        return "\n".join(lines)
