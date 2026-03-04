from __future__ import annotations

import pytest

from src.analysis.peak_model_metrics import compute_pr_auc, compute_brier_score


def test_perfect_pr_auc() -> None:
    # 완벽한 예측: PR-AUC == 1.0
    y_true = [1, 1, 0, 0]
    y_prob = [0.9, 0.8, 0.2, 0.1]
    assert compute_pr_auc(y_true=y_true, y_prob=y_prob) == pytest.approx(1.0)


def test_random_brier_score() -> None:
    # 항상 0.5 예측: Brier == 0.25
    y_true = [1, 0, 1, 0]
    y_prob = [0.5, 0.5, 0.5, 0.5]
    assert compute_brier_score(y_true=y_true, y_prob=y_prob) == pytest.approx(0.25)


def test_perfect_brier_score() -> None:
    y_true = [1, 0]
    y_prob = [1.0, 0.0]
    assert compute_brier_score(y_true=y_true, y_prob=y_prob) == pytest.approx(0.0)


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        compute_pr_auc(y_true=[], y_prob=[])
