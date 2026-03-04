"""Prediction quality metrics for peak probability model evaluation."""

from __future__ import annotations

from collections.abc import Sequence

from sklearn.metrics import average_precision_score, brier_score_loss


def compute_pr_auc(*, y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    """Compute Precision-Recall AUC (average precision).

    Preferred over ROC-AUC for imbalanced labels.
    """
    if not y_true:
        raise ValueError("y_true must not be empty")
    return float(average_precision_score(list(y_true), list(y_prob)))


def compute_brier_score(*, y_true: Sequence[int], y_prob: Sequence[float]) -> float:
    """Compute Brier score (lower is better, 0.0 = perfect calibration)."""
    if not y_true:
        raise ValueError("y_true must not be empty")
    return float(brier_score_loss(list(y_true), list(y_prob)))
