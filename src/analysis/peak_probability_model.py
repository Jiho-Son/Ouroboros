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
    """

    MINIMUM_BARS = 15  # window + return lookback

    def __init__(self, window: int = 14) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self._window = window

    def build(self, *, bars: list[FeatureBar], entry_index: int) -> np.ndarray:
        """Return 1D feature vector for entry at entry_index.

        Uses only bars[:entry_index+1] — future data is inaccessible.

        Note: Returns raw (unscaled) features. Callers should apply per-fold StandardScaler
        fitted only on train data to prevent global scaling leakage.
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

