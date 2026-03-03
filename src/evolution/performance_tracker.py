"""Performance tracking system for strategy monitoring.

Tracks win rates, monitors improvement over time,
and provides performance metrics dashboard.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StrategyMetrics:
    """Performance metrics for a strategy over a time period."""

    strategy_name: str
    period_start: str
    period_end: str
    total_trades: int
    wins: int
    losses: int
    holds: int
    win_rate: float
    avg_pnl: float
    total_pnl: float
    best_trade: float
    worst_trade: float
    avg_confidence: float


@dataclass
class PerformanceDashboard:
    """Comprehensive performance dashboard."""

    generated_at: str
    overall_metrics: StrategyMetrics
    daily_metrics: list[StrategyMetrics]
    weekly_metrics: list[StrategyMetrics]
    improvement_trend: dict[str, Any]


class PerformanceTracker:
    """Tracks and monitors strategy performance over time."""

    def __init__(self, db_path: str) -> None:
        """Initialize performance tracker.

        Args:
            db_path: Path to the trade logs database
        """
        self._db_path = db_path

    def get_strategy_metrics(
        self,
        strategy_name: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> StrategyMetrics:
        """Get performance metrics for a strategy over a time period.

        Args:
            strategy_name: Name of the strategy (None = all strategies)
            start_date: Start date in ISO format (None = beginning of time)
            end_date: End date in ISO format (None = now)

        Returns:
            StrategyMetrics object with performance data
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row

        try:
            # Build query with optional filters
            query = """
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN action = 'HOLD' THEN 1 ELSE 0 END) as holds,
                    COALESCE(AVG(CASE WHEN pnl IS NOT NULL THEN pnl END), 0) as avg_pnl,
                    COALESCE(SUM(CASE WHEN pnl IS NOT NULL THEN pnl ELSE 0 END), 0) as total_pnl,
                    COALESCE(MAX(pnl), 0) as best_trade,
                    COALESCE(MIN(pnl), 0) as worst_trade,
                    COALESCE(AVG(confidence), 0) as avg_confidence,
                    MIN(timestamp) as period_start,
                    MAX(timestamp) as period_end
                FROM trades
                WHERE 1=1
            """
            params: list[Any] = []

            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)

            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)

            # Note: Currently trades table doesn't have strategy_name column
            # This is a placeholder for future extension

            row = conn.execute(query, params).fetchone()

            total_trades = row["total_trades"] or 0
            wins = row["wins"] or 0
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

            return StrategyMetrics(
                strategy_name=strategy_name or "default",
                period_start=row["period_start"] or "",
                period_end=row["period_end"] or "",
                total_trades=total_trades,
                wins=wins,
                losses=row["losses"] or 0,
                holds=row["holds"] or 0,
                win_rate=round(win_rate, 2),
                avg_pnl=round(row["avg_pnl"], 2),
                total_pnl=round(row["total_pnl"], 2),
                best_trade=round(row["best_trade"], 2),
                worst_trade=round(row["worst_trade"], 2),
                avg_confidence=round(row["avg_confidence"], 2),
            )
        finally:
            conn.close()

    def get_daily_metrics(
        self, days: int = 7, strategy_name: str | None = None
    ) -> list[StrategyMetrics]:
        """Get daily performance metrics for the last N days.

        Args:
            days: Number of days to retrieve (default 7)
            strategy_name: Name of the strategy (None = all strategies)

        Returns:
            List of StrategyMetrics, one per day
        """
        metrics = []
        end_date = datetime.now(UTC)

        for i in range(days):
            day_end = end_date - timedelta(days=i)
            day_start = day_end - timedelta(days=1)

            day_metrics = self.get_strategy_metrics(
                strategy_name=strategy_name,
                start_date=day_start.isoformat(),
                end_date=day_end.isoformat(),
            )
            metrics.append(day_metrics)

        return metrics

    def get_weekly_metrics(
        self, weeks: int = 4, strategy_name: str | None = None
    ) -> list[StrategyMetrics]:
        """Get weekly performance metrics for the last N weeks.

        Args:
            weeks: Number of weeks to retrieve (default 4)
            strategy_name: Name of the strategy (None = all strategies)

        Returns:
            List of StrategyMetrics, one per week
        """
        metrics = []
        end_date = datetime.now(UTC)

        for i in range(weeks):
            week_end = end_date - timedelta(weeks=i)
            week_start = week_end - timedelta(weeks=1)

            week_metrics = self.get_strategy_metrics(
                strategy_name=strategy_name,
                start_date=week_start.isoformat(),
                end_date=week_end.isoformat(),
            )
            metrics.append(week_metrics)

        return metrics

    def calculate_improvement_trend(self, metrics_history: list[StrategyMetrics]) -> dict[str, Any]:
        """Calculate improvement trend from historical metrics.

        Args:
            metrics_history: List of StrategyMetrics ordered from oldest to newest

        Returns:
            Dictionary with trend analysis
        """
        if len(metrics_history) < 2:
            return {
                "trend": "insufficient_data",
                "win_rate_change": 0.0,
                "pnl_change": 0.0,
                "confidence_change": 0.0,
            }

        oldest = metrics_history[0]
        newest = metrics_history[-1]

        win_rate_change = newest.win_rate - oldest.win_rate
        pnl_change = newest.avg_pnl - oldest.avg_pnl
        confidence_change = newest.avg_confidence - oldest.avg_confidence

        # Determine overall trend
        if win_rate_change > 5.0 and pnl_change > 0:
            trend = "improving"
        elif win_rate_change < -5.0 or pnl_change < 0:
            trend = "declining"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "win_rate_change": round(win_rate_change, 2),
            "pnl_change": round(pnl_change, 2),
            "confidence_change": round(confidence_change, 2),
            "period_count": len(metrics_history),
        }

    def generate_dashboard(self, strategy_name: str | None = None) -> PerformanceDashboard:
        """Generate a comprehensive performance dashboard.

        Args:
            strategy_name: Name of the strategy (None = all strategies)

        Returns:
            PerformanceDashboard with all metrics
        """
        # Get overall metrics
        overall_metrics = self.get_strategy_metrics(strategy_name=strategy_name)

        # Get daily metrics (last 7 days)
        daily_metrics = self.get_daily_metrics(days=7, strategy_name=strategy_name)

        # Get weekly metrics (last 4 weeks)
        weekly_metrics = self.get_weekly_metrics(weeks=4, strategy_name=strategy_name)

        # Calculate improvement trend
        improvement_trend = self.calculate_improvement_trend(weekly_metrics[::-1])

        return PerformanceDashboard(
            generated_at=datetime.now(UTC).isoformat(),
            overall_metrics=overall_metrics,
            daily_metrics=daily_metrics,
            weekly_metrics=weekly_metrics,
            improvement_trend=improvement_trend,
        )

    def export_dashboard_json(self, dashboard: PerformanceDashboard) -> str:
        """Export dashboard as JSON string.

        Args:
            dashboard: PerformanceDashboard object

        Returns:
            JSON string representation
        """
        data = {
            "generated_at": dashboard.generated_at,
            "overall_metrics": asdict(dashboard.overall_metrics),
            "daily_metrics": [asdict(m) for m in dashboard.daily_metrics],
            "weekly_metrics": [asdict(m) for m in dashboard.weekly_metrics],
            "improvement_trend": dashboard.improvement_trend,
        }
        return json.dumps(data, indent=2)

    def log_dashboard(self, dashboard: PerformanceDashboard) -> None:
        """Log dashboard summary to logger.

        Args:
            dashboard: PerformanceDashboard object
        """
        logger.info("=" * 60)
        logger.info("PERFORMANCE DASHBOARD")
        logger.info("=" * 60)
        logger.info("Generated: %s", dashboard.generated_at)
        logger.info("")
        logger.info("Overall Performance:")
        logger.info("  Total Trades: %d", dashboard.overall_metrics.total_trades)
        logger.info("  Win Rate: %.2f%%", dashboard.overall_metrics.win_rate)
        logger.info("  Average P&L: %.2f", dashboard.overall_metrics.avg_pnl)
        logger.info("  Total P&L: %.2f", dashboard.overall_metrics.total_pnl)
        logger.info("")
        logger.info("Improvement Trend (%s):", dashboard.improvement_trend["trend"])
        logger.info("  Win Rate Change: %+.2f%%", dashboard.improvement_trend["win_rate_change"])
        logger.info("  P&L Change: %+.2f", dashboard.improvement_trend["pnl_change"])
        logger.info("=" * 60)
