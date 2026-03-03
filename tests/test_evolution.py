"""Tests for the Evolution Engine components.

Tests cover:
- EvolutionOptimizer: failure analysis and strategy generation
- ABTester: A/B testing and statistical comparison
- PerformanceTracker: metrics tracking and dashboard
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config import Settings
from src.db import init_db, log_trade
from src.evolution.ab_test import ABTester
from src.evolution.optimizer import EvolutionOptimizer
from src.evolution.performance_tracker import (
    PerformanceDashboard,
    PerformanceTracker,
    StrategyMetrics,
)
from src.logging.decision_logger import DecisionLogger

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Provide an in-memory database with initialized schema."""
    return init_db(":memory:")


@pytest.fixture
def settings() -> Settings:
    """Provide test settings."""
    return Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        GEMINI_MODEL="gemini-pro",
        DB_PATH=":memory:",
    )


@pytest.fixture
def optimizer(settings: Settings) -> EvolutionOptimizer:
    """Provide an EvolutionOptimizer instance."""
    return EvolutionOptimizer(settings)


@pytest.fixture
def decision_logger(db_conn: sqlite3.Connection) -> DecisionLogger:
    """Provide a DecisionLogger instance."""
    return DecisionLogger(db_conn)


@pytest.fixture
def ab_tester() -> ABTester:
    """Provide an ABTester instance."""
    return ABTester(significance_level=0.05)


@pytest.fixture
def performance_tracker(settings: Settings) -> PerformanceTracker:
    """Provide a PerformanceTracker instance."""
    return PerformanceTracker(db_path=":memory:")


# ------------------------------------------------------------------
# EvolutionOptimizer Tests
# ------------------------------------------------------------------


def test_analyze_failures_uses_decision_logger(optimizer: EvolutionOptimizer) -> None:
    """Test that analyze_failures uses DecisionLogger.get_losing_decisions()."""
    # Add some losing decisions to the database
    logger = optimizer._decision_logger

    # High-confidence loss
    id1 = logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=85,
        rationale="Expected growth",
        context_snapshot={"L1": {"price": 70000}},
        input_data={"price": 70000, "volume": 1000},
    )
    logger.update_outcome(id1, pnl=-2000.0, accuracy=0)

    # Another high-confidence loss
    id2 = logger.log_decision(
        stock_code="000660",
        market="KR",
        exchange_code="KRX",
        action="SELL",
        confidence=90,
        rationale="Expected drop",
        context_snapshot={"L1": {"price": 100000}},
        input_data={"price": 100000, "volume": 500},
    )
    logger.update_outcome(id2, pnl=-1500.0, accuracy=0)

    # Low-confidence loss (should be ignored)
    id3 = logger.log_decision(
        stock_code="035420",
        market="KR",
        exchange_code="KRX",
        action="HOLD",
        confidence=70,
        rationale="Uncertain",
        context_snapshot={},
        input_data={},
    )
    logger.update_outcome(id3, pnl=-500.0, accuracy=0)

    # Analyze failures
    failures = optimizer.analyze_failures(limit=10)

    # Should get 2 failures (confidence >= 80)
    assert len(failures) == 2
    assert all(f["confidence"] >= 80 for f in failures)
    assert all(f["outcome_pnl"] <= -100.0 for f in failures)


def test_analyze_failures_empty_database(optimizer: EvolutionOptimizer) -> None:
    """Test analyze_failures with no losing decisions."""
    failures = optimizer.analyze_failures()
    assert failures == []


def test_identify_failure_patterns(optimizer: EvolutionOptimizer) -> None:
    """Test identification of failure patterns."""
    failures = [
        {
            "decision_id": "1",
            "timestamp": "2024-01-15T09:30:00+00:00",
            "stock_code": "005930",
            "market": "KR",
            "exchange_code": "KRX",
            "action": "BUY",
            "confidence": 85,
            "rationale": "Test",
            "outcome_pnl": -1000.0,
            "outcome_accuracy": 0,
            "context_snapshot": {},
            "input_data": {},
        },
        {
            "decision_id": "2",
            "timestamp": "2024-01-15T14:30:00+00:00",
            "stock_code": "000660",
            "market": "KR",
            "exchange_code": "KRX",
            "action": "SELL",
            "confidence": 90,
            "rationale": "Test",
            "outcome_pnl": -2000.0,
            "outcome_accuracy": 0,
            "context_snapshot": {},
            "input_data": {},
        },
        {
            "decision_id": "3",
            "timestamp": "2024-01-15T09:45:00+00:00",
            "stock_code": "035420",
            "market": "US_NASDAQ",
            "exchange_code": "NASDAQ",
            "action": "BUY",
            "confidence": 80,
            "rationale": "Test",
            "outcome_pnl": -500.0,
            "outcome_accuracy": 0,
            "context_snapshot": {},
            "input_data": {},
        },
    ]

    patterns = optimizer.identify_failure_patterns(failures)

    assert patterns["total_failures"] == 3
    assert patterns["markets"]["KR"] == 2
    assert patterns["markets"]["US_NASDAQ"] == 1
    assert patterns["actions"]["BUY"] == 2
    assert patterns["actions"]["SELL"] == 1
    assert 9 in patterns["hours"]  # 09:30 and 09:45
    assert 14 in patterns["hours"]  # 14:30
    assert patterns["avg_confidence"] == 85.0
    assert patterns["avg_loss"] == -1166.67


def test_identify_failure_patterns_empty(optimizer: EvolutionOptimizer) -> None:
    """Test pattern identification with no failures."""
    patterns = optimizer.identify_failure_patterns([])
    assert patterns["pattern_count"] == 0
    assert patterns["patterns"] == {}


@pytest.mark.asyncio
async def test_generate_strategy_creates_file(
    optimizer: EvolutionOptimizer, tmp_path: Path
) -> None:
    """Test that generate_strategy creates a strategy file."""
    failures = [
        {
            "decision_id": "1",
            "timestamp": "2024-01-15T09:30:00+00:00",
            "stock_code": "005930",
            "market": "KR",
            "action": "BUY",
            "confidence": 85,
            "outcome_pnl": -1000.0,
            "context_snapshot": {},
            "input_data": {},
        }
    ]

    # Mock Gemini response
    mock_response = Mock()
    mock_response.text = """
    # Simple strategy
    price = market_data.get("current_price", 0)
    if price > 50000:
        return {"action": "BUY", "confidence": 70, "rationale": "Price above threshold"}
    return {"action": "HOLD", "confidence": 50, "rationale": "Waiting"}
    """

    with patch.object(
        optimizer._client.aio.models, "generate_content", new=AsyncMock(return_value=mock_response)
    ):
        with patch("src.evolution.optimizer.STRATEGIES_DIR", tmp_path):
            strategy_path = await optimizer.generate_strategy(failures)

    assert strategy_path is not None
    assert strategy_path.exists()
    assert strategy_path.suffix == ".py"
    assert "class Strategy_" in strategy_path.read_text()
    assert "def evaluate" in strategy_path.read_text()


@pytest.mark.asyncio
async def test_generate_strategy_saves_valid_python_code(
    optimizer: EvolutionOptimizer,
    tmp_path: Path,
) -> None:
    """Test that syntactically valid generated code is saved."""
    failures = [{"decision_id": "1", "timestamp": "2024-01-15T09:30:00+00:00"}]

    mock_response = Mock()
    mock_response.text = (
        'price = market_data.get("current_price", 0)\n'
        "if price > 0:\n"
        '    return {"action": "BUY", "confidence": 80, "rationale": "Positive price"}\n'
        'return {"action": "HOLD", "confidence": 50, "rationale": "No signal"}\n'
    )

    with patch.object(
        optimizer._client.aio.models, "generate_content", new=AsyncMock(return_value=mock_response)
    ):
        with patch("src.evolution.optimizer.STRATEGIES_DIR", tmp_path):
            strategy_path = await optimizer.generate_strategy(failures)

    assert strategy_path is not None
    assert strategy_path.exists()


@pytest.mark.asyncio
async def test_generate_strategy_blocks_invalid_python_code(
    optimizer: EvolutionOptimizer,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that syntactically invalid generated code is not saved."""
    failures = [{"decision_id": "1", "timestamp": "2024-01-15T09:30:00+00:00"}]

    mock_response = Mock()
    mock_response.text = (
        'if market_data.get("current_price", 0) > 0\n'
        '    return {"action": "BUY", "confidence": 80, "rationale": "broken"}\n'
    )

    with patch.object(
        optimizer._client.aio.models, "generate_content", new=AsyncMock(return_value=mock_response)
    ):
        with patch("src.evolution.optimizer.STRATEGIES_DIR", tmp_path):
            with caplog.at_level("WARNING"):
                strategy_path = await optimizer.generate_strategy(failures)

    assert strategy_path is None
    assert list(tmp_path.glob("*.py")) == []
    assert "failed syntax validation" in caplog.text


@pytest.mark.asyncio
async def test_generate_strategy_handles_api_error(optimizer: EvolutionOptimizer) -> None:
    """Test that generate_strategy handles Gemini API errors gracefully."""
    failures = [{"decision_id": "1", "timestamp": "2024-01-15T09:30:00+00:00"}]

    with patch.object(
        optimizer._client.aio.models,
        "generate_content",
        side_effect=Exception("API Error"),
    ):
        strategy_path = await optimizer.generate_strategy(failures)

    assert strategy_path is None


def test_get_performance_summary() -> None:
    """Test getting performance summary from trades table."""
    # Create a temporary database with trades
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    conn = init_db(tmp_path)
    log_trade(conn, "005930", "BUY", 85, "Test win", quantity=10, price=70000, pnl=1000.0)
    log_trade(conn, "000660", "SELL", 90, "Test loss", quantity=5, price=100000, pnl=-500.0)
    log_trade(conn, "035420", "BUY", 80, "Test win", quantity=8, price=50000, pnl=800.0)
    conn.close()

    # Create settings with temp database path
    settings = Settings(
        KIS_APP_KEY="test_key",
        KIS_APP_SECRET="test_secret",
        KIS_ACCOUNT_NO="12345678-01",
        GEMINI_API_KEY="test_gemini_key",
        GEMINI_MODEL="gemini-pro",
        DB_PATH=tmp_path,
    )

    optimizer = EvolutionOptimizer(settings)
    summary = optimizer.get_performance_summary()

    assert summary["total_trades"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["total_pnl"] == 1300.0
    assert summary["avg_pnl"] == 433.33

    # Clean up
    Path(tmp_path).unlink()


def test_validate_strategy_success(optimizer: EvolutionOptimizer, tmp_path: Path) -> None:
    """Test strategy validation when tests pass."""
    strategy_file = tmp_path / "test_strategy.py"
    strategy_file.write_text("# Valid strategy file")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        result = optimizer.validate_strategy(strategy_file)

    assert result is True
    assert strategy_file.exists()


def test_validate_strategy_failure(optimizer: EvolutionOptimizer, tmp_path: Path) -> None:
    """Test strategy validation when tests fail."""
    strategy_file = tmp_path / "test_strategy.py"
    strategy_file.write_text("# Invalid strategy file")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=1, stdout="FAILED", stderr="")
        result = optimizer.validate_strategy(strategy_file)

    assert result is False
    # File should be deleted on failure
    assert not strategy_file.exists()


# ------------------------------------------------------------------
# ABTester Tests
# ------------------------------------------------------------------


def test_calculate_performance_basic(ab_tester: ABTester) -> None:
    """Test basic performance calculation."""
    trades = [
        {"pnl": 1000.0},
        {"pnl": -500.0},
        {"pnl": 800.0},
        {"pnl": 200.0},
    ]

    perf = ab_tester.calculate_performance(trades, "TestStrategy")

    assert perf.strategy_name == "TestStrategy"
    assert perf.total_trades == 4
    assert perf.wins == 3
    assert perf.losses == 1
    assert perf.total_pnl == 1500.0
    assert perf.avg_pnl == 375.0
    assert perf.win_rate == 75.0
    assert perf.sharpe_ratio is not None


def test_calculate_performance_empty(ab_tester: ABTester) -> None:
    """Test performance calculation with no trades."""
    perf = ab_tester.calculate_performance([], "EmptyStrategy")

    assert perf.total_trades == 0
    assert perf.wins == 0
    assert perf.losses == 0
    assert perf.total_pnl == 0.0
    assert perf.avg_pnl == 0.0
    assert perf.win_rate == 0.0
    assert perf.sharpe_ratio is None


def test_compare_strategies_significant_difference(ab_tester: ABTester) -> None:
    """Test strategy comparison with significant performance difference."""
    # Strategy A: consistently profitable
    trades_a = [{"pnl": 1000.0} for _ in range(30)]

    # Strategy B: consistently losing
    trades_b = [{"pnl": -500.0} for _ in range(30)]

    result = ab_tester.compare_strategies(trades_a, trades_b, "Strategy A", "Strategy B")

    # scipy returns np.True_ instead of Python bool
    assert bool(result.is_significant) is True
    assert result.winner == "Strategy A"
    assert result.p_value < 0.05
    assert result.performance_a.avg_pnl > result.performance_b.avg_pnl


def test_compare_strategies_no_difference(ab_tester: ABTester) -> None:
    """Test strategy comparison with no significant difference."""
    # Both strategies have similar performance
    trades_a = [{"pnl": 100.0}, {"pnl": -50.0}, {"pnl": 80.0}]
    trades_b = [{"pnl": 90.0}, {"pnl": -60.0}, {"pnl": 85.0}]

    result = ab_tester.compare_strategies(trades_a, trades_b, "Strategy A", "Strategy B")

    # With small samples and similar performance, likely not significant
    assert result.winner is None or not result.is_significant


def test_should_deploy_meets_criteria(ab_tester: ABTester) -> None:
    """Test deployment decision when criteria are met."""
    # Create a winning result that meets criteria
    trades_a = [{"pnl": 1000.0} for _ in range(25)]  # 100% win rate
    trades_b = [{"pnl": -500.0} for _ in range(25)]

    result = ab_tester.compare_strategies(trades_a, trades_b, "Winner", "Loser")

    should_deploy = ab_tester.should_deploy(result, min_win_rate=60.0, min_trades=20)

    assert should_deploy is True


def test_should_deploy_insufficient_trades(ab_tester: ABTester) -> None:
    """Test deployment decision with insufficient trades."""
    trades_a = [{"pnl": 1000.0} for _ in range(10)]  # Only 10 trades
    trades_b = [{"pnl": -500.0} for _ in range(10)]

    result = ab_tester.compare_strategies(trades_a, trades_b, "Winner", "Loser")

    should_deploy = ab_tester.should_deploy(result, min_win_rate=60.0, min_trades=20)

    assert should_deploy is False


def test_should_deploy_low_win_rate(ab_tester: ABTester) -> None:
    """Test deployment decision with low win rate."""
    # Mix of wins and losses, below 60% win rate
    trades_a = [{"pnl": 100.0}] * 10 + [{"pnl": -100.0}] * 15  # 40% win rate
    trades_b = [{"pnl": -500.0} for _ in range(25)]

    result = ab_tester.compare_strategies(trades_a, trades_b, "LowWinner", "Loser")

    should_deploy = ab_tester.should_deploy(result, min_win_rate=60.0, min_trades=20)

    assert should_deploy is False


def test_should_deploy_not_significant(ab_tester: ABTester) -> None:
    """Test deployment decision when difference is not significant."""
    # Use more varied data to ensure statistical insignificance
    trades_a = [{"pnl": 100.0}, {"pnl": -50.0}] * 12 + [{"pnl": 100.0}]
    trades_b = [{"pnl": 95.0}, {"pnl": -45.0}] * 12 + [{"pnl": 95.0}]

    result = ab_tester.compare_strategies(trades_a, trades_b, "A", "B")

    should_deploy = ab_tester.should_deploy(result, min_win_rate=60.0, min_trades=20)

    # Not significant or not profitable enough
    # Even if significant, win rate is 50% which is below 60% threshold
    assert should_deploy is False


# ------------------------------------------------------------------
# PerformanceTracker Tests
# ------------------------------------------------------------------


def test_get_strategy_metrics(db_conn: sqlite3.Connection) -> None:
    """Test getting strategy metrics."""
    # Add some trades
    log_trade(db_conn, "005930", "BUY", 85, "Win 1", quantity=10, price=70000, pnl=1000.0)
    log_trade(db_conn, "000660", "SELL", 90, "Loss 1", quantity=5, price=100000, pnl=-500.0)
    log_trade(db_conn, "035420", "BUY", 80, "Win 2", quantity=8, price=50000, pnl=800.0)
    log_trade(db_conn, "005930", "HOLD", 75, "Hold", quantity=0, price=70000, pnl=0.0)

    tracker = PerformanceTracker(db_path=":memory:")
    # Manually set connection for testing
    tracker._db_path = db_conn

    # Need to use the same connection
    with patch("sqlite3.connect", return_value=db_conn):
        metrics = tracker.get_strategy_metrics()

    assert metrics.total_trades == 4
    assert metrics.wins == 2
    assert metrics.losses == 1
    assert metrics.holds == 1
    assert metrics.win_rate == 50.0
    assert metrics.total_pnl == 1300.0


def test_calculate_improvement_trend_improving(performance_tracker: PerformanceTracker) -> None:
    """Test improvement trend calculation for improving strategy."""
    metrics = [
        StrategyMetrics(
            strategy_name="test",
            period_start="2024-01-01",
            period_end="2024-01-07",
            total_trades=10,
            wins=5,
            losses=5,
            holds=0,
            win_rate=50.0,
            avg_pnl=100.0,
            total_pnl=1000.0,
            best_trade=500.0,
            worst_trade=-300.0,
            avg_confidence=75.0,
        ),
        StrategyMetrics(
            strategy_name="test",
            period_start="2024-01-08",
            period_end="2024-01-14",
            total_trades=10,
            wins=7,
            losses=3,
            holds=0,
            win_rate=70.0,
            avg_pnl=200.0,
            total_pnl=2000.0,
            best_trade=600.0,
            worst_trade=-200.0,
            avg_confidence=80.0,
        ),
    ]

    trend = performance_tracker.calculate_improvement_trend(metrics)

    assert trend["trend"] == "improving"
    assert trend["win_rate_change"] == 20.0
    assert trend["pnl_change"] == 100.0
    assert trend["confidence_change"] == 5.0


def test_calculate_improvement_trend_declining(performance_tracker: PerformanceTracker) -> None:
    """Test improvement trend calculation for declining strategy."""
    metrics = [
        StrategyMetrics(
            strategy_name="test",
            period_start="2024-01-01",
            period_end="2024-01-07",
            total_trades=10,
            wins=7,
            losses=3,
            holds=0,
            win_rate=70.0,
            avg_pnl=200.0,
            total_pnl=2000.0,
            best_trade=600.0,
            worst_trade=-200.0,
            avg_confidence=80.0,
        ),
        StrategyMetrics(
            strategy_name="test",
            period_start="2024-01-08",
            period_end="2024-01-14",
            total_trades=10,
            wins=4,
            losses=6,
            holds=0,
            win_rate=40.0,
            avg_pnl=-50.0,
            total_pnl=-500.0,
            best_trade=300.0,
            worst_trade=-400.0,
            avg_confidence=70.0,
        ),
    ]

    trend = performance_tracker.calculate_improvement_trend(metrics)

    assert trend["trend"] == "declining"
    assert trend["win_rate_change"] == -30.0
    assert trend["pnl_change"] == -250.0


def test_calculate_improvement_trend_insufficient_data(
    performance_tracker: PerformanceTracker,
) -> None:
    """Test improvement trend with insufficient data."""
    metrics = [
        StrategyMetrics(
            strategy_name="test",
            period_start="2024-01-01",
            period_end="2024-01-07",
            total_trades=10,
            wins=5,
            losses=5,
            holds=0,
            win_rate=50.0,
            avg_pnl=100.0,
            total_pnl=1000.0,
            best_trade=500.0,
            worst_trade=-300.0,
            avg_confidence=75.0,
        )
    ]

    trend = performance_tracker.calculate_improvement_trend(metrics)

    assert trend["trend"] == "insufficient_data"
    assert trend["win_rate_change"] == 0.0
    assert trend["pnl_change"] == 0.0


def test_export_dashboard_json(performance_tracker: PerformanceTracker) -> None:
    """Test exporting dashboard as JSON."""
    overall_metrics = StrategyMetrics(
        strategy_name="test",
        period_start="2024-01-01",
        period_end="2024-01-31",
        total_trades=100,
        wins=60,
        losses=40,
        holds=10,
        win_rate=60.0,
        avg_pnl=150.0,
        total_pnl=15000.0,
        best_trade=1000.0,
        worst_trade=-500.0,
        avg_confidence=80.0,
    )

    dashboard = PerformanceDashboard(
        generated_at=datetime.now(UTC).isoformat(),
        overall_metrics=overall_metrics,
        daily_metrics=[],
        weekly_metrics=[],
        improvement_trend={"trend": "improving", "win_rate_change": 10.0},
    )

    json_output = performance_tracker.export_dashboard_json(dashboard)

    # Verify it's valid JSON
    data = json.loads(json_output)
    assert "generated_at" in data
    assert "overall_metrics" in data
    assert data["overall_metrics"]["total_trades"] == 100
    assert data["overall_metrics"]["win_rate"] == 60.0


def test_generate_dashboard() -> None:
    """Test generating a complete dashboard."""
    # Create tracker with temp database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    # Initialize with data
    conn = init_db(tmp_path)
    log_trade(conn, "005930", "BUY", 85, "Win", quantity=10, price=70000, pnl=1000.0)
    log_trade(conn, "000660", "SELL", 90, "Loss", quantity=5, price=100000, pnl=-500.0)
    conn.close()

    tracker = PerformanceTracker(db_path=tmp_path)
    dashboard = tracker.generate_dashboard()

    assert isinstance(dashboard, PerformanceDashboard)
    assert dashboard.overall_metrics.total_trades == 2
    assert len(dashboard.daily_metrics) == 7
    assert len(dashboard.weekly_metrics) == 4
    assert "trend" in dashboard.improvement_trend

    # Clean up
    Path(tmp_path).unlink()


# ------------------------------------------------------------------
# Integration Tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_evolution_pipeline(optimizer: EvolutionOptimizer, tmp_path: Path) -> None:
    """Test the complete evolution pipeline."""
    # Add losing decisions
    logger = optimizer._decision_logger
    id1 = logger.log_decision(
        stock_code="005930",
        market="KR",
        exchange_code="KRX",
        action="BUY",
        confidence=85,
        rationale="Expected growth",
        context_snapshot={},
        input_data={},
    )
    logger.update_outcome(id1, pnl=-2000.0, accuracy=0)

    # Mock Gemini and subprocess
    mock_response = Mock()
    mock_response.text = 'return {"action": "HOLD", "confidence": 50, "rationale": "Test"}'

    with patch.object(
        optimizer._client.aio.models, "generate_content", new=AsyncMock(return_value=mock_response)
    ):
        with patch("src.evolution.optimizer.STRATEGIES_DIR", tmp_path):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout="", stderr="")

                result = await optimizer.evolve()

    assert result is not None
    assert "title" in result
    assert "branch" in result
    assert "status" in result
