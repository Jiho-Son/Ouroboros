from __future__ import annotations

import numpy as np
import pytest

from src.analysis.peak_probability_model import FeatureBar, FeatureBuilder, HistGBPeakModel
from src.analysis.walk_forward_split import generate_walk_forward_splits


def _make_bars(n: int = 30) -> list[FeatureBar]:
    """단조 증가하는 n개 바 생성."""
    bars = []
    for i in range(n):
        close = 100.0 + i
        bars.append(
            FeatureBar(high=close + 1.0, low=close - 1.0, close=close, volume=1000.0 + i * 10)
        )
    return bars


def test_feature_shape() -> None:
    bars = _make_bars(30)
    builder = FeatureBuilder(window=14)
    feat = builder.build(bars=bars, entry_index=20)
    assert feat.ndim == 1
    assert feat.shape[0] > 0


def test_feature_window_sealed() -> None:
    """entry_index 이후 데이터가 피처에 영향을 주지 않아야 한다."""
    bars = _make_bars(30)
    builder = FeatureBuilder(window=14)
    feat_original = builder.build(bars=bars, entry_index=20)

    bars_tampered = list(bars)
    for i in range(21, 30):
        b = bars_tampered[i]
        bars_tampered[i] = FeatureBar(
            high=b.high * 10, low=b.low * 10, close=b.close * 10, volume=b.volume * 10
        )

    feat_tampered = builder.build(bars=bars_tampered, entry_index=20)
    np.testing.assert_array_equal(feat_original, feat_tampered)


def test_requires_minimum_bars() -> None:
    bars = _make_bars(5)
    builder = FeatureBuilder(window=14)
    with pytest.raises(ValueError, match="insufficient"):
        builder.build(bars=bars, entry_index=4)


def test_no_nan_in_features() -> None:
    bars = _make_bars(30)
    builder = FeatureBuilder(window=14)
    feat = builder.build(bars=bars, entry_index=20)
    assert not np.any(np.isnan(feat))


def test_histgb_fit_predict() -> None:
    """fit 후 predict_proba가 [0, 1] 범위의 float를 반환해야 한다."""
    builder = FeatureBuilder(window=14)
    bars = _make_bars(50)
    entry_indices = list(range(20, 45))
    feat_matrix = np.stack([builder.build(bars=bars, entry_index=i) for i in entry_indices])
    y = np.array([1 if i % 2 == 0 else -1 for i in range(len(entry_indices))])

    model = HistGBPeakModel()
    model.fit(x=feat_matrix, y=y)

    prob = model.predict_proba(x=feat_matrix[:5])
    assert prob.shape == (5,)
    assert np.all(prob >= 0.0) and np.all(prob <= 1.0)


def test_histgb_not_fitted_raises() -> None:
    model = HistGBPeakModel()
    with pytest.raises(Exception):
        model.predict_proba(x=np.zeros((3, 7)))


def test_walk_forward_no_leakage() -> None:
    """Walk-forward fold에서 train에 없는 미래 인덱스가 test에만 있어야 한다."""
    folds = generate_walk_forward_splits(
        n_samples=30, train_size=10, test_size=5, purge_size=2, embargo_size=1
    )
    for fold in folds:
        train_set = set(fold.train_indices)
        test_set = set(fold.test_indices)
        assert train_set.isdisjoint(test_set), "train/test overlap detected"
        assert max(fold.train_indices) < min(fold.test_indices), "test must be strictly after train"
