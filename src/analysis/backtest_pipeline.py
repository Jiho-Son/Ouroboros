"""Integrated v2 backtest pipeline.

Wires TripleBarrier labeling + WalkForward split + CostGuard validation
into a single deterministic orchestration path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Literal

from src.analysis.backtest_cost_guard import BacktestCostModel, validate_backtest_cost_model
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


@dataclass(frozen=True)
class BacktestFoldResult:
    fold_index: int
    train_indices: list[int]
    test_indices: list[int]
    train_label_distribution: dict[int, int]
    test_label_distribution: dict[int, int]
    baseline_scores: list[BaselineScore]


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
        resolved_timestamps = [ts for ts in timestamps if ts is not None]

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
        if not test_labels:
            continue
        fold_results.append(
            BacktestFoldResult(
                fold_index=fold_idx,
                train_indices=fold.train_indices,
                test_indices=fold.test_indices,
                train_label_distribution=_label_dist(train_labels),
                test_label_distribution=_label_dist(test_labels),
                baseline_scores=[
                    BaselineScore(name="B0", accuracy=_baseline_b0(train_labels, test_labels)),
                    BaselineScore(name="B1", accuracy=_score_constant(1, test_labels)),
                    BaselineScore(
                        name="M1",
                        accuracy=_score_constant(_m1_pred(train_labels), test_labels),
                    ),
                ],
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
    if not train_labels:
        return _score_constant(0, test_labels)
    # Majority-class baseline from training fold.
    choices = (-1, 0, 1)
    pred = max(choices, key=lambda c: train_labels.count(c))
    return _score_constant(pred, test_labels)


def _m1_pred(train_labels: Sequence[int]) -> int:
    if not train_labels:
        return 0
    return train_labels[-1]


def _build_run_id(*, n_entries: int, n_folds: int, sessions: Sequence[str]) -> str:
    sess_key = "_".join(sessions)
    return f"v2p-e{n_entries}-f{n_folds}-s{sess_key}"


def fold_has_leakage(fold: WalkForwardFold) -> bool:
    """Utility for tests/verification: True when train/test overlap exists."""
    return bool(set(fold.train_indices).intersection(fold.test_indices))
