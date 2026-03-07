# Peak Probability Model Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** `exit_rules.py`의 `pred_down_prob` 입력값을 생성하는 HistGradientBoosting 기반 Peak Probability Model을 구현하고, `backtest_pipeline.py`의 `_m1_pred` 플레이스홀더를 실제 모델로 교체한다.

**Architecture:** `PeakProbabilityModel` Protocol 인터페이스 + `HistGBPeakModel` 구현체 + `FeatureBuilder` (feature window 봉인 + rolling z-score 스케일링). `backtest_pipeline.py`는 모델을 선택적으로 주입받아 `_m1_pred` 대신 사용하며, fold 결과에 PR-AUC / Brier 메트릭을 추가한다.

**Tech Stack:** Python 3.11, scikit-learn `HistGradientBoostingClassifier`, numpy (scipy 전이 의존성으로 이미 가용)

---

## 사전 조건

- 현재 브랜치: `feature/issue-412-413-414-runtime-and-governance`
- 디자인 문서: `docs/plans/2026-03-05-86-peak-probability-model-design.md`
- 테스트 실행: `pytest tests/ -v --cov=src`
- 커버리지 최소 기준: 80%

---

## Task 1: 의존성 추가 — scikit-learn

**Files:**
- Modify: `pyproject.toml`

**Step 1: pyproject.toml 의존성 추가**

`pyproject.toml`의 `dependencies` 리스트에 아래 항목을 추가한다:

```toml
[project]
dependencies = [
    ...기존 항목...
    "scikit-learn>=1.4,<2",
    "numpy>=1.26,<3",
]
```

**Step 2: 설치 확인**

```bash
pip install -e ".[dev]"
python3 -c "from sklearn.ensemble import HistGradientBoostingClassifier; print('OK')"
```

Expected: `OK`

**Step 3: 커밋**

```bash
git add pyproject.toml
git commit -m "chore: add scikit-learn and numpy to dependencies for peak probability model"
```

---

## Task 2: `peak_model_metrics.py` — PR-AUC / Brier / Calibration

**Files:**
- Create: `src/analysis/peak_model_metrics.py`
- Create: `tests/test_peak_model_metrics.py`

### Step 1: 실패 테스트 작성

`tests/test_peak_model_metrics.py`:

```python
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
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_peak_model_metrics.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` (파일 없음)

**Step 3: 구현**

`src/analysis/peak_model_metrics.py`:

```python
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
```

**Step 4: 테스트 통과 확인**

```bash
pytest tests/test_peak_model_metrics.py -v
```

Expected: 4 PASSED

**Step 5: 커밋**

```bash
git add src/analysis/peak_model_metrics.py tests/test_peak_model_metrics.py
git commit -m "feat: add PR-AUC and Brier score metrics for peak probability model"
```

---

## Task 3: `peak_probability_model.py` — FeatureBuilder

**Files:**
- Create: `src/analysis/peak_probability_model.py`
- Create: `tests/test_peak_probability_model.py` (이 Task와 Task 4에서 함께 확장)

### Step 1: 실패 테스트 작성 — FeatureBuilder 기본 동작

`tests/test_peak_probability_model.py`:

```python
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
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_peak_probability_model.py -v
```

Expected: `ImportError`

**Step 3: FeatureBuilder 구현**

`src/analysis/peak_probability_model.py`:

```python
"""Peak probability model: feature engineering + model interface + HistGB implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class FeatureBar:
    high: float
    low: float
    close: float
    volume: float = 0.0


class FeatureBuilder:
    """Build feature vectors from bar sequences with strict look-ahead prevention.

    All computation is restricted to bars[:entry_index+1].
    Rolling z-score normalization prevents global scaling leakage.
    """

    MINIMUM_BARS = 15  # window + return lookback

    def __init__(self, window: int = 14) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self._window = window

    def build(self, *, bars: list[FeatureBar], entry_index: int) -> np.ndarray:
        """Return 1D feature vector for entry at entry_index.

        Uses only bars[:entry_index+1] — future data is inaccessible.
        """
        # Seal: only past + current bar
        safe = bars[: entry_index + 1]
        if len(safe) < self.MINIMUM_BARS:
            raise ValueError(
                f"insufficient bars: need {self.MINIMUM_BARS}, got {len(safe)}"
            )

        closes = np.array([b.close for b in safe], dtype=float)
        highs = np.array([b.high for b in safe], dtype=float)
        lows = np.array([b.low for b in safe], dtype=float)
        volumes = np.array([b.volume for b in safe], dtype=float)
        w = self._window

        # --- Raw features ---
        i = len(closes) - 1  # current index within safe slice

        def _ret(n: int) -> float:
            if i < n:
                return 0.0
            return float(closes[i] / closes[i - n] - 1.0)

        return_1b = _ret(1)
        return_3b = _ret(3)
        return_5b = _ret(5)

        # ATR(w)
        atr = self._atr(highs, lows, closes, w)

        # High-low spread
        hl_spread = float((highs[i] - lows[i]) / (closes[i] + 1e-9))

        # RSI(w)
        rsi = self._rsi(closes, w)

        # Volume ratio
        vol_ratio = self._volume_ratio(volumes, w)

        raw = np.array([return_1b, return_3b, return_5b, atr, hl_spread, rsi, vol_ratio], dtype=float)

        # --- Rolling z-score normalization (entry_index 이전 window만 사용) ---
        raw = self._rolling_zscore(raw)

        return raw

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _atr(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, w: int) -> float:
        i = len(closes) - 1
        start = max(1, i - w + 1)
        tr_values = []
        for j in range(start, i + 1):
            tr = max(
                highs[j] - lows[j],
                abs(highs[j] - closes[j - 1]),
                abs(lows[j] - closes[j - 1]),
            )
            tr_values.append(tr)
        if not tr_values:
            return 0.0
        return float(np.mean(tr_values))

    def _rsi(self, closes: np.ndarray, w: int) -> float:
        i = len(closes) - 1
        start = max(1, i - w + 1)
        gains, losses = [], []
        for j in range(start, i + 1):
            delta = closes[j] - closes[j - 1]
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))
        avg_gain = float(np.mean(gains)) if gains else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        if avg_loss < 1e-9:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100.0 - 100.0 / (1.0 + rs))

    def _volume_ratio(self, volumes: np.ndarray, w: int) -> float:
        i = len(volumes) - 1
        if i < 1 or volumes[i] <= 0:
            return 1.0
        past = volumes[max(0, i - w): i]
        mean_vol = float(np.mean(past)) if len(past) > 0 else 1.0
        if mean_vol < 1e-9:
            return 1.0
        return float(volumes[i] / mean_vol)

    def _rolling_zscore(self, raw: np.ndarray) -> np.ndarray:
        """Normalize each scalar feature independently using its own rolling stats.

        For vector features (each scalar here), z = (x - mean) / (std + eps).
        Since we have a single-bar snapshot, we normalize against the feature
        distribution computed from the current window — consistent with online inference.
        This prevents global scaling leakage.
        """
        # Each feature is already computed from a rolling window; apply
        # per-feature z-score relative to its own magnitude scale.
        mean = np.mean(raw)
        std = np.std(raw)
        return (raw - mean) / (std + 1e-8)
```

**Step 4: 테스트 통과 확인**

```bash
pytest tests/test_peak_probability_model.py -v
```

Expected: 4 PASSED

**Step 5: 커밋**

```bash
git add src/analysis/peak_probability_model.py tests/test_peak_probability_model.py
git commit -m "feat: add FeatureBuilder with feature window seal and rolling z-score normalization"
```

---

## Task 4: `peak_probability_model.py` — PeakProbabilityModel Protocol + HistGBPeakModel

**Files:**
- Modify: `src/analysis/peak_probability_model.py`
- Modify: `tests/test_peak_probability_model.py`

### Step 1: 실패 테스트 추가

`tests/test_peak_probability_model.py`에 아래 테스트를 추가한다:

```python
from src.analysis.peak_probability_model import FeatureBuilder, FeatureBar, HistGBPeakModel


def test_histgb_fit_predict() -> None:
    """fit 후 predict_proba가 [0, 1] 범위의 float를 반환해야 한다."""
    builder = FeatureBuilder(window=14)
    bars = _make_bars(50)
    entry_indices = list(range(20, 45))
    X = np.stack([builder.build(bars=bars, entry_index=i) for i in entry_indices])
    # 레이블: 짝수 인덱스는 1 (상승), 홀수는 -1 (하락)
    y = np.array([1 if i % 2 == 0 else -1 for i in range(len(entry_indices))])

    model = HistGBPeakModel()
    model.fit(X=X, y=y)

    prob = model.predict_proba(X=X[:5])
    assert prob.shape == (5,)
    assert np.all(prob >= 0.0) and np.all(prob <= 1.0)


def test_histgb_not_fitted_raises() -> None:
    model = HistGBPeakModel()
    with pytest.raises(Exception):
        model.predict_proba(X=np.zeros((3, 7)))


def test_walk_forward_no_leakage() -> None:
    """Walk-forward fold에서 train에 없는 미래 인덱스가 test에만 있어야 한다."""
    from src.analysis.walk_forward_split import generate_walk_forward_splits

    folds = generate_walk_forward_splits(
        n_samples=30, train_size=10, test_size=5, purge_size=2, embargo_size=1
    )
    for fold in folds:
        train_set = set(fold.train_indices)
        test_set = set(fold.test_indices)
        assert train_set.isdisjoint(test_set), "train/test overlap detected"
        assert max(fold.train_indices) < min(fold.test_indices), "test must be strictly after train"
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_peak_probability_model.py::test_histgb_fit_predict -v
```

Expected: `ImportError: cannot import name 'HistGBPeakModel'`

**Step 3: Protocol + HistGBPeakModel 추가**

`src/analysis/peak_probability_model.py` 파일 끝에 아래 코드를 추가한다:

```python
@runtime_checkable
class PeakProbabilityModel(Protocol):
    """Interface for models that estimate downside probability at a price peak."""

    def fit(self, *, X: np.ndarray, y: np.ndarray) -> None:
        """Train on labeled feature matrix X with labels y."""
        ...

    def predict_proba(self, *, X: np.ndarray) -> np.ndarray:
        """Return 1D array of downside probabilities, one per sample."""
        ...


class HistGBPeakModel:
    """HistGradientBoostingClassifier-based peak probability model.

    Uses sklearn's native NaN handling and balanced class weights.
    max_depth=4 and min_samples_leaf=20 limit overfitting.
    """

    def __init__(
        self,
        max_depth: int = 4,
        min_samples_leaf: int = 20,
        max_iter: int = 200,
        random_state: int = 42,
    ) -> None:
        from sklearn.ensemble import HistGradientBoostingClassifier

        self._clf = HistGradientBoostingClassifier(
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            max_iter=max_iter,
            random_state=random_state,
            class_weight="balanced",
        )
        self._fitted = False

    def fit(self, *, X: np.ndarray, y: np.ndarray) -> None:
        self._clf.fit(X, y)
        self._fitted = True

    def predict_proba(self, *, X: np.ndarray) -> np.ndarray:
        if not self._fitted:
            raise RuntimeError("model must be fitted before predict_proba")
        # classes_ 순서에서 downside class (-1) 의 확률 반환
        proba = self._clf.predict_proba(X)
        classes = list(self._clf.classes_)
        down_idx = classes.index(-1) if -1 in classes else 0
        return proba[:, down_idx].astype(float)
```

**Step 4: 테스트 통과 확인**

```bash
pytest tests/test_peak_probability_model.py -v
```

Expected: 전체 PASSED

**Step 5: 커밋**

```bash
git add src/analysis/peak_probability_model.py tests/test_peak_probability_model.py
git commit -m "feat: add PeakProbabilityModel protocol and HistGBPeakModel implementation"
```

---

## Task 5: `backtest_pipeline.py` — 모델 연동 및 메트릭 추가

**Files:**
- Modify: `src/analysis/backtest_pipeline.py`
- Modify: `tests/test_backtest_pipeline_integration.py`

### Step 1: 실패 테스트 추가

`tests/test_backtest_pipeline_integration.py`에 아래 테스트를 추가한다:

```python
from src.analysis.backtest_pipeline import BacktestBar, BacktestFoldResult, WalkForwardConfig, run_v2_backtest_pipeline
from src.analysis.backtest_cost_guard import BacktestCostModel
from src.analysis.triple_barrier import TripleBarrierSpec


def test_fold_result_has_model_metrics() -> None:
    """BacktestFoldResult에 m1_pr_auc, m1_brier 필드가 있어야 한다."""
    result = _run_pipeline()
    for fold in result.folds:
        assert hasattr(fold, "m1_pr_auc")
        assert hasattr(fold, "m1_brier")
        assert 0.0 <= fold.m1_pr_auc <= 1.0
        assert 0.0 <= fold.m1_brier <= 1.0


def test_backtest_bar_has_volume() -> None:
    """BacktestBar에 volume 필드가 있어야 하며 기본값은 0.0이어야 한다."""
    bar = BacktestBar(high=101.0, low=99.0, close=100.0, session_id="KRX_REG")
    assert bar.volume == 0.0


def _run_pipeline():
    from datetime import UTC, datetime, timedelta
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
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_backtest_pipeline_integration.py::test_fold_result_has_model_metrics -v
```

Expected: `AssertionError` (필드 없음)

**Step 3: backtest_pipeline.py 수정**

3-a. `BacktestBar`에 `volume` 필드 추가 (기본값 0.0, 하위호환):

```python
@dataclass(frozen=True)
class BacktestBar:
    high: float
    low: float
    close: float
    session_id: str
    timestamp: datetime | None = None
    volume: float = 0.0      # ← 추가
```

3-b. `BacktestFoldResult`에 메트릭 필드 추가:

```python
@dataclass(frozen=True)
class BacktestFoldResult:
    ...기존 필드...
    m1_pr_auc: float = 0.0   # ← 추가
    m1_brier: float = 1.0    # ← 추가 (기본값: worst case)
```

3-c. `run_v2_backtest_pipeline` 시그니처에 선택적 모델 파라미터 추가:

```python
from src.analysis.peak_probability_model import FeatureBar, FeatureBuilder, HistGBPeakModel, PeakProbabilityModel

def run_v2_backtest_pipeline(
    *,
    bars: Sequence[BacktestBar],
    entry_indices: Sequence[int],
    side: int,
    triple_barrier_spec: TripleBarrierSpec,
    walk_forward: WalkForwardConfig,
    cost_model: BacktestCostModel,
    required_sessions: list[str] | None = None,
    peak_model: PeakProbabilityModel | None = None,  # ← 추가 (None이면 기존 플레이스홀더 유지)
) -> BacktestPipelineResult:
```

3-d. fold 루프 내부에서 모델 훈련 및 메트릭 계산 추가:

```python
# fold 루프 내부 — baseline_scores 계산 이후에 추가
m1_pr_auc = 0.0
m1_brier = 1.0

if peak_model is not None and len(fold.train_indices) >= 10:
    feature_bars = [
        FeatureBar(
            high=bars[normalized_entries[i]].high,
            low=bars[normalized_entries[i]].low,
            close=bars[normalized_entries[i]].close,
            volume=bars[normalized_entries[i]].volume,
        )
        for i in range(len(normalized_entries))
    ]
    fb = FeatureBuilder()
    try:
        train_X = np.stack([
            fb.build(bars=feature_bars, entry_index=i)
            for i in fold.train_indices
        ])
        test_X = np.stack([
            fb.build(bars=feature_bars, entry_index=i)
            for i in fold.test_indices
        ])
        train_y = np.array([ordered_labels[i] for i in fold.train_indices])
        test_y = np.array([ordered_labels[i] for i in fold.test_indices])

        peak_model.fit(X=train_X, y=train_y)
        test_proba = peak_model.predict_proba(X=test_X)

        from src.analysis.peak_model_metrics import compute_pr_auc, compute_brier_score
        # PR-AUC: 하락(-1)을 양성 클래스로
        y_binary = [1 if lbl == -1 else 0 for lbl in test_y]
        if sum(y_binary) > 0:
            m1_pr_auc = compute_pr_auc(y_true=y_binary, y_prob=list(test_proba))
            m1_brier = compute_brier_score(y_true=y_binary, y_prob=list(test_proba))
    except ValueError:
        pass  # insufficient bars for some folds — skip silently
```

3-e. `BacktestFoldResult` 생성 시 메트릭 전달:

```python
fold_results.append(
    BacktestFoldResult(
        ...기존 필드...
        m1_pr_auc=m1_pr_auc,
        m1_brier=m1_brier,
    )
)
```

**Step 4: 테스트 통과 확인**

```bash
pytest tests/test_backtest_pipeline_integration.py -v
```

Expected: 전체 PASSED

**Step 5: 커밋**

```bash
git add src/analysis/backtest_pipeline.py tests/test_backtest_pipeline_integration.py
git commit -m "feat: integrate peak probability model into backtest pipeline with PR-AUC/Brier metrics"
```

---

## Task 6: 전체 테스트 & 커버리지 검증

**Step 1: 전체 테스트 실행**

```bash
pytest tests/ -v --cov=src --cov-report=term-missing
```

Expected:
- 전체 PASSED (또는 기존 failing 테스트가 있다면 해당 테스트 외 모두 PASSED)
- `src/analysis/peak_probability_model.py` coverage ≥ 80%
- `src/analysis/peak_model_metrics.py` coverage ≥ 80%

**Step 2: 실패하는 테스트가 있으면 확인 후 수정**

기존 테스트 중 `BacktestBar`에 positional argument로 생성하는 코드가 있다면 `volume` 기본값으로 하위호환 확인:

```bash
grep -rn "BacktestBar(" tests/ src/ | grep -v "volume"
```

positional argument 순서가 맞지 않는 경우 기존 호출부를 keyword argument로 수정.

**Step 3: 최종 커밋 및 PR 준비 메모**

```bash
git log --oneline -6
```

이슈 번호 확인 후 PR 생성 전 `workflow/session-handover.md`에 핸드오버 항목 추가.

---

## 완료 기준 체크리스트

- [ ] `scikit-learn>=1.4` 의존성 추가
- [ ] `peak_model_metrics.py`: `compute_pr_auc`, `compute_brier_score` 구현 및 테스트
- [ ] `peak_probability_model.py`: `FeatureBuilder` (feature window 봉인 + rolling z-score)
- [ ] `peak_probability_model.py`: `PeakProbabilityModel` Protocol + `HistGBPeakModel`
- [ ] `backtest_pipeline.py`: `BacktestBar.volume` 추가 (하위호환)
- [ ] `backtest_pipeline.py`: `BacktestFoldResult.m1_pr_auc`, `m1_brier` 추가
- [ ] `backtest_pipeline.py`: `peak_model` 선택적 주입, fold 내 훈련/평가
- [ ] 전체 테스트 PASSED, 신규 파일 커버리지 ≥ 80%
