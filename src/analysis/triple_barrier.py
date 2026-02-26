"""Triple barrier labeler utilities.

Implements first-touch labeling with upper/lower/time barriers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


TieBreakMode = Literal["stop_first", "take_first"]


@dataclass(frozen=True)
class TripleBarrierSpec:
    take_profit_pct: float
    stop_loss_pct: float
    max_holding_bars: int
    tie_break: TieBreakMode = "stop_first"


@dataclass(frozen=True)
class TripleBarrierLabel:
    label: int  # +1 take-profit first, -1 stop-loss first, 0 timeout
    touched: Literal["take_profit", "stop_loss", "time"]
    touch_bar: int
    entry_price: float
    upper_barrier: float
    lower_barrier: float


def label_with_triple_barrier(
    *,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    entry_index: int,
    side: int,
    spec: TripleBarrierSpec,
) -> TripleBarrierLabel:
    """Label one entry using triple-barrier first-touch rule.

    Args:
        highs/lows/closes: OHLC components with identical length.
        entry_index: Entry bar index in the sequences.
        side: +1 for long, -1 for short.
        spec: Barrier specification.
    """
    if side not in {1, -1}:
        raise ValueError("side must be +1 or -1")
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise ValueError("highs, lows, closes lengths must match")
    if entry_index < 0 or entry_index >= len(closes):
        raise IndexError("entry_index out of range")
    if spec.max_holding_bars <= 0:
        raise ValueError("max_holding_bars must be positive")

    entry_price = float(closes[entry_index])
    if entry_price <= 0:
        raise ValueError("entry price must be positive")

    if side == 1:
        upper = entry_price * (1.0 + spec.take_profit_pct)
        lower = entry_price * (1.0 - spec.stop_loss_pct)
    else:
        # For short side, favorable move is down.
        upper = entry_price * (1.0 + spec.stop_loss_pct)
        lower = entry_price * (1.0 - spec.take_profit_pct)

    last_index = min(len(closes) - 1, entry_index + spec.max_holding_bars)
    for idx in range(entry_index + 1, last_index + 1):
        h = float(highs[idx])
        l = float(lows[idx])

        up_touch = h >= upper
        down_touch = l <= lower
        if not up_touch and not down_touch:
            continue

        if up_touch and down_touch:
            if spec.tie_break == "stop_first":
                touched = "stop_loss" if side == 1 else "take_profit"
                label = -1 if side == 1 else 1
            else:
                touched = "take_profit" if side == 1 else "stop_loss"
                label = 1 if side == 1 else -1
        elif up_touch:
            touched = "take_profit" if side == 1 else "stop_loss"
            label = 1 if side == 1 else -1
        else:
            touched = "stop_loss" if side == 1 else "take_profit"
            label = -1 if side == 1 else 1

        return TripleBarrierLabel(
            label=label,
            touched=touched,
            touch_bar=idx,
            entry_price=entry_price,
            upper_barrier=upper,
            lower_barrier=lower,
        )

    return TripleBarrierLabel(
        label=0,
        touched="time",
        touch_bar=last_index,
        entry_price=entry_price,
        upper_barrier=upper,
        lower_barrier=lower,
    )
