"""Integrated v2 backtest pipeline.

Wires TripleBarrier labeling + WalkForward split + CostGuard validation
into a single deterministic orchestration path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Literal, cast

from src.analysis.backtest_cost_guard import BacktestCostModel, validate_backtest_cost_model
from src.analysis.backtest_execution_model import (
    BacktestExecutionModel,
    ExecutionAssumptions,
    ExecutionRequest,
)
from src.analysis.triple_barrier import TripleBarrierSpec, label_with_triple_barrier
from src.analysis.walk_forward_split import WalkForwardFold, generate_walk_forward_splits


@dataclass(frozen=True)
class BacktestBar:
    high: float
    low: float
    close: float
    session_id: str
    timestamp: datetime | None = None


@dataclass(frozen=True)
class WalkForwardConfig:
    train_size: int
    test_size: int
    step_size: int | None = None
    purge_size: int = 0
    embargo_size: int = 0
    min_train_size: int = 1


@dataclass(frozen=True)
class BaselineScore:
    name: Literal["B0", "B1", "M1"]
    accuracy: float
    cost_adjusted_accuracy: float


@dataclass(frozen=True)
class BacktestFoldResult:
    fold_index: int
    train_indices: list[int]
    test_indices: list[int]
    train_label_distribution: dict[int, int]
    test_label_distribution: dict[int, int]
    baseline_scores: list[BaselineScore]
    execution_adjusted_avg_return_bps: float
    execution_adjusted_trade_count: int
    execution_rejected_count: int
    execution_partial_count: int


@dataclass(frozen=True)
class BacktestPipelineResult:
    run_id: str
    n_bars: int
    n_entries: int
    required_sessions: list[str]
    label_distribution: dict[int, int]
    folds: list[BacktestFoldResult]


def run_v2_backtest_pipeline(
    *,
    bars: Sequence[BacktestBar],
    entry_indices: Sequence[int],
    side: int,
    triple_barrier_spec: TripleBarrierSpec,
    walk_forward: WalkForwardConfig,
    cost_model: BacktestCostModel,
    required_sessions: list[str] | None = None,
) -> BacktestPipelineResult:
    """Run v2 integrated pipeline (cost guard -> labels -> walk-forward baselines)."""
    if not bars:
        raise ValueError("bars must not be empty")
    if not entry_indices:
        raise ValueError("entry_indices must not be empty")

    resolved_sessions = (
        sorted(set(required_sessions))
        if required_sessions is not None
        else sorted({bar.session_id for bar in bars})
    )
    validate_backtest_cost_model(model=cost_model, required_sessions=resolved_sessions)
    execution_model = BacktestExecutionModel(
        ExecutionAssumptions(
            slippage_bps_by_session=cost_model.slippage_bps_by_session or {},
            failure_rate_by_session=cost_model.failure_rate_by_session or {},
            partial_fill_rate_by_session=cost_model.partial_fill_rate_by_session or {},
            seed=0,
        )
    )

    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    closes = [float(bar.close) for bar in bars]
    timestamps = [bar.timestamp for bar in bars]
    normalized_entries = sorted(set(int(i) for i in entry_indices))
    if normalized_entries[0] < 0 or normalized_entries[-1] >= len(bars):
        raise IndexError("entry index out of range")

    resolved_timestamps: list[datetime] | None = None
    if triple_barrier_spec.max_holding_minutes is not None:
        if any(ts is None for ts in timestamps):
            raise ValueError(
                "BacktestBar.timestamp is required for all bars when "
                "triple_barrier_spec.max_holding_minutes is set"
            )
        resolved_timestamps = cast(list[datetime], timestamps)

    labels_by_bar_index: dict[int, int] = {}
    for idx in normalized_entries:
        labels_by_bar_index[idx] = label_with_triple_barrier(
            highs=highs,
            lows=lows,
            closes=closes,
            timestamps=resolved_timestamps,
            entry_index=idx,
            side=side,
            spec=triple_barrier_spec,
        ).label

    ordered_labels = [labels_by_bar_index[idx] for idx in normalized_entries]
    ordered_sessions = [bars[idx].session_id for idx in normalized_entries]
    ordered_prices = [bars[idx].close for idx in normalized_entries]
    folds = generate_walk_forward_splits(
        n_samples=len(normalized_entries),
        train_size=walk_forward.train_size,
        test_size=walk_forward.test_size,
        step_size=walk_forward.step_size,
        purge_size=walk_forward.purge_size,
        embargo_size=walk_forward.embargo_size,
        min_train_size=walk_forward.min_train_size,
    )

    fold_results: list[BacktestFoldResult] = []
    for fold_idx, fold in enumerate(folds):
        train_labels = [ordered_labels[i] for i in fold.train_indices]
        test_labels = [ordered_labels[i] for i in fold.test_indices]
        test_sessions = [ordered_sessions[i] for i in fold.test_indices]
        test_prices = [ordered_prices[i] for i in fold.test_indices]
        if not test_labels:
            continue
        execution_model = _build_execution_model(cost_model=cost_model, fold_seed=fold_idx)
        execution_return_model = _build_execution_model(
            cost_model=cost_model,
            fold_seed=fold_idx,
        )
        b0_pred = _baseline_b0_pred(train_labels)
        m1_pred = _m1_pred(train_labels)
        execution_returns_bps: list[float] = []
        execution_rejected = 0
        execution_partial = 0
        for rel_idx in fold.test_indices:
            entry_bar_index = normalized_entries[rel_idx]
            bar = bars[entry_bar_index]
            trade = _simulate_execution_adjusted_return_bps(
                execution_model=execution_return_model,
                bar=bar,
                label=ordered_labels[rel_idx],
                side=side,
                spec=triple_barrier_spec,
                commission_bps=float(cost_model.commission_bps or 0.0),
            )
            if trade["status"] == "REJECTED":
                execution_rejected += 1
                continue
            execution_returns_bps.append(float(trade["return_bps"]))
            if trade["status"] == "PARTIAL":
                execution_partial += 1
        fold_results.append(
            BacktestFoldResult(
                fold_index=fold_idx,
                train_indices=fold.train_indices,
                test_indices=fold.test_indices,
                train_label_distribution=_label_dist(train_labels),
                test_label_distribution=_label_dist(test_labels),
                baseline_scores=[
                    BaselineScore(
                        name="B0",
                        accuracy=_score_constant(b0_pred, test_labels),
                        cost_adjusted_accuracy=_score_with_execution(
                            prediction=b0_pred,
                            actual=test_labels,
                            sessions=test_sessions,
                            reference_prices=test_prices,
                            execution_model=execution_model,
                            commission_bps=float(cost_model.commission_bps or 0.0),
                        ),
                    ),
                    BaselineScore(
                        name="B1",
                        accuracy=_score_constant(1, test_labels),
                        cost_adjusted_accuracy=_score_with_execution(
                            prediction=1,
                            actual=test_labels,
                            sessions=test_sessions,
                            reference_prices=test_prices,
                            execution_model=execution_model,
                            commission_bps=float(cost_model.commission_bps or 0.0),
                        ),
                    ),
                    BaselineScore(
                        name="M1",
                        accuracy=_score_constant(m1_pred, test_labels),
                        cost_adjusted_accuracy=_score_with_execution(
                            prediction=m1_pred,
                            actual=test_labels,
                            sessions=test_sessions,
                            reference_prices=test_prices,
                            execution_model=execution_model,
                            commission_bps=float(cost_model.commission_bps or 0.0),
                        ),
                    ),
                ],
                execution_adjusted_avg_return_bps=(
                    mean(execution_returns_bps) if execution_returns_bps else 0.0
                ),
                execution_adjusted_trade_count=len(execution_returns_bps),
                execution_rejected_count=execution_rejected,
                execution_partial_count=execution_partial,
            )
        )

    return BacktestPipelineResult(
        run_id=_build_run_id(
            n_entries=len(normalized_entries),
            n_folds=len(fold_results),
            sessions=resolved_sessions,
        ),
        n_bars=len(bars),
        n_entries=len(normalized_entries),
        required_sessions=resolved_sessions,
        label_distribution=_label_dist(ordered_labels),
        folds=fold_results,
    )


def _label_dist(labels: Sequence[int]) -> dict[int, int]:
    dist: dict[int, int] = {-1: 0, 0: 0, 1: 0}
    for val in labels:
        if val in dist:
            dist[val] += 1
    return dist


def _score_constant(pred: int, actual: Sequence[int]) -> float:
    return mean(1.0 if pred == label else 0.0 for label in actual)


def _baseline_b0(train_labels: Sequence[int], test_labels: Sequence[int]) -> float:
    return _score_constant(_baseline_b0_pred(train_labels), test_labels)


def _baseline_b0_pred(train_labels: Sequence[int]) -> int:
    if not train_labels:
        return 0
    # Majority-class baseline from training fold.
    choices = (-1, 0, 1)
    return max(choices, key=lambda c: train_labels.count(c))


def _m1_pred(train_labels: Sequence[int]) -> int:
    if not train_labels:
        return 0
    return train_labels[-1]


def _build_execution_model(
    *,
    cost_model: BacktestCostModel,
    fold_seed: int,
) -> BacktestExecutionModel:
    return BacktestExecutionModel(
        ExecutionAssumptions(
            slippage_bps_by_session=dict(cost_model.slippage_bps_by_session or {}),
            failure_rate_by_session=dict(cost_model.failure_rate_by_session or {}),
            partial_fill_rate_by_session=dict(cost_model.partial_fill_rate_by_session or {}),
            seed=fold_seed,
        )
    )


def _score_with_execution(
    *,
    prediction: int,
    actual: Sequence[int],
    sessions: Sequence[str],
    reference_prices: Sequence[float],
    execution_model: BacktestExecutionModel,
    commission_bps: float,
) -> float:
    if not actual:
        return 0.0
    contributions: list[float] = []
    for label, session_id, reference_price in zip(actual, sessions, reference_prices, strict=True):
        if prediction == 0:
            contributions.append(1.0 if label == 0 else 0.0)
            continue
        side = "BUY" if prediction > 0 else "SELL"
        execution = execution_model.simulate(
            ExecutionRequest(
                side=side,
                session_id=session_id,
                qty=100,
                reference_price=reference_price,
            )
        )
        if execution.status == "REJECTED":
            contributions.append(0.0)
            continue
        fill_ratio = execution.filled_qty / 100.0
        cost_penalty = min(0.99, (commission_bps + execution.slippage_bps) / 10000.0)
        correctness = 1.0 if prediction == label else 0.0
        contributions.append(correctness * fill_ratio * (1.0 - cost_penalty))
    return mean(contributions)


def _build_run_id(*, n_entries: int, n_folds: int, sessions: Sequence[str]) -> str:
    sess_key = "_".join(sessions)
    return f"v2p-e{n_entries}-f{n_folds}-s{sess_key}"


def fold_has_leakage(fold: WalkForwardFold) -> bool:
    """Utility for tests/verification: True when train/test overlap exists."""
    return bool(set(fold.train_indices).intersection(fold.test_indices))


def _simulate_execution_adjusted_return_bps(
    *,
    execution_model: BacktestExecutionModel,
    bar: BacktestBar,
    label: int,
    side: int,
    spec: TripleBarrierSpec,
    commission_bps: float,
) -> dict[str, float | str]:
    qty = 100
    entry_req = ExecutionRequest(
        side="BUY" if side == 1 else "SELL",
        session_id=bar.session_id,
        qty=qty,
        reference_price=float(bar.close),
    )
    entry_fill = execution_model.simulate(entry_req)
    if entry_fill.status == "REJECTED":
        return {"status": "REJECTED", "return_bps": 0.0}

    exit_qty = entry_fill.filled_qty
    if label == 1:
        gross_return_bps = spec.take_profit_pct * 10000.0
    elif label == -1:
        gross_return_bps = -spec.stop_loss_pct * 10000.0
    else:
        gross_return_bps = 0.0

    if side == 1:
        exit_price = float(bar.close) * (1.0 + gross_return_bps / 10000.0)
    else:
        exit_price = float(bar.close) * (1.0 - gross_return_bps / 10000.0)

    exit_req = ExecutionRequest(
        side="SELL" if side == 1 else "BUY",
        session_id=bar.session_id,
        qty=exit_qty,
        reference_price=max(0.01, exit_price),
    )
    exit_fill = execution_model.simulate(exit_req)
    if exit_fill.status == "REJECTED":
        return {"status": "REJECTED", "return_bps": 0.0}

    fill_ratio = min(entry_fill.filled_qty, exit_fill.filled_qty) / qty
    cost_bps = (
        float(entry_fill.slippage_bps)
        + float(exit_fill.slippage_bps)
        + (2.0 * float(commission_bps))
    )
    net_return_bps = (gross_return_bps * fill_ratio) - cost_bps
    is_partial = entry_fill.status == "PARTIAL" or exit_fill.status == "PARTIAL"
    status = "PARTIAL" if is_partial else "FILLED"
    return {"status": status, "return_bps": net_return_bps}
