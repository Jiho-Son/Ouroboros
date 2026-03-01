from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.analysis.backtest_cost_guard import BacktestCostModel
from src.analysis.backtest_pipeline import (
    BacktestBar,
    WalkForwardConfig,
    fold_has_leakage,
    run_v2_backtest_pipeline,
)
from src.analysis.triple_barrier import TripleBarrierSpec
from src.analysis.walk_forward_split import generate_walk_forward_splits


def _bars() -> list[BacktestBar]:
    base_ts = datetime(2026, 2, 28, 0, 0, tzinfo=UTC)
    closes = [100.0, 101.0, 102.0, 101.5, 103.0, 102.5, 104.0, 103.5, 105.0, 104.5, 106.0, 105.5]
    bars: list[BacktestBar] = []
    for i, close in enumerate(closes):
        bars.append(
            BacktestBar(
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                session_id="KRX_REG" if i % 2 == 0 else "US_PRE",
                timestamp=base_ts + timedelta(minutes=i),
            )
        )
    return bars


def _cost_model() -> BacktestCostModel:
    return BacktestCostModel(
        commission_bps=3.0,
        slippage_bps_by_session={"KRX_REG": 10.0, "US_PRE": 50.0},
        failure_rate_by_session={"KRX_REG": 0.01, "US_PRE": 0.08},
        unfavorable_fill_required=True,
    )


def test_pipeline_happy_path_returns_fold_and_artifact_contract() -> None:
    out = run_v2_backtest_pipeline(
        bars=_bars(),
        entry_indices=[0, 1, 2, 3, 4, 5, 6, 7],
        side=1,
        triple_barrier_spec=TripleBarrierSpec(
            take_profit_pct=0.02,
            stop_loss_pct=0.01,
            max_holding_minutes=3,
        ),
        walk_forward=WalkForwardConfig(
            train_size=4,
            test_size=2,
            step_size=2,
            purge_size=1,
            embargo_size=1,
            min_train_size=3,
        ),
        cost_model=_cost_model(),
    )

    assert out.run_id.startswith("v2p-e8-f")
    assert out.n_bars == 12
    assert out.n_entries == 8
    assert out.required_sessions == ["KRX_REG", "US_PRE"]
    assert len(out.folds) > 0
    assert set(out.label_distribution) == {-1, 0, 1}
    for fold in out.folds:
        names = {score.name for score in fold.baseline_scores}
        assert names == {"B0", "B1", "M1"}
        for score in fold.baseline_scores:
            assert 0.0 <= score.accuracy <= 1.0


def test_pipeline_cost_guard_fail_fast() -> None:
    bad = BacktestCostModel(
        commission_bps=3.0,
        slippage_bps_by_session={"KRX_REG": 10.0},
        failure_rate_by_session={"KRX_REG": 0.01},
        unfavorable_fill_required=True,
    )
    try:
        run_v2_backtest_pipeline(
            bars=_bars(),
            entry_indices=[0, 1, 2, 3],
            side=1,
            triple_barrier_spec=TripleBarrierSpec(
                take_profit_pct=0.02,
                stop_loss_pct=0.01,
                max_holding_minutes=3,
            ),
            walk_forward=WalkForwardConfig(train_size=2, test_size=1),
            cost_model=bad,
            required_sessions=["KRX_REG", "US_PRE"],
        )
    except ValueError as exc:
        assert "missing slippage_bps_by_session" in str(exc)
    else:
        raise AssertionError("expected cost guard validation error")


def test_pipeline_fold_leakage_guard() -> None:
    folds = generate_walk_forward_splits(
        n_samples=12,
        train_size=6,
        test_size=2,
        step_size=2,
        purge_size=1,
        embargo_size=1,
        min_train_size=5,
    )
    assert folds
    for fold in folds:
        assert not fold_has_leakage(fold)


def test_pipeline_deterministic_seed_free_deterministic_result() -> None:
    cfg = dict(
        bars=_bars(),
        entry_indices=[0, 1, 2, 3, 4, 5, 6, 7],
        side=1,
        triple_barrier_spec=TripleBarrierSpec(
            take_profit_pct=0.02,
            stop_loss_pct=0.01,
            max_holding_minutes=3,
        ),
        walk_forward=WalkForwardConfig(
            train_size=4,
            test_size=2,
            step_size=2,
            purge_size=1,
            embargo_size=1,
            min_train_size=3,
        ),
        cost_model=_cost_model(),
    )
    out1 = run_v2_backtest_pipeline(**cfg)
    out2 = run_v2_backtest_pipeline(**cfg)
    assert out1 == out2


def test_pipeline_rejects_minutes_spec_when_timestamp_missing() -> None:
    bars = _bars()
    bars[2] = BacktestBar(
        high=bars[2].high,
        low=bars[2].low,
        close=bars[2].close,
        session_id=bars[2].session_id,
        timestamp=None,
    )
    try:
        run_v2_backtest_pipeline(
            bars=bars,
            entry_indices=[0, 1, 2, 3],
            side=1,
            triple_barrier_spec=TripleBarrierSpec(
                take_profit_pct=0.02,
                stop_loss_pct=0.01,
                max_holding_minutes=3,
            ),
            walk_forward=WalkForwardConfig(train_size=2, test_size=1),
            cost_model=_cost_model(),
        )
    except ValueError as exc:
        assert "BacktestBar.timestamp is required" in str(exc)
    else:
        raise AssertionError("expected timestamp validation error")
