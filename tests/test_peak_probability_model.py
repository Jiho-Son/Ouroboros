from __future__ import annotations

import pytest
import numpy as np

from src.analysis.peak_probability_model import FeatureBuilder, FeatureBar


def _make_bars(n: int = 30) -> list[FeatureBar]:
    """단조 증가하는 n개 바 생성."""
    bars = []
    for i in range(n):
        close = 100.0 + i
        bars.append(FeatureBar(high=close + 1.0, low=close - 1.0, close=close, volume=1000.0 + i * 10))
    return bars


def test_feature_shape() -> None:
    bars = _make_bars(30)
    builder = FeatureBuilder(window=14)
    X = builder.build(bars=bars, entry_index=20)
    # 피처 벡터는 1D ndarray
    assert X.ndim == 1
    assert X.shape[0] > 0


def test_feature_window_sealed() -> None:
    """entry_index 이후 데이터가 피처에 영향을 주지 않아야 한다."""
    bars = _make_bars(30)
    builder = FeatureBuilder(window=14)
    X_original = builder.build(bars=bars, entry_index=20)

    # entry_index 이후 bar를 변조
    bars_tampered = list(bars)
    for i in range(21, 30):
        b = bars_tampered[i]
        bars_tampered[i] = FeatureBar(
            high=b.high * 10, low=b.low * 10, close=b.close * 10, volume=b.volume * 10
        )

    X_tampered = builder.build(bars=bars_tampered, entry_index=20)
    np.testing.assert_array_equal(X_original, X_tampered)


def test_requires_minimum_bars() -> None:
    bars = _make_bars(5)
    builder = FeatureBuilder(window=14)
    with pytest.raises(ValueError, match="insufficient"):
        builder.build(bars=bars, entry_index=4)


def test_no_nan_in_features() -> None:
    bars = _make_bars(30)
    builder = FeatureBuilder(window=14)
    X = builder.build(bars=bars, entry_index=20)
    assert not np.any(np.isnan(X))
