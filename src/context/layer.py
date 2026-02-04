"""Context layer definitions for multi-tier memory management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContextLayer(str, Enum):
    """7-tier context hierarchy from real-time to generational."""

    L1_LEGACY = "L1_LEGACY"  # Cumulative/generational wisdom
    L2_ANNUAL = "L2_ANNUAL"  # Yearly performance
    L3_QUARTERLY = "L3_QUARTERLY"  # Quarterly strategy adjustments
    L4_MONTHLY = "L4_MONTHLY"  # Monthly rebalancing
    L5_WEEKLY = "L5_WEEKLY"  # Weekly stock selection
    L6_DAILY = "L6_DAILY"  # Daily trade logs
    L7_REALTIME = "L7_REALTIME"  # Real-time market data


@dataclass(frozen=True)
class LayerMetadata:
    """Metadata for each context layer."""

    layer: ContextLayer
    description: str
    retention_days: int | None  # None = keep forever
    aggregation_source: ContextLayer | None  # Parent layer for aggregation


# Layer configuration
LAYER_CONFIG: dict[ContextLayer, LayerMetadata] = {
    ContextLayer.L1_LEGACY: LayerMetadata(
        layer=ContextLayer.L1_LEGACY,
        description="Cumulative trading history and core lessons learned across generations",
        retention_days=None,  # Keep forever
        aggregation_source=ContextLayer.L2_ANNUAL,
    ),
    ContextLayer.L2_ANNUAL: LayerMetadata(
        layer=ContextLayer.L2_ANNUAL,
        description="Yearly returns, Sharpe ratio, max drawdown, win rate",
        retention_days=365 * 10,  # 10 years
        aggregation_source=ContextLayer.L3_QUARTERLY,
    ),
    ContextLayer.L3_QUARTERLY: LayerMetadata(
        layer=ContextLayer.L3_QUARTERLY,
        description="Quarterly strategy adjustments, market phase detection, sector rotation",
        retention_days=365 * 3,  # 3 years
        aggregation_source=ContextLayer.L4_MONTHLY,
    ),
    ContextLayer.L4_MONTHLY: LayerMetadata(
        layer=ContextLayer.L4_MONTHLY,
        description="Monthly portfolio rebalancing, risk exposure, drawdown recovery",
        retention_days=365 * 2,  # 2 years
        aggregation_source=ContextLayer.L5_WEEKLY,
    ),
    ContextLayer.L5_WEEKLY: LayerMetadata(
        layer=ContextLayer.L5_WEEKLY,
        description="Weekly stock selection, sector focus, volatility regime",
        retention_days=365,  # 1 year
        aggregation_source=ContextLayer.L6_DAILY,
    ),
    ContextLayer.L6_DAILY: LayerMetadata(
        layer=ContextLayer.L6_DAILY,
        description="Daily trade logs, P&L, market summaries, decision accuracy",
        retention_days=90,  # 90 days
        aggregation_source=ContextLayer.L7_REALTIME,
    ),
    ContextLayer.L7_REALTIME: LayerMetadata(
        layer=ContextLayer.L7_REALTIME,
        description="Real-time positions, quotes, orderbook, volatility, live P&L",
        retention_days=7,  # 7 days (real-time data is ephemeral)
        aggregation_source=None,  # No aggregation source (leaf layer)
    ),
}
