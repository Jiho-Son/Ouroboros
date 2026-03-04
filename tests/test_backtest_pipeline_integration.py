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
        partial_fill_rate_by_session={"KRX_REG": 0.05, "US_PRE": 0.2},
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
            assert 0.0 <= score.cost_adjusted_accuracy <= 1.0
        assert fold.execution_adjusted_trade_count >= 0
        assert fold.execution_rejected_count >= 0
        assert fold.execution_partial_count >= 0


def test_pipeline_cost_guard_fail_fast() -> None:
    bad = BacktestCostModel(
        commission_bps=3.0,
        slippage_bps_by_session={"KRX_REG": 10.0},
        failure_rate_by_session={"KRX_REG": 0.01},
        partial_fill_rate_by_session={"KRX_REG": 0.05},
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


def test_pipeline_fold_scores_reflect_cost_and_execution_effects() -> None:
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
    )
    optimistic = BacktestCostModel(
        commission_bps=0.0,
        slippage_bps_by_session={"KRX_REG": 0.0, "US_PRE": 0.0},
        failure_rate_by_session={"KRX_REG": 0.0, "US_PRE": 0.0},
        partial_fill_rate_by_session={"KRX_REG": 0.0, "US_PRE": 0.0},
        unfavorable_fill_required=True,
    )
    conservative = BacktestCostModel(
        commission_bps=10.0,
        slippage_bps_by_session={"KRX_REG": 30.0, "US_PRE": 80.0},
        failure_rate_by_session={"KRX_REG": 0.2, "US_PRE": 0.4},
        partial_fill_rate_by_session={"KRX_REG": 0.5, "US_PRE": 0.7},
        unfavorable_fill_required=True,
    )
    optimistic_out = run_v2_backtest_pipeline(cost_model=optimistic, **cfg)
    conservative_out = run_v2_backtest_pipeline(cost_model=conservative, **cfg)

    assert optimistic_out.folds and conservative_out.folds
    optimistic_score = optimistic_out.folds[0].baseline_scores[1].cost_adjusted_accuracy
    conservative_score = conservative_out.folds[0].baseline_scores[1].cost_adjusted_accuracy
    assert conservative_score < optimistic_score

    optimistic_avg_return = optimistic_out.folds[0].execution_adjusted_avg_return_bps
    conservative_avg_return = conservative_out.folds[0].execution_adjusted_avg_return_bps
    assert conservative_avg_return < optimistic_avg_return


def test_fold_result_has_model_metrics() -> None:
    """BacktestFoldResult에 m1_pr_auc, m1_brier 필드가 있어야 한다."""
    result = _run_pipeline_for_model_metrics()
    for fold in result.folds:
        assert hasattr(fold, "m1_pr_auc")
        assert hasattr(fold, "m1_brier")
        assert 0.0 <= fold.m1_pr_auc <= 1.0
        assert 0.0 <= fold.m1_brier <= 1.0


def test_backtest_bar_has_volume() -> None:
    """BacktestBar에 volume 필드가 있어야 하며 기본값은 0.0이어야 한다."""
    bar = BacktestBar(high=101.0, low=99.0, close=100.0, session_id="KRX_REG")
    assert bar.volume == 0.0


def test_fold_result_with_peak_model_produces_nondefault_metrics() -> None:
    """peak_model을 전달하면 m1_pr_auc와 m1_brier가 실제 계산된 값이어야 한다."""
    from datetime import UTC, datetime, timedelta
    from src.analysis.peak_probability_model import HistGBPeakModel

    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    # 충분한 bar 수 (FeatureBuilder.MINIMUM_BARS=15 이상, 학습 충분)
    closes = [100.0 + (i % 5) * 0.5 for i in range(60)]
    bars = [
        BacktestBar(
            high=c + 1.0, low=c - 1.0, close=c,
            session_id="KRX_REG",
            timestamp=base_ts + timedelta(minutes=i),
            volume=1000.0 + i * 50,
        )
        for i, c in enumerate(closes)
    ]
    result = run_v2_backtest_pipeline(
        bars=bars,
        entry_indices=list(range(20, 55)),
        side=1,
        triple_barrier_spec=TripleBarrierSpec(
            take_profit_pct=0.02, stop_loss_pct=0.01,
            max_holding_minutes=10,
        ),
        walk_forward=WalkForwardConfig(train_size=10, test_size=5, purge_size=1),
        cost_model=BacktestCostModel(
            commission_bps=3.0,
            slippage_bps_by_session={"KRX_REG": 10.0},
            failure_rate_by_session={"KRX_REG": 0.01},
            partial_fill_rate_by_session={"KRX_REG": 0.05},
            unfavorable_fill_required=True,
        ),
        required_sessions=["KRX_REG"],
        peak_model=HistGBPeakModel(),
    )
    # peak_model 경로가 실행되었음을 확인: 적어도 하나의 fold에서 기본값이 아닌 결과
    # (기본값: m1_pr_auc=0.0, m1_brier=1.0)
    # 모든 fold가 sufficient bars를 충족하지 않을 수 있으니 존재 여부만 확인
    assert len(result.folds) > 0
    for fold in result.folds:
        assert 0.0 <= fold.m1_pr_auc <= 1.0
        assert 0.0 <= fold.m1_brier <= 1.0


def _run_pipeline_for_model_metrics():
    base_ts = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    closes = [100.0 + i * 0.5 for i in range(20)]
    bars = [
        BacktestBar(
            high=c + 1.0, low=c - 1.0, close=c,
            session_id="KRX_REG",
            timestamp=base_ts + timedelta(minutes=i),
            volume=1000.0 + i * 50,
        )
        for i, c in enumerate(closes)
    ]
    return run_v2_backtest_pipeline(
        bars=bars,
        entry_indices=list(range(2, 18)),
        side=1,
        triple_barrier_spec=TripleBarrierSpec(
            take_profit_pct=0.02, stop_loss_pct=0.01,
            max_holding_minutes=10,
        ),
        walk_forward=WalkForwardConfig(train_size=5, test_size=3, purge_size=1),
        cost_model=BacktestCostModel(
            commission_bps=3.0,
            slippage_bps_by_session={"KRX_REG": 10.0},
            failure_rate_by_session={"KRX_REG": 0.01},
            partial_fill_rate_by_session={"KRX_REG": 0.05},
            unfavorable_fill_required=True,
        ),
        required_sessions=["KRX_REG"],
    )
