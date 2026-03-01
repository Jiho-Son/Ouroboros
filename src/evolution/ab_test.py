"""A/B Testing framework for strategy comparison.

Runs multiple strategies in parallel, tracks their performance,
and uses statistical significance testing to determine winners.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import scipy.stats as stats

logger = logging.getLogger(__name__)


@dataclass
class StrategyPerformance:
    """Performance metrics for a single strategy."""

    strategy_name: str
    total_trades: int
    wins: int
    losses: int
    total_pnl: float
    avg_pnl: float
    win_rate: float
    sharpe_ratio: float | None = None


@dataclass
class ABTestResult:
    """Result of an A/B test between two strategies."""

    strategy_a: str
    strategy_b: str
    winner: str | None
    p_value: float
    confidence_level: float
    is_significant: bool
    performance_a: StrategyPerformance
    performance_b: StrategyPerformance


class ABTester:
    """A/B testing framework for comparing trading strategies."""

    def __init__(self, significance_level: float = 0.05) -> None:
        """Initialize A/B tester.

        Args:
            significance_level: P-value threshold for statistical significance (default 0.05)
        """
        self._significance_level = significance_level

    def calculate_performance(
        self, trades: list[dict[str, Any]], strategy_name: str
    ) -> StrategyPerformance:
        """Calculate performance metrics for a strategy.

        Args:
            trades: List of trade records with pnl values
            strategy_name: Name of the strategy

        Returns:
            StrategyPerformance object with calculated metrics
        """
        if not trades:
            return StrategyPerformance(
                strategy_name=strategy_name,
                total_trades=0,
                wins=0,
                losses=0,
                total_pnl=0.0,
                avg_pnl=0.0,
                win_rate=0.0,
                sharpe_ratio=None,
            )

        total_trades = len(trades)
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in trades if t.get("pnl", 0) < 0)
        pnls = [t.get("pnl", 0.0) for t in trades]
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0.0
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        # Calculate Sharpe ratio (risk-adjusted return)
        sharpe_ratio = None
        if len(pnls) > 1:
            mean_return = avg_pnl
            std_return = (sum((p - mean_return) ** 2 for p in pnls) / (len(pnls) - 1)) ** 0.5
            if std_return > 0:
                sharpe_ratio = mean_return / std_return

        return StrategyPerformance(
            strategy_name=strategy_name,
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            total_pnl=round(total_pnl, 2),
            avg_pnl=round(avg_pnl, 2),
            win_rate=round(win_rate, 2),
            sharpe_ratio=round(sharpe_ratio, 4) if sharpe_ratio else None,
        )

    def compare_strategies(
        self,
        trades_a: list[dict[str, Any]],
        trades_b: list[dict[str, Any]],
        strategy_a_name: str = "Strategy A",
        strategy_b_name: str = "Strategy B",
    ) -> ABTestResult:
        """Compare two strategies using statistical testing.

        Uses a two-sample t-test to determine if performance difference is significant.

        Args:
            trades_a: List of trades from strategy A
            trades_b: List of trades from strategy B
            strategy_a_name: Name of strategy A
            strategy_b_name: Name of strategy B

        Returns:
            ABTestResult with comparison details
        """
        perf_a = self.calculate_performance(trades_a, strategy_a_name)
        perf_b = self.calculate_performance(trades_b, strategy_b_name)

        # Extract PnL arrays for statistical testing
        pnls_a = [t.get("pnl", 0.0) for t in trades_a]
        pnls_b = [t.get("pnl", 0.0) for t in trades_b]

        # Perform two-sample t-test
        if len(pnls_a) > 1 and len(pnls_b) > 1:
            t_stat, p_value = stats.ttest_ind(pnls_a, pnls_b, equal_var=False)
            is_significant = p_value < self._significance_level
            confidence_level = (1 - p_value) * 100
        else:
            # Not enough data for statistical test
            p_value = 1.0
            is_significant = False
            confidence_level = 0.0

        # Determine winner based on average PnL
        winner = None
        if is_significant:
            if perf_a.avg_pnl > perf_b.avg_pnl:
                winner = strategy_a_name
            elif perf_b.avg_pnl > perf_a.avg_pnl:
                winner = strategy_b_name

        return ABTestResult(
            strategy_a=strategy_a_name,
            strategy_b=strategy_b_name,
            winner=winner,
            p_value=round(p_value, 4),
            confidence_level=round(confidence_level, 2),
            is_significant=is_significant,
            performance_a=perf_a,
            performance_b=perf_b,
        )

    def should_deploy(
        self,
        result: ABTestResult,
        min_win_rate: float = 60.0,
        min_trades: int = 20,
    ) -> bool:
        """Determine if a winning strategy should be deployed.

        Args:
            result: A/B test result
            min_win_rate: Minimum win rate percentage for deployment (default 60%)
            min_trades: Minimum number of trades required (default 20)

        Returns:
            True if the winning strategy meets deployment criteria
        """
        if not result.is_significant or result.winner is None:
            return False

        # Get performance of winning strategy
        if result.winner == result.strategy_a:
            winning_perf = result.performance_a
        else:
            winning_perf = result.performance_b

        # Check deployment criteria
        has_enough_trades = winning_perf.total_trades >= min_trades
        has_good_win_rate = winning_perf.win_rate >= min_win_rate
        is_profitable = winning_perf.avg_pnl > 0

        meets_criteria = has_enough_trades and has_good_win_rate and is_profitable

        if meets_criteria:
            logger.info(
                "Strategy '%s' meets deployment criteria: win_rate=%.2f%%, trades=%d, avg_pnl=%.2f",
                result.winner,
                winning_perf.win_rate,
                winning_perf.total_trades,
                winning_perf.avg_pnl,
            )
        else:
            logger.info(
                "Strategy '%s' does NOT meet deployment criteria: "
                "win_rate=%.2f%% (min %.2f%%), trades=%d (min %d), avg_pnl=%.2f",
                result.winner if result.winner else "unknown",
                winning_perf.win_rate if result.winner else 0.0,
                min_win_rate,
                winning_perf.total_trades if result.winner else 0,
                min_trades,
                winning_perf.avg_pnl if result.winner else 0.0,
            )

        return meets_criteria
